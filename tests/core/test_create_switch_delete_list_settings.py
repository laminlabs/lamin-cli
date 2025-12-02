import os
import subprocess
from pathlib import Path

import lamindb as ln
import lamindb_setup as ln_setup


def test_create_project():
    exit_status = os.system("lamin create project --name testproject")
    assert exit_status == 0


def test_branch():
    exit_status = os.system("lamin switch --branch archive")
    assert exit_status == 0
    result = subprocess.run(
        "lamin settings get branch",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.stdout.strip().split("\n")[-1] == "archive"
    exit_status = os.system("lamin switch --branch main")
    assert exit_status == 0
    result = subprocess.run(
        "lamin settings get branch",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.stdout.strip().split("\n")[-1] == "main"
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


def test_dev_dir():
    # default dev-dir is None
    result = subprocess.run(
        "lamin settings get dev-dir",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.stdout.strip().split("\n")[-1] == "None"
    assert ln_setup.settings.dev_dir is None
    # set dev-dir to tmp_path
    this_path = Path(__file__).resolve()
    exit_status = os.system(f"lamin settings set dev-dir {this_path.parent}")
    assert exit_status == 0
    result = subprocess.run(
        "lamin settings get dev-dir",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.stdout.strip().split("\n")[-1] == str(this_path.parent)
    assert ln_setup.settings.dev_dir == this_path.parent
    # unset dev-dir
    exit_status = os.system("lamin settings set dev-dir none")
    assert exit_status == 0
    result = subprocess.run(
        "lamin settings get dev-dir",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.stdout.strip().split("\n")[-1] == "None"
    assert ln_setup.settings.dev_dir is None
