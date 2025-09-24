import os
import subprocess

import lamindb as ln
import lamindb_setup as ln_setup


def test_branch():
    exit_status = os.system("lamin switch --branch archive")
    assert exit_status == 0
    result = subprocess.run(
        "lamin settings get branch",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.stdout.strip() == "archive"
    exit_status = os.system("lamin switch --branch main")
    assert exit_status == 0
    result = subprocess.run(
        "lamin settings get branch",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.stdout.strip() == "main"
    exit_status = os.system("lamin create branch --name testbranch")
    exit_status = os.system("lamin switch --branch testbranch")
    assert exit_status == 0
    exit_status = os.system("lamin list branch")
    assert exit_status == 0
    exit_status = os.system("lamin delete branch --name testbranch")
    assert exit_status == 0
    exit_status = os.system("lamin switch --branch main")


def test_space():
    exit_status = os.system("lamin switch --space non_existent")
    assert exit_status == 256
    exit_status = os.system("lamin switch --space all")
    assert exit_status == 0
    assert ln_setup.settings.space.uid == 12 * "a"
