from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import lamindb_setup as ln_setup
from lamindb_setup.core._settings_store import (
    current_instance_settings_file,
    local_current_instance_file,
)

if TYPE_CHECKING:
    from pathlib import Path


def _run(command: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
    )


def test_connect_here_sets_local_marker_and_dev_dir(tmp_path: Path):
    global_before = current_instance_settings_file().read_text()
    instance_slug = ln_setup.settings.instance.slug
    owner = ln_setup.settings.instance.owner
    name = ln_setup.settings.instance.name
    project_dir = tmp_path / "project-a"
    project_dir.mkdir()
    marker_path = local_current_instance_file(project_dir)
    dev_dir_path = ln_setup.settings.settings_dir / f"dev-dir--{owner}--{name}.txt"

    result = _run(f"lamin connect {instance_slug} --here", cwd=project_dir)
    assert result.returncode == 0, result.stderr
    assert marker_path.exists()
    assert marker_path.read_text().strip() == instance_slug
    assert current_instance_settings_file().read_text() == global_before
    assert dev_dir_path.exists()
    assert dev_dir_path.read_text().strip() == project_dir.resolve().as_posix()

    nested = project_dir / "subdir"
    nested.mkdir()
    result = _run("lamin disconnect --here", cwd=nested)
    assert result.returncode == 0, result.stderr
    assert not marker_path.exists()
    assert not dev_dir_path.exists()
    assert current_instance_settings_file().read_text() == global_before


def test_connect_here_updates_dev_dir_when_repeated(tmp_path: Path):
    instance_slug = ln_setup.settings.instance.slug
    owner = ln_setup.settings.instance.owner
    name = ln_setup.settings.instance.name
    first_dir = tmp_path / "project-first"
    second_dir = tmp_path / "project-second"
    first_dir.mkdir()
    second_dir.mkdir()
    dev_dir_path = ln_setup.settings.settings_dir / f"dev-dir--{owner}--{name}.txt"

    first = _run(f"lamin connect {instance_slug} --here", cwd=first_dir)
    assert first.returncode == 0, first.stderr
    assert dev_dir_path.exists()
    assert dev_dir_path.read_text().strip() == first_dir.resolve().as_posix()

    second = _run(f"lamin connect {instance_slug} --here", cwd=second_dir)
    assert second.returncode == 0, second.stderr
    assert dev_dir_path.read_text().strip() == second_dir.resolve().as_posix()

    assert _run("lamin disconnect --here", cwd=first_dir).returncode == 0
    assert _run("lamin disconnect --here", cwd=second_dir).returncode == 0
