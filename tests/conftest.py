import shutil

import lamindb as ln
import pytest
from lamin_utils import logger


def pytest_sessionstart(session: pytest.Session):
    ln.setup.init(
        storage="./default_storage",
        name="lamindb-unit-tests",
    )


def pytest_sessionfinish(session: pytest.Session):
    logger.set_verbosity(1)
    ln.setup.delete("lamindb-unit-tests", force=True)
    shutil.rmtree("./default_storage")
