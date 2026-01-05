import argparse
import json
import sys
from pathlib import Path

import lamindb_setup as ln_setup

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))

    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-name", required=True)
    parser.add_argument("--export-dir", required=True)
    parser.add_argument("--modules", required=True)
    parser.add_argument("--original-counts", required=True)
    args = parser.parse_args()

    instance_name = args.instance_name
    export_dir = args.export_dir
    modules_without_lamindb = {m for m in args.modules.split(",") if m}
    modules_complete = modules_without_lamindb.copy()
    modules_complete.add("lamindb")

    ln_setup.init(
        storage=f"{instance_name}-clone", modules=f"{','.join(modules_without_lamindb)}"
    )

    ln_setup.io.import_db(
        module_names=list(modules_complete), input_dir=export_dir, if_exists="replace"
    )

    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("PRAGMA wal_checkpoint(FULL)")

    from lamin_cli.clone._clone_verification import (
        _compare_record_counts,
        _count_instance_records,
    )

    clone_counts = _count_instance_records()
    original_counts = json.loads(args.original_counts)
    mismatches = _compare_record_counts(original_counts, clone_counts)
    if mismatches:
        print(json.dumps(mismatches), file=sys.stderr)
        sys.exit(1)

    from django.db import connections

    connections.close_all()
