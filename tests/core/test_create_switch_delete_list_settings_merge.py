import os
import shutil
import subprocess
import warnings
from pathlib import Path

import lamindb as ln
import lamindb_setup as ln_setup
import pytest
from click.testing import CliRunner
from lamin_cli.__main__ import main
from lamindb_setup.core._settings_store import (
    current_modules_file,
    local_current_instance_file,
    settings_dir,
)
from lamindb_setup.errors import CurrentInstanceNotConfigured, NoWriteAccess


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


def _setup_create_no_write_access(monkeypatch, message: str) -> list[str]:
    class DummyProject:
        def __init__(self, name):
            self.name = name

        def save(self):
            raise NoWriteAccess(message)

    monkeypatch.setattr("lamindb.Project", DummyProject)
    return ["create", "project", "blocked_project"]


def _setup_switch_no_write_access(monkeypatch, message: str) -> list[str]:
    def raise_no_write_access(target, **kwargs):
        raise NoWriteAccess(message)

    monkeypatch.setattr("lamindb.setup.switch", raise_no_write_access)
    return ["switch", "-c", "blocked_branch"]


def _setup_save_no_write_access(monkeypatch, message: str) -> list[str]:
    def raise_no_write_access(**kwargs):
        raise NoWriteAccess(message)

    monkeypatch.setattr("lamin_cli.__main__.save_", raise_no_write_access)
    return ["save", "blocked_file.txt"]


@pytest.mark.parametrize(
    "setup_case",
    [
        _setup_create_no_write_access,
        _setup_switch_no_write_access,
        _setup_save_no_write_access,
    ],
)
def test_write_commands_map_no_write_access_to_click_exception(monkeypatch, setup_case):
    message = "You're not allowed to write to the space 'all'."
    command = setup_case(monkeypatch, message)
    result = CliRunner().invoke(main, command)

    assert result.exit_code == 1
    assert message in result.output
    assert "Error" in result.output
    assert "Traceback" not in result.output


def test_list_space_maps_no_instance_to_click_exception(monkeypatch):
    monkeypatch.setattr(
        "lamindb.Space.to_dataframe",
        lambda *args, **kwargs: (_ for _ in ()).throw(CurrentInstanceNotConfigured()),
    )
    result = CliRunner().invoke(main, ["list", "space"])

    assert result.exit_code == 1
    assert "No instance is connected" in result.output
    assert "Error" in result.output
    assert "Traceback" not in result.output


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


def test_create_branch_managed_uses_hub(monkeypatch):
    calls = []

    def fake_create_branch(name, description=None):
        calls.append((name, description))
        return {"name": name}

    monkeypatch.setattr("lamin_cli.hub.create_branch", fake_create_branch)
    instance = ln_setup.settings.instance
    original_api_url = instance._api_url
    instance._api_url = "https://lamin.ai/api"
    try:
        result = CliRunner().invoke(main, ["create", "branch", "managedcreate"])
    finally:
        instance._api_url = original_api_url

    assert result.exit_code == 0
    assert calls == [("managedcreate", None)]


def test_list_branch_managed_uses_hub(monkeypatch):
    calls = {"list": []}

    def fake_list_branches(limit=100):
        calls["list"].append(limit)
        print("managed-branch-list")

    monkeypatch.setattr("lamin_cli.hub.list_branches", fake_list_branches)
    instance = ln_setup.settings.instance
    original_api_url = instance._api_url
    instance._api_url = "https://lamin.ai/api"
    try:
        result = CliRunner().invoke(main, ["list", "branch", "--limit", "5"])
    finally:
        instance._api_url = original_api_url

    assert result.exit_code == 0
    assert calls["list"] == [5]
    assert "managed-branch-list" in result.output


def test_switch_branch_managed_uses_hub(monkeypatch):
    calls = []

    def fake_switch_branch(target, create=False):
        calls.append((target, create))

    def should_not_be_called(*args, **kwargs):
        raise AssertionError(
            "lamindb.setup.switch should not be called for managed branch switch"
        )

    monkeypatch.setattr("lamin_cli.hub.switch_branch", fake_switch_branch)
    monkeypatch.setattr("lamindb.setup.switch", should_not_be_called)
    instance = ln_setup.settings.instance
    original_api_url = instance._api_url
    instance._api_url = "https://lamin.ai/api"
    try:
        result = CliRunner().invoke(main, ["switch", "managedbranch"])
    finally:
        instance._api_url = original_api_url

    assert result.exit_code == 0
    assert calls == [("managedbranch", False)]


def test_hub_switch_branch_writes_branch_file(monkeypatch):
    from lamin_cli.hub.switch import switch_branch

    instance = ln_setup.settings.instance
    branch_file = (
        settings_dir / f"current-branch--{instance.owner}--{instance.name}.txt"
    )
    original_contents = branch_file.read_text() if branch_file.exists() else None

    def fake_request_json(method, path, *, params=None, body=None):
        return {"uid": "z9x8c7v6b5n4m3k2", "name": "managedbranch"}

    monkeypatch.setattr("lamin_cli.hub.switch.request_json", fake_request_json)
    try:
        switch_branch("managedbranch")
        assert branch_file.read_text() == "z9x8c7v6b5n4m3k2\nmanagedbranch"
    finally:
        if original_contents is None:
            branch_file.unlink(missing_ok=True)
        else:
            branch_file.write_text(original_contents)


def test_hub_switch_branch_create_existing_raises(monkeypatch):
    from lamin_cli.hub._click import click as hub_click
    from lamin_cli.hub.switch import switch_branch

    def fake_create_branch(name, description=None):
        raise hub_click.ClickException(
            f"Branch '{name}' already exists. Omit -c/--create to switch to it."
        )

    monkeypatch.setattr("lamin_cli.hub.switch.create_branch", fake_create_branch)

    with pytest.raises(hub_click.ClickException, match="already exists"):
        switch_branch("existingbranch", create=True)


def test_hub_switch_branch_missing_branch_raises(monkeypatch):
    from lamin_cli.hub._click import click as hub_click
    from lamin_cli.hub.switch import switch_branch

    def fake_request_json(method, path, *, params=None, body=None):
        if path.endswith("/missingbranch"):
            return None
        if path.endswith("/branch"):
            return []
        return None

    monkeypatch.setattr("lamin_cli.hub.switch.request_json", fake_request_json)

    with pytest.raises(hub_click.ClickException, match="please check on the hub UI"):
        switch_branch("missingbranch")


def test_merge():
    """Merge a branch into main: create branch, add record, switch to main, merge."""
    exit_status = os.system("lamin switch -c merge_test_branch")
    assert exit_status == 0
    ln_setup.settings.branch = (
        "merge_test_branch"  # refresh in-process; CLI wrote to file
    )
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


def test_switch_create_existing_branch_raises():
    """Switch with -c/--create and existing branch exits non-zero with hint to omit -c."""
    result = subprocess.run(
        "lamin switch -c main",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.returncode != 0
    err_output = result.stderr + result.stdout
    assert "already exists" in err_output.lower()
    assert "-c/--create" in err_output or "Omit" in err_output


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
    # lamin switch --branch <name> (hidden flag, same as lamin switch <name>)
    exit_status = os.system("lamin switch --branch main")
    assert exit_status == 0
    # lamin switch space <name> should switch space
    exit_status = os.system("lamin switch space all")
    assert exit_status == 0


def test_space():
    exit_status = os.system("lamin switch --space non_existent")
    assert exit_status == 256
    exit_status = os.system("lamin switch --space all")
    assert exit_status == 0
    assert ln_setup.settings.space.uid == 12 * "a"


def test_dev_dir(tmp_path: Path):
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
    # set dev-dir to temp dir
    target_dir = tmp_path / "dev-dir-target"
    target_dir.mkdir()
    marker_path = local_current_instance_file(target_dir)
    exit_status = os.system(f"lamin settings dev-dir set {target_dir}")
    assert exit_status == 0
    result = subprocess.run(
        "lamin settings dev-dir get",
        capture_output=True,
        text=True,
        shell=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip().split("\n")[-1] == str(target_dir)
    assert ln_setup.settings.dev_dir == target_dir
    assert marker_path.exists()
    assert marker_path.read_text().strip() == ln_setup.settings.instance.slug
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
    assert not marker_path.exists()


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


def test_settings_modules_set():
    path = current_modules_file()
    original = path.read_text() if path.exists() else None

    try:
        result_set = subprocess.run(
            "lamin settings modules set bionty,pertdb",
            capture_output=True,
            text=True,
            shell=True,
        )
        assert result_set.returncode == 0
        assert path.exists()
        assert path.read_text().strip() == "bionty,pertdb"

        result_set_empty = subprocess.run(
            'lamin settings modules set ""',
            capture_output=True,
            text=True,
            shell=True,
        )
        assert result_set_empty.returncode == 0
        assert path.exists()
        assert path.read_text() == ""
    finally:
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(original)
