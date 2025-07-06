import os

import lamindb_setup as ln_setup


def test_branch():
    exit_status = os.system("lamin switch --branch archive")
    assert exit_status == 0
    assert ln_setup.settings.branch.uid == 12 * "a"
    exit_status = os.system("lamin switch --branch main")
    assert exit_status == 0
    assert ln_setup.settings.branch.uid == 12 * "m"


def test_space():
    exit_status = os.system("lamin switch --space non_existent")
    assert exit_status == 256
    exit_status = os.system("lamin switch --space all")
    assert exit_status == 0
    assert ln_setup.settings.space.uid == 12 * "a"
