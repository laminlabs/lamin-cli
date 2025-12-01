import os
from datetime import datetime, timedelta, timezone

from lamindb_setup.core.upath import UPath


def test_branch():
    exit_status = os.system("lamin connect laminlabs/lamin-site-assets")
    assert exit_status == 0

    before_snapshot = datetime.now(timezone.utc)
    exit_status = os.system("lamin io snapshot")
    assert exit_status == 0
    after_snapshot = datetime.now(timezone.utc)

    path = UPath("s3://lamin-site-assets/.lamindb/lamin.db.gz")
    assert path.exists()

    stat = path.stat()
    file_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    assert (
        before_snapshot - timedelta(seconds=15)
        <= file_mtime
        <= after_snapshot + timedelta(seconds=15)
    )
