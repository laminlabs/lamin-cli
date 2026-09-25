import shutil
from pathlib import Path

import lamindb as ln
import pytest
from lamin_utils import logger


def pytest_sessionstart(session: pytest.Session):
    # Ensure clean state if previous run didn't finish (sessionfinish didn't run)
    storage_path = Path("./default_storage_cli_agents")
    if storage_path.exists():
        shutil.rmtree(storage_path)
    ln.setup.init(
        storage="./default_storage_cli_agents",
        name="lamin-cli-agents-tests",
    )


def pytest_sessionfinish(session: pytest.Session):
    logger.set_verbosity(1)
    shutil.rmtree("./default_storage_cli_agents")
    ln.setup.delete("lamin-cli-agents-tests", force=True)
