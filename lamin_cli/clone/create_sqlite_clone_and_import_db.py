import argparse
from pathlib import Path

import lamindb_setup as ln_setup

parser = argparse.ArgumentParser()
parser.add_argument("--instance-name", required=True)
parser.add_argument("--export-dir", required=True)
parser.add_argument("--modules", required=True)
args = parser.parse_args()

instance_name = args.instance_name
export_dir = args.export_dir
modules_without_lamindb = {m for m in args.modules.split(",") if m}
modules_complete = modules_without_lamindb.copy()
modules_complete.add("lamindb")

ln_setup.init(
    storage=f"{instance_name}-clone", modules=f"{','.join(modules_without_lamindb)}"
)

import lamindb

ln_setup.io.import_db(
    module_names=list(modules_complete), input_dir=export_dir, if_exists="replace"
)

from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("PRAGMA wal_checkpoint(FULL)")

from django.db import connections

connections.close_all()
