import os

from lamindb_setup.core.upath import UPath


def test_branch():
    exit_status = os.system("lamin connect laminlabs/lamin-site-assets")
    assert exit_status == 0
    exit_status = os.system("lamin snapshot")
    assert exit_status == 0
    path = UPath("s3://lamin-site-assets/.lamindb/lamin.db.gz")
    assert path.exists()
    assert path.stat()
