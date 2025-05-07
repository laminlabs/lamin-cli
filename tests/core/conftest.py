import shutil

import lamindb as ln
import pytest
from lamin_utils import logger


def pytest_sessionstart(session: pytest.Session):
    ln.setup.init(
        storage="./default_storage_cli",
        name="lamin-cli-unit-tests",
    )
    ln.setup.settings.auto_connect = True


def pytest_sessionfinish(session: pytest.Session):
    logger.set_verbosity(1)
    shutil.rmtree("./default_storage_cli")
    ln.setup.delete("lamin-cli-unit-tests", force=True)
