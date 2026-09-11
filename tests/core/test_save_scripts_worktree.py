import shutil
import subprocess
import sys
import time
from pathlib import Path

scripts_dir = Path(__file__).parent.parent.resolve() / "scripts"


def run_lamin(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["lamin", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_save_script_uses_active_worktree_relative_key():
    unique = time.time_ns()
    worktree_parent = scripts_dir / f"worktrees-{unique}"
    branch_name = f"feature-a-{unique}"
    child = worktree_parent / branch_name
    script_path = child / "pipelines" / f"worktree-script-{unique}.py"
    expected_key = f"pipelines/{script_path.name}"

    assert run_lamin("settings", "worktree", "get").stdout.strip() == "false"
    assert run_lamin("settings", "dev-dir", "get").stdout.strip() == "None"

    try:
        worktree_parent.mkdir()
        assert (
            run_lamin("settings", "dev-dir", "set", str(worktree_parent)).returncode
            == 0
        )
        assert run_lamin("settings", "worktree", "set", "true").returncode == 0
        switch_result = run_lamin("switch", "-c", branch_name, cwd=worktree_parent)
        assert switch_result.returncode == 0, (
            f"stdout: {switch_result.stdout}\nstderr: {switch_result.stderr}"
        )

        script_path.parent.mkdir(parents=True)
        script_path.write_text("print('worktree save test')\n")
        save_result = run_lamin("save", str(script_path), cwd=child)
        assert save_result.returncode == 0, (
            f"stdout: {save_result.stdout}\nstderr: {save_result.stderr}"
        )

        query_result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import lamindb as ln; "
                f"assert ln.Transform.filter(key={expected_key!r}).count() >= 1",
            ],
            cwd=child,
            capture_output=True,
            text=True,
        )
        assert query_result.returncode == 0, (
            f"stdout: {query_result.stdout}\nstderr: {query_result.stderr}"
        )
    finally:
        if child.exists():
            run_lamin(
                "delete",
                "transform",
                "--key",
                expected_key,
                "--permanent",
                cwd=child,
            )
            run_lamin("delete", "branch", "--name", branch_name, cwd=child)
            shutil.rmtree(child)
        run_lamin("settings", "worktree", "set", "false")
        run_lamin("settings", "dev-dir", "unset")
        if worktree_parent.exists():
            shutil.rmtree(worktree_parent)
