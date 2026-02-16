"""Test that transform reference and reference_type are correctly set with sync_git.

Uses the same setup as lamindb core test_run_external_script: run the script
with python so sync_git_repo is set and ln.track creates the transform with reference.
"""

import subprocess
from pathlib import Path

import lamindb as ln

scripts_dir = Path(__file__).parent.parent.resolve() / "scripts"


def test_transform_reference_and_reference_type_set_with_sync_git():
    """Transform created via script with sync_git_repo has reference and reference_type set."""
    script_path = scripts_dir / "run-track-and-finish-sync-git.py"
    result = subprocess.run(
        f"python {script_path}",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert "created Transform" in result.stdout.decode()
    assert "started new Run" in result.stdout.decode()

    transform = ln.Transform.get(key="run-track-and-finish-sync-git.py")
    assert transform.reference is not None
    assert transform.reference.endswith(
        "/tests/scripts/run-track-and-finish-sync-git.py"
    )
    assert transform.reference.startswith(
        "https://github.com/laminlabs/lamin-cli/blob/"
    )
    assert transform.reference_type == "url"
