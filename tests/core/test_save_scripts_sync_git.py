"""Test that lamin save sets transform reference and reference_type when LAMINDB_SYNC_GIT_REPO is set."""

import os
import subprocess
from pathlib import Path

import lamindb as ln

scripts_dir = Path(__file__).parent.parent.resolve() / "scripts"


def test_lamin_save_sets_reference_with_sync_git_env():
    """lamin save with LAMINDB_SYNC_GIT_REPO sets transform.reference and reference_type."""
    env = os.environ.copy()
    env["LAMIN_TESTING"] = "true"
    env["LAMINDB_SYNC_GIT_REPO"] = "https://github.com/laminlabs/lamin-cli"

    # Use dummy script dedicated to this test (not mutated by other tests)
    script_path = scripts_dir / "dummy_sync_git_save.py"

    result = subprocess.run(
        f"lamin save {script_path}",
        shell=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr.decode()

    transform = ln.Transform.get(key="dummy_sync_git_save.py")
    assert transform.reference is not None
    assert "dummy_sync_git_save.py" in transform.reference
    assert transform.reference.startswith(
        "https://github.com/laminlabs/lamin-cli/blob/"
    )
    assert transform.reference_type == "url"

    # Clean up
    subprocess.run(
        "lamin delete transform --key dummy_sync_git_save.py --permanent",
        shell=True,
        capture_output=True,
    )
