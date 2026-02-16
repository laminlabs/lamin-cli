import shutil
import subprocess
from pathlib import Path

import lamindb as ln

PLAN_HEADER = """---
name: LaminProfiler duration plot notebook
overview: Create a LaminDB-tracked Jupyter notebook under dev-dir/laminprofiler/ that connects to laminlabs/lamindata, loads the record iJEIHhE5OVrz7qmP (or the task it belongs to), and plots the duration_in_sec feature vs. time.
---

# Plan body (header stripped for description only)
Some markdown content here.
"""


def test_save_plan_file_auto_key_kind_description():
    """lamin save <path> on .plan.md auto-generates key=.plans/<name>.plan.md, kind=plan, description from header."""
    plans_dir = Path(__file__).parent / "plans"
    plans_dir.mkdir(exist_ok=True)
    plan_path = plans_dir / "migrate_instances_cli_module_41ecb2cc.plan.md"
    plan_path.write_text(PLAN_HEADER)

    try:
        result = subprocess.run(
            f"lamin save {plan_path}",
            shell=True,
            capture_output=True,
        )
        out, err = result.stdout.decode(), result.stderr.decode()
        assert result.returncode == 0, f"stdout: {out}\nstderr: {err}"
        assert "saving artifact as" in out or "saving artifact as" in err
        assert "kind=" in out or "kind=" in err
        assert "saved:" in out
        assert "storage path:" in out

        artifact = ln.Artifact.get(
            key=".plans/migrate_instances_cli_module_41ecb2cc.plan.md"
        )
        assert artifact.key == ".plans/migrate_instances_cli_module_41ecb2cc.plan.md"
        assert artifact.kind == "plan"
        expected_description = (
            "LaminProfiler duration plot notebook. Create a LaminDB-tracked Jupyter notebook "
            "under dev-dir/laminprofiler/ that connects to laminlabs/lamindata, loads the record "
            "iJEIHhE5OVrz7qmP (or the task it belongs to), and plots the duration_in_sec feature vs. time."
        )
        assert artifact.description == expected_description

        # Stored artifact body has front matter stripped
        path = artifact.cache()
        body = path.read_text()
        assert "---" not in body or body.strip().startswith("#")
        assert "name: LaminProfiler" not in body
        assert "overview: Create" not in body
        assert "# Plan body" in body
        assert "Some markdown content here" in body
    finally:
        plan_path.unlink(missing_ok=True)
        if plans_dir.exists() and not any(plans_dir.iterdir()):
            plans_dir.rmdir()
        result = subprocess.run(
            "lamin delete artifact --key .plans/migrate_instances_cli_module_41ecb2cc.plan.md --permanent",
            shell=True,
            capture_output=True,
        )
        assert result.returncode == 0


def test_save_plan_claude_plans_dir():
    """Files under .claude/plans/ are detected as plans (Claude Code) even without .plan.md suffix."""
    claude_plans = Path(__file__).parent / ".claude" / "plans"
    claude_plans.mkdir(parents=True, exist_ok=True)
    plan_path = claude_plans / "my_plan.md"
    plan_path.write_text(PLAN_HEADER)

    try:
        result = subprocess.run(
            f"lamin save {plan_path}",
            shell=True,
            capture_output=True,
        )
        out, err = result.stdout.decode(), result.stderr.decode()
        assert result.returncode == 0, f"stdout: {out}\nstderr: {err}"
        assert 'saving artifact as `kind="plan"`' in out
        artifact = ln.Artifact.get(key=".plans/my_plan.md")
        assert artifact.kind == "plan"
        assert "LaminProfiler" in (artifact.description or "")
    finally:
        plan_path.unlink(missing_ok=True)
        if (claude_plans.parent / ".claude").exists():
            shutil.rmtree(claude_plans.parent / ".claude")
        subprocess.run(
            "lamin delete artifact --key .plans/my_plan.md --permanent",
            shell=True,
            capture_output=True,
        )


def test_save_plan_kind_override():
    """--kind overrides auto-inferred plan kind."""
    plans_dir = Path(__file__).parent / "plans"
    plans_dir.mkdir(exist_ok=True)
    plan_path = plans_dir / "override_kind.plan.md"
    plan_path.write_text(PLAN_HEADER)

    try:
        result = subprocess.run(
            f"lamin save {plan_path} --kind dataset",
            shell=True,
            capture_output=True,
        )
        assert result.returncode == 0
        artifact = ln.Artifact.get(key=".plans/override_kind.plan.md")
        assert artifact.kind == "dataset"
    finally:
        plan_path.unlink(missing_ok=True)
        if plans_dir.exists() and not any(plans_dir.iterdir()):
            plans_dir.rmdir()
        subprocess.run(
            "lamin delete artifact --key .plans/override_kind.plan.md --permanent",
            shell=True,
            capture_output=True,
        )
