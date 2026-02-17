import shutil
from pathlib import Path

import lamindb as ln
import pytest
from lamin_utils import logger
from lamindb_setup.core._settings_store import settings_dir


def pytest_sessionstart(session: pytest.Session):
    # Remove stale branch/space files that cause init to fail when the instance
    # was deleted and recreated (branch uids change)
    for pattern in (
        "current-branch--*--lamin-cli-unit-tests.txt",
        "current-space--*--lamin-cli-unit-tests.txt",
    ):
        for f in settings_dir.glob(pattern):
            f.unlink(missing_ok=True)
    # Ensure clean state if previous run didn't finish (sessionfinish didn't run)
    storage_path = Path("./default_storage_cli")
    if storage_path.exists():
        shutil.rmtree(storage_path)
    ln.setup.init(
        storage="./default_storage_cli",
        name="lamin-cli-unit-tests",
    )


def pytest_sessionfinish(session: pytest.Session):
    logger.set_verbosity(1)
    shutil.rmtree("./default_storage_cli")
    ln.setup.delete("lamin-cli-unit-tests", force=True)
