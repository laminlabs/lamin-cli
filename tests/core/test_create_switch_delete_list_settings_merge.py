import os
import shutil
import subprocess
import warnings
from pathlib import Path

import lamindb as ln
import lamindb_setup as ln_setup


def test_create_project():
    exit_status = os.system("lamin create project testproject")
    assert exit_status == 0


def test_create_backward_compat():
    """Backward compat: lamin create <registry> --name <name> still works (undocumented)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        exit_status = os.system("lamin create branch --name backcompatbranch")
    assert exit_status == 0
    exit_status = os.system("lamin delete branch --name backcompatbranch")
    assert exit_status == 0


def test_branch():
    exit_status = os.system("lamin switch archive")
    assert exit_status == 0
    result = subprocess.run(
        "lamin settings get branch",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.stdout.strip().split("\n")[-1] == "archive"
    exit_status = os.system("lamin switch main")
    assert exit_status == 0
    result = subprocess.run(
        "lamin settings get branch",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.stdout.strip().split("\n")[-1] == "main"
    exit_status = os.system("lamin switch -c testbranch")
    assert exit_status == 0
    exit_status = os.system("lamin list branch")
    assert exit_status == 0
    exit_status = os.system("lamin delete branch --name testbranch")
    assert exit_status == 0
    exit_status = os.system("lamin switch main")


def test_merge():
    """Merge a branch into main: create branch, add record, switch to main, merge."""
    exit_status = os.system("lamin switch -c merge_test_branch")
    assert exit_status == 0
    ulabel = ln.ULabel(name="merge_test_record").save()
    assert ulabel.branch.name == "merge_test_branch"
    exit_status = os.system("lamin switch main")
    assert exit_status == 0
    ln_setup.settings.branch = "main"  # refresh in-process; CLI wrote to file
    assert ln.ULabel.filter(name="merge_test_record").count() == 0
    exit_status = os.system("lamin merge merge_test_branch")
    assert exit_status == 0
    ulabel = ln.ULabel.get(name="merge_test_record")
    assert ulabel.branch.name == "main"
    exit_status = os.system("lamin delete branch --name merge_test_branch")
    # raises "cannot delete branch because it is linked to an artifact"
    assert exit_status == 256


def test_merge_nonexistent_branch():
    """Merge a non-existent branch exits non-zero with clear error."""
    result = subprocess.run(
        "lamin merge nonexistent_branch_xyz",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.returncode != 0
    err_output = (result.stderr + result.stdout).lower()
    assert "not found" in err_output or "nonexistent" in err_output


def test_switch_nonexistent_branch():
    """Switch to a non-existent branch (without --create) exits non-zero with clear error."""
    result = subprocess.run(
        "lamin switch nonexistent_branch_xyz",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.returncode != 0
    err_output = (result.stderr + result.stdout).lower()
    assert (
        "branch" in err_output
        or "not found" in err_output
        or "nonexistent" in err_output
    )


def test_switch_backward_compat():
    """Backward compat: lamin switch branch X and lamin switch space Y still work (deprecated)."""
    exit_status = os.system("lamin switch branch archive")
    assert exit_status == 0
    result = subprocess.run(
        "lamin settings get branch",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip().split("\n")[-1] == "archive"
    exit_status = os.system("lamin switch branch main")
    # lamin switch space <name> should switch space
    exit_status = os.system("lamin switch space all")
    assert exit_status == 0


def test_space():
    exit_status = os.system("lamin switch --space non_existent")
    assert exit_status == 256
    exit_status = os.system("lamin switch --space all")
    assert exit_status == 0
    assert ln_setup.settings.space.uid == 12 * "a"


def test_dev_dir():
    """Test lamin settings dev-dir get/set (new pattern: lamin settings dev-dir ...)."""
    # default dev-dir is None
    result = subprocess.run(
        "lamin settings dev-dir get",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip().split("\n")[-1] == "None"
    assert ln_setup.settings.dev_dir is None
    # set dev-dir to parent dir
    this_path = Path(__file__).resolve()
    exit_status = os.system(f"lamin settings dev-dir set {this_path.parent}")
    assert exit_status == 0
    result = subprocess.run(
        "lamin settings dev-dir get",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip().split("\n")[-1] == str(this_path.parent)
    assert ln_setup.settings.dev_dir == this_path.parent
    # unset dev-dir
    exit_status = os.system("lamin settings dev-dir unset")
    assert exit_status == 0
    result = subprocess.run(
        "lamin settings dev-dir get",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip().split("\n")[-1] == "None"
    assert ln_setup.settings.dev_dir is None


def test_dev_dir_legacy_get_set():
    """Legacy pattern lamin settings get/set dev-dir still works (backward compat)."""
    result = subprocess.run(
        "lamin settings get dev-dir",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip().split("\n")[-1] == "None"
    this_path = Path(__file__).resolve()
    exit_status = os.system(f"lamin settings set dev-dir {this_path.parent}")
    assert exit_status == 0
    result = subprocess.run(
        "lamin settings get dev-dir",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip().split("\n")[-1] == str(this_path.parent)
    exit_status = os.system("lamin settings set dev-dir none")
    assert exit_status == 0


def test_settings_cache_get_set_reset():
    """Test lamin settings cache-dir get and set."""
    result = subprocess.run(
        "lamin settings cache-dir get",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.returncode == 0
    line = result.stdout.strip().split("\n")[-1]
    # Output is "The cache directory is <path>"
    original_cache = line.split(" is ", 1)[-1] if " is " in line else line
    assert original_cache
    assert Path(original_cache).is_absolute() or original_cache.startswith("~")
    # Set cache to a temp dir, verify, then reset to original
    tmp_dir = Path(__file__).resolve().parent / "tmp_cache_test"
    tmp_dir.mkdir(exist_ok=True)
    try:
        result_set = subprocess.run(
            f"lamin settings cache-dir set {tmp_dir}",
            capture_output=True,
            text=True,
            shell=True,
        )
        assert result_set.returncode == 0
        result = subprocess.run(
            "lamin settings cache-dir get",
            capture_output=True,
            text=True,
            shell=True,
        )
        assert result.returncode == 0
        line = result.stdout.strip().split("\n")[-1]
        got_path = line.split(" is ", 1)[-1] if " is " in line else line
        assert got_path == str(tmp_dir)
        # Reset to original cache
        result_reset = subprocess.run(
            "lamin settings cache-dir reset",
            capture_output=True,
            text=True,
            shell=True,
        )
        assert result_reset.returncode == 0
        result = subprocess.run(
            "lamin settings cache-dir get",
            capture_output=True,
            text=True,
            shell=True,
        )
        assert result.returncode == 0
        line = result.stdout.strip().split("\n")[-1]
        got_path = line.split(" is ", 1)[-1] if " is " in line else line
        assert got_path == original_cache

    finally:
        subprocess.run(
            f"lamin settings cache-dir set {original_cache}",
            capture_output=True,
            shell=True,
        )
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
