import os

import nox
from laminci.nox import install_lamindb

IS_PR = os.getenv("GITHUB_EVENT_NAME") != "push"
nox.options.default_venv_backend = "none"


@nox.session
def setup(session):
    branch = "main" if IS_PR else "release"
    install_lamindb(session, branch=branch)
