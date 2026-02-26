import shutil
import subprocess
import sys
from pathlib import Path

import lamindb as ln


def run_lamin(*args: str) -> subprocess.CompletedProcess:
    """Run lamin CLI from the current checkout via module entrypoint."""
    return subprocess.run(
        [sys.executable, "-m", "lamin_cli", *args],
        capture_output=True,
        text=True,
    )


OVERVIEW_TEXT = (
    "Create a LaminDB-tracked Jupyter notebook under dev-dir/laminprofiler/ that "
    "connects to laminlabs/lamindata, loads the record iJEIHhE5OVrz7qmP (or the task "
    "it belongs to), and plots the duration_in_sec feature vs. time."
)

PLAN_HEADER = f"""---
name: LaminProfiler duration plot notebook
overview: {OVERVIEW_TEXT}
---

# Plan body (header stripped for description only)
Some markdown content here.
"""

PLAN_HEADER_WITH_TODOS = """---
name: Refactor Tutorial Positioning
overview: Propose a targeted refactor of `tutorial.md` so it complements (rather than duplicates) `lamindb/README.md`, optimized for intermediate users and balanced code+concept delivery.
todos:
  - id: map-overlap
    content: Create README↔tutorial overlap map and mark keep/replace/remove sections.
    status: pending
  - id: define-new-outline
    content: Draft concept-first tutorial outline with section goals and one example each.
    status: pending
isProject: false
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
        result = run_lamin("save", str(plan_path))
        out, err = result.stdout, result.stderr
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
        assert artifact.description == OVERVIEW_TEXT

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
        result = run_lamin(
            "delete",
            "artifact",
            "--key",
            ".plans/migrate_instances_cli_module_41ecb2cc.plan.md",
            "--permanent",
        )
        assert result.returncode == 0


def test_save_plan_claude_plans_dir():
    """Files under .claude/plans/ are detected as plans (Claude Code) even without .plan.md suffix."""
    claude_plans = Path(__file__).parent / ".claude" / "plans"
    claude_plans.mkdir(parents=True, exist_ok=True)
    plan_path = claude_plans / "my_plan.md"
    plan_path.write_text(PLAN_HEADER)

    try:
        result = run_lamin("save", str(plan_path))
        out, err = result.stdout, result.stderr
        assert result.returncode == 0, f"stdout: {out}\nstderr: {err}"
        assert 'saving artifact as `kind="plan"`' in out
        artifact = ln.Artifact.get(key=".plans/my_plan.md")
        assert artifact.kind == "plan"
        assert artifact.description == OVERVIEW_TEXT
    finally:
        plan_path.unlink(missing_ok=True)
        if (claude_plans.parent / ".claude").exists():
            shutil.rmtree(claude_plans.parent / ".claude")
        run_lamin(
            "delete",
            "artifact",
            "--key",
            ".plans/my_plan.md",
            "--permanent",
        )


def test_save_plan_kind_override():
    """--kind overrides auto-inferred plan kind."""
    plans_dir = Path(__file__).parent / "plans"
    plans_dir.mkdir(exist_ok=True)
    plan_path = plans_dir / "override_kind.plan.md"
    plan_path.write_text(PLAN_HEADER)

    try:
        result = run_lamin("save", str(plan_path), "--kind", "dataset")
        assert result.returncode == 0
        artifact = ln.Artifact.get(key=".plans/override_kind.plan.md")
        assert artifact.kind == "dataset"
    finally:
        plan_path.unlink(missing_ok=True)
        if plans_dir.exists() and not any(plans_dir.iterdir()):
            plans_dir.rmdir()
        run_lamin(
            "delete",
            "artifact",
            "--key",
            ".plans/override_kind.plan.md",
            "--permanent",
        )


def test_save_plan_header_todos_do_not_leak_into_description():
    """Only frontmatter overview populates description, not todos/id/status metadata."""
    plans_dir = Path(__file__).parent / "plans"
    plans_dir.mkdir(exist_ok=True)
    plan_path = plans_dir / "header_todos.plan.md"
    plan_path.write_text(PLAN_HEADER_WITH_TODOS)
    key = ".plans/header_todos.plan.md"
    expected_description = (
        "Propose a targeted refactor of `tutorial.md` so it complements "
        "(rather than duplicates) `lamindb/README.md`, optimized for intermediate users "
        "and balanced code+concept delivery."
    )

    try:
        result = run_lamin("save", str(plan_path))
        out, err = result.stdout, result.stderr
        assert result.returncode == 0, f"stdout: {out}\nstderr: {err}"
        artifact = ln.Artifact.get(key=key)
        assert artifact.description == expected_description
        assert "map-overlap" not in (artifact.description or "")
        assert "status: pending" not in (artifact.description or "")
    finally:
        plan_path.unlink(missing_ok=True)
        if plans_dir.exists() and not any(plans_dir.iterdir()):
            plans_dir.rmdir()
        run_lamin("delete", "artifact", "--key", key, "--permanent")
