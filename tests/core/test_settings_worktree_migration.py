from pathlib import Path

import click
import lamindb_setup
import pytest
from click.testing import CliRunner
from lamin_cli._settings import _disable_worktree, _enable_worktree, settings
from lamindb_setup.core._settings_store import local_current_branch_file


class FakeSettings:
    def __init__(self, dev_dir: Path | None, branch: str = "main"):
        self.dev_dir = dev_dir
        self.worktree = False
        self.branch = branch

    def _read_branch_idlike_name(self):
        return "branchuid123", self.branch


def test_enable_worktree_requires_dev_dir():
    with pytest.raises(click.ClickException, match="configured dev-dir"):
        _enable_worktree(FakeSettings(None))


def test_enable_worktree_empty_dev_dir(tmp_path: Path):
    settings = FakeSettings(tmp_path)

    _enable_worktree(settings)

    assert settings.worktree is True
    assert list(tmp_path.iterdir()) == []


def test_enable_worktree_ignores_root_config_directories(tmp_path: Path):
    settings = FakeSettings(tmp_path)
    for directory in (".agents", ".claude", ".lamin", ".vscode"):
        config_file = tmp_path / directory / "config.txt"
        config_file.parent.mkdir()
        config_file.write_text(directory)

    _enable_worktree(settings)

    assert settings.worktree is True
    assert not (tmp_path / "main").exists()
    for directory in (".agents", ".claude", ".lamin", ".vscode"):
        assert (tmp_path / directory / "config.txt").read_text() == directory


def test_enable_worktree_is_idempotent(tmp_path: Path):
    settings = FakeSettings(tmp_path)
    settings.worktree = True
    source = tmp_path / "analysis.py"
    source.write_text("print('hello')\n")

    _enable_worktree(settings)

    assert source.exists()


def test_enable_worktree_migrates_existing_dev_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    settings = FakeSettings(tmp_path)
    root_lamin = tmp_path / ".lamin"
    root_lamin.mkdir()
    (root_lamin / "current_instance").write_text("account/instance")
    (tmp_path / "analysis.py").write_text("print('hello')\n")
    nested = tmp_path / "data"
    nested.mkdir()
    (nested / "result.csv").write_text("value\n1\n")
    hidden = tmp_path / ".gitignore"
    hidden.write_text(".cache\n")
    root_config_files = {
        ".agents": "skill instructions",
        ".claude": "claude settings",
        ".vscode": "editor settings",
    }
    for directory, content in root_config_files.items():
        config_file = tmp_path / directory / "config.txt"
        config_file.parent.mkdir()
        config_file.write_text(content)
    monkeypatch.setattr("lamin_cli._settings.click.confirm", lambda *a, **k: True)

    _enable_worktree(settings)

    main = tmp_path / "main"
    assert settings.worktree is True
    assert (main / "analysis.py").read_text() == "print('hello')\n"
    assert (main / "data" / "result.csv").read_text() == "value\n1\n"
    assert (main / ".gitignore").read_text() == ".cache\n"
    assert (root_lamin / "current_instance").read_text() == "account/instance"
    for directory, content in root_config_files.items():
        assert (tmp_path / directory / "config.txt").read_text() == content
        assert not (main / directory).exists()
    assert local_current_branch_file(main).read_text() == "branchuid123\nmain"
    assert not (tmp_path / "analysis.py").exists()


def test_enable_worktree_preserves_non_main_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    settings = FakeSettings(tmp_path, branch="analysis")
    (tmp_path / "analysis.py").write_text("print('hello')\n")
    monkeypatch.setattr("lamin_cli._settings.click.confirm", lambda *a, **k: True)

    _enable_worktree(settings)

    assert (tmp_path / "analysis" / "analysis.py").exists()
    assert (
        local_current_branch_file(tmp_path / "analysis").read_text()
        == "branchuid123\nanalysis"
    )


def test_enable_worktree_cancel_leaves_dev_dir_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    settings = FakeSettings(tmp_path)
    source = tmp_path / "analysis.py"
    source.write_text("print('hello')\n")
    monkeypatch.setattr("lamin_cli._settings.click.confirm", lambda *a, **k: False)

    with pytest.raises(click.Abort):
        _enable_worktree(settings)

    assert settings.worktree is False
    assert source.exists()
    assert not (tmp_path / "main").exists()


def test_enable_worktree_target_collision_is_safe(tmp_path: Path):
    settings = FakeSettings(tmp_path)
    source = tmp_path / "analysis.py"
    source.write_text("print('hello')\n")
    (tmp_path / "main").mkdir()

    with pytest.raises(click.ClickException, match="already exists"):
        _enable_worktree(settings)

    assert settings.worktree is False
    assert source.exists()


def test_enable_worktree_rolls_back_failed_move(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    settings = FakeSettings(tmp_path)
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a")
    second.write_text("b")
    monkeypatch.setattr("lamin_cli._settings.click.confirm", lambda *a, **k: True)
    original_replace = Path.replace

    def fail_second_source(source: Path, target: Path):
        if source == second:
            raise OSError("simulated move failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_second_source)

    with pytest.raises(OSError, match="simulated move failure"):
        _enable_worktree(settings)

    assert settings.worktree is False
    assert first.read_text() == "a"
    assert second.read_text() == "b"
    assert not (tmp_path / "main").exists()


@pytest.mark.parametrize(
    "arguments",
    (["worktree", "set", "true"], ["set", "worktree", "true"]),
)
def test_both_worktree_command_forms_migrate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arguments: list[str]
):
    fake_settings = FakeSettings(tmp_path)
    (tmp_path / "analysis.py").write_text("print('hello')\n")
    monkeypatch.setattr(lamindb_setup, "settings", fake_settings)

    result = CliRunner().invoke(settings, arguments, input="y\n")

    assert result.exit_code == 0, result.output
    assert fake_settings.worktree is True
    assert (tmp_path / "main" / "analysis.py").exists()


@pytest.mark.parametrize(
    "arguments",
    (
        ["worktree", "set", "false"],
        ["set", "worktree", "false"],
        ["worktree", "unset"],
    ),
)
def test_all_disable_command_forms_restore_dev_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arguments: list[str]
):
    fake_settings = FakeSettings(tmp_path)
    fake_settings.worktree = True
    main = tmp_path / "main"
    local_current_branch_file(main).parent.mkdir(parents=True)
    local_current_branch_file(main).write_text("branchuid123\nmain")
    (main / "analysis.py").write_text("print('hello')\n")
    monkeypatch.setattr(lamindb_setup, "settings", fake_settings)

    result = CliRunner().invoke(settings, arguments, input="y\n")

    assert result.exit_code == 0, result.output
    assert fake_settings.worktree is False
    assert (tmp_path / "analysis.py").exists()


def test_disable_worktree_restores_manual_dev_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    settings = FakeSettings(tmp_path)
    settings.worktree = True
    main = tmp_path / "main"
    main.mkdir()
    local_current_branch_file(main).parent.mkdir()
    local_current_branch_file(main).write_text("branchuid123\nmain")
    (main / "analysis.py").write_text("print('hello')\n")
    root_config_files = {
        ".agents": "skill instructions",
        ".claude": "claude settings",
        ".vscode": "editor settings",
    }
    for directory, content in root_config_files.items():
        config_file = tmp_path / directory / "config.txt"
        config_file.parent.mkdir()
        config_file.write_text(content)
    monkeypatch.setattr("lamin_cli._settings.click.confirm", lambda *a, **k: True)

    _disable_worktree(settings)

    assert settings.worktree is False
    assert (tmp_path / "analysis.py").read_text() == "print('hello')\n"
    for directory, content in root_config_files.items():
        assert (tmp_path / directory / "config.txt").read_text() == content
    assert not main.exists()


def test_disable_worktree_rejects_multiple_branch_directories(tmp_path: Path):
    settings = FakeSettings(tmp_path)
    settings.worktree = True
    for name in ("main", "feature"):
        branch = tmp_path / name
        local_current_branch_file(branch).parent.mkdir(parents=True)
        local_current_branch_file(branch).write_text(f"uid-{name}\n{name}")

    with pytest.raises(click.ClickException, match="multiple branch directories"):
        _disable_worktree(settings)

    assert settings.worktree is True


def test_disable_worktree_rejects_collisions(tmp_path: Path):
    settings = FakeSettings(tmp_path)
    settings.worktree = True
    main = tmp_path / "main"
    local_current_branch_file(main).parent.mkdir(parents=True)
    local_current_branch_file(main).write_text("branchuid123\nmain")
    (main / "analysis.py").write_text("new")
    (tmp_path / "analysis.py").write_text("old")

    with pytest.raises(click.ClickException, match="already exist"):
        _disable_worktree(settings)

    assert settings.worktree is True
    assert (main / "analysis.py").read_text() == "new"
    assert (tmp_path / "analysis.py").read_text() == "old"


def test_disable_worktree_cancel_leaves_branch_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    settings = FakeSettings(tmp_path)
    settings.worktree = True
    main = tmp_path / "main"
    local_current_branch_file(main).parent.mkdir(parents=True)
    local_current_branch_file(main).write_text("branchuid123\nmain")
    source = main / "analysis.py"
    source.write_text("content")
    monkeypatch.setattr("lamin_cli._settings.click.confirm", lambda *a, **k: False)

    with pytest.raises(click.Abort):
        _disable_worktree(settings)

    assert settings.worktree is True
    assert source.read_text() == "content"


def test_disable_worktree_rolls_back_failed_move(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    settings = FakeSettings(tmp_path)
    settings.worktree = True
    main = tmp_path / "main"
    local_current_branch_file(main).parent.mkdir(parents=True)
    local_current_branch_file(main).write_text("branchuid123\nmain")
    first = main / "a.txt"
    second = main / "b.txt"
    first.write_text("a")
    second.write_text("b")
    monkeypatch.setattr("lamin_cli._settings.click.confirm", lambda *a, **k: True)
    original_replace = Path.replace

    def fail_second_source(source: Path, target: Path):
        if source == second:
            raise OSError("simulated move failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_second_source)

    with pytest.raises(OSError, match="simulated move failure"):
        _disable_worktree(settings)

    assert settings.worktree is True
    assert first.read_text() == "a"
    assert second.read_text() == "b"
    assert not (tmp_path / "a.txt").exists()
