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
    project_dir = tmp_path / "project-a"
    project_dir.mkdir()
    marker_path = local_current_instance_file(project_dir)

    result = _run(f"lamin connect {instance_slug} --here", cwd=project_dir)
    assert result.returncode == 0, result.stderr
    result.stdout + result.stderr
    assert marker_path.exists()
    assert marker_path.read_text().strip() == instance_slug
    assert current_instance_settings_file().read_text() == global_before

    nested = project_dir / "subdir"
    nested.mkdir()
    got = _run("lamin settings dev-dir get", cwd=nested)
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip().split("\n")[-1] == str(project_dir.resolve())

    result = _run("lamin disconnect --here", cwd=nested)
    assert result.returncode == 0, result.stderr
    assert not marker_path.exists()
    assert current_instance_settings_file().read_text() == global_before


def test_connect_here_keeps_both_dev_dirs(tmp_path: Path):
    instance_slug = ln_setup.settings.instance.slug
    first_dir = tmp_path / "project-first"
    second_dir = tmp_path / "project-second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_marker = local_current_instance_file(first_dir)
    second_marker = local_current_instance_file(second_dir)

    first = _run(f"lamin connect {instance_slug} --here", cwd=first_dir)
    assert first.returncode == 0, first.stderr
    second = _run(f"lamin connect {instance_slug} --here", cwd=second_dir)
    assert second.returncode == 0, second.stderr
    assert first_marker.read_text().strip() == instance_slug
    assert second_marker.read_text().strip() == instance_slug

    assert _run("lamin disconnect --here", cwd=first_dir).returncode == 0
    assert not first_marker.exists()
    assert second_marker.exists()
    assert _run("lamin disconnect --here", cwd=second_dir).returncode == 0
    assert not second_marker.exists()
