import shutil

import lamindb as ln
import pytest
from lamin_utils import logger


def pytest_sessionstart(session: pytest.Session):
    ln.setup.init(
        storage="./default_storage_ci",
        name="laminci-unit-tests",
    )
    ln.setup.settings.auto_connect = True


def pytest_sessionfinish(session: pytest.Session):
    logger.set_verbosity(1)
    shutil.rmtree("./default_storage_ci")
    ln.setup.delete("laminci-unit-tests", force=True)
