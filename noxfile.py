import os

import nox
from laminci.nox import install_lamindb, login_testuser1

IS_PR = os.getenv("GITHUB_EVENT_NAME") != "push"
nox.options.default_venv_backend = "none"


@nox.session
def setup(session):
    branch = "main" if IS_PR else "release"  # point back to "release"
    install_lamindb(session, branch=branch)
    login_testuser1(session)
