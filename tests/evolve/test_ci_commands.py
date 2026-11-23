# Adapted source tests use deliberately invalid inputs and lightweight stand-ins.
# mypy: disable-error-code="arg-type, attr-defined, call-overload, func-returns-value, return-value"
"""Tests for lamindb_setup.evolve.ci (config, PR check, Markdown rendering)."""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from click.exceptions import Exit
from lamindb_setup.evolve.ci._config import ConfigError, load_config
from lamindb_setup.evolve.ci._markdown import render_report
from lamindb_setup.evolve.ci._pr import (
    ChangeRecord,
    DeploymentDiff,
    PlanValidation,
    PRReport,
    run_pr_check,
)
from lamindb_setup.evolve.console import OutputFormat

if TYPE_CHECKING:
    from collections.abc import Iterator

# --- config -----------------------------------------------------------------


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _make_plan(tmp_path: Path, name: str) -> Path:
    return _write(tmp_path, name, "# a plan file\n")


# --- markdown ---------------------------------------------------------------


_CREATED_IN_RUN = "managed by prerequisite plan 'base' (created by it in this run)"


def _borrowed(name: str, *warnings: str) -> ChangeRecord:
    """A borrowed object the instance lacks, created by prerequisite `base`."""
    return ChangeRecord(
        action="create",
        kind="Feature",
        name=name,
        uid="(known after apply)",
        details=["managed by a prerequisite plan"] if warnings else [_CREATED_IN_RUN],
        warnings=list(warnings),
        borrowed=True,
    )


def _child_diff(*changes: ChangeRecord) -> DeploymentDiff:
    return DeploymentDiff(
        instance_slug="acme/dev",
        plan_target="plan_child.py",
        ok=True,
        changes=list(changes),
    )


def _rename_changes() -> list[ChangeRecord]:
    """A delete + create that looks like an undeclared rename of `old`."""
    return [
        ChangeRecord(action="delete", kind="Feature", name="old", uid="uid_old"),
        ChangeRecord(
            action="create",
            kind="Feature",
            name="new",
            uid="(known after apply)",
            warnings=["looks like a rename of deleted Feature 'old'"],
            suspected_rename_of="old",
        ),
    ]


# --- run_pr_check orchestration ---------------------------------------------


class _FakePlan:
    def __init__(self) -> None:
        self.finalized = False
        self.name = "fake"
        self._exports = None
        self.dependencies: list[object] = []
        self.depends_on: tuple[object, ...] = ()
        self.finalize_count = 0
        self.reset_count = 0

    def finalize(self) -> None:
        self.finalized = True
        self.finalize_count += 1

    def reset(self) -> None:
        self.finalized = False
        self.reset_count += 1


def _config_two_instances(tmp_path: Path) -> Path:
    _make_plan(tmp_path, "plan_a.py")
    _make_plan(tmp_path, "plan_b.py")
    return _write(
        tmp_path,
        "lamin_schemas.yml",
        "instances:\n"
        "  - slug: acme/dev\n    plans: [plan_a.py:a, plan_b.py:b]\n"
        "  - slug: acme/prod\n    plans: [plan_a.py:a]\n",
    )


def _missing_prerequisite_changes(*, owner: bool = True) -> list[ChangeRecord]:
    """A borrowed object no configured plan creates, as the diff records it."""
    from lamindb_setup.evolve.core import (
        ChangeWarning,
        MissingPrerequisite,
        PrerequisitePlan,
        WarningCategory,
    )

    warning = ChangeWarning(
        "owned by a prerequisite plan that has not been applied to this instance",
        WarningCategory.PREREQUISITE,
    )
    borrowed = _borrowed("ft", warning)
    if owner:
        borrowed.missing_prerequisite = MissingPrerequisite(
            kind="Feature",
            name="ft",
            key="Feature:ft",
            owner=PrerequisitePlan("base", "plans/plan_base.py"),
        )
    own = ChangeRecord(action="create", kind="Feature", name="b", uid="u")
    return [borrowed, own]


class _FakeObj:
    def __init__(self, name: str) -> None:
        self.name = name


# --- CLI registration -------------------------------------------------------


def test_ci_app_registered() -> None:
    from lamin_cli.evolve._commands import app

    groups = list(app.commands)
    assert "ci" in groups


# --- the `ci` commands, end to end ----------------------------------------
#
# These run `lamin evolve ci ...` as its own process against the `instance`
# fixture's SQLite instance (see `run_cli` in conftest.py); an instance that
# cannot be checked is simulated with an audit hook refusing its database.

_LIVE_PLAN = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import Schema
from lamindb_setup.evolve import create_plan

PLAN_TRANSFORM_STEM_UID = "ciLiveUID001"


@create_plan
def plan_a(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("ci_ft")
    f = Feature("ci_col", dtype=LaminDType(str), type=ft)
    Schema(name="CiSchema", features=[f])
"""


def _live_config_text(slug: str, *, drift_output_dir: str | None = None) -> str:
    head = f"drift_output_dir: {drift_output_dir}\n" if drift_output_dir else ""
    return f"{head}instances:\n  - slug: {slug}\n    plans: [plan_a.py:plan_a]\n"


def _live_config(
    directory: Path, slug: str, *, drift_output_dir: str | None = None
) -> Path:
    """Write `plan_a.py` and a configuration deploying it to `slug`."""
    _write(directory, "plan_a.py", _LIVE_PLAN)
    return _write(
        directory,
        "lamin_schemas.yml",
        _live_config_text(slug, drift_output_dir=drift_output_dir),
    )


def _database_unreachable(instance: object) -> dict[str, str]:
    """A `run_cli` fault: the instance's SQLite database refuses to open."""
    return {
        "event": "sqlite3.connect",
        "match": str(instance.path),  # type: ignore[attr-defined]
        "raise": "PermissionError",
        "message": "database is unreachable",
    }


def _drift_plans(directory: Path) -> list[Path]:
    return sorted(directory.glob("plan_drift_*.py"))


def test_ci_plan_cli_runs(instance, run_cli, tmp_path: Path) -> None:
    config = _live_config(tmp_path, instance.slug)
    out = tmp_path / "report.md"

    result = run_cli("ci", "plan", "-c", str(config), "-o", str(out))

    assert result.returncode == 0, result.stderr
    report = out.read_text(encoding="utf-8")
    assert "lamin evolve CI" in report
    # A diff against the live instance, which holds none of the plan yet.
    assert "CiSchema" in report


def test_ci_plan_cli_missing_config() -> None:
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app

    result = CliRunner().invoke(app, ["ci", "plan", "-c", "definitely_missing.yml"])
    assert result.exit_code == 1
    assert "Error" in result.output


def _drift_config_with_output_dir(tmp_path: Path, output_dir: str) -> Path:
    _make_plan(tmp_path, "plan_a.py")
    return _write(
        tmp_path,
        "lamin_schemas.yml",
        f"drift_output_dir: {output_dir}\n"
        "instances:\n  - slug: acme/dev\n    plans: [plan_a.py:a]\n",
    )


def test_ci_drift_cli_uses_configured_output_dir(
    instance, run_cli, tmp_path: Path
) -> None:
    config = _live_config(tmp_path, instance.slug, drift_output_dir="drift")
    # Run from elsewhere: the configured directory is relative to the config.
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    result = run_cli("ci", "drift", "-c", str(config), cwd=cwd)

    assert result.returncode == 0, result.stderr
    assert _drift_plans(tmp_path / "drift")
    assert not _drift_plans(cwd)


def test_ci_drift_cli_output_dir_flag_overrides_config(
    instance, run_cli, tmp_path: Path
) -> None:
    config = _live_config(tmp_path, instance.slug, drift_output_dir="drift")
    explicit = tmp_path / "elsewhere"

    result = run_cli("ci", "drift", "-c", str(config), "-d", str(explicit))

    assert result.returncode == 0, result.stderr
    assert _drift_plans(explicit)
    assert not (tmp_path / "drift").exists()


# --- drift detection --------------------------------------------------------


class _FakeLockEntry:
    def __init__(
        self, uid: str, kind: str | None = None, name: str | None = None
    ) -> None:
        self.uid = uid
        self.kind = kind
        self.name = name


class _FakeLock:
    def __init__(self, uids: list[str]) -> None:
        self.objects = {u: _FakeLockEntry(u) for u in uids}

    def uid_for(self, key: str) -> str | None:  # pragma: no cover - unused here
        entry = self.objects.get(key)
        return entry.uid if entry else None


class _FakeIntroResult:
    def __init__(
        self,
        output: Path,
        reconstructed: int,
        errors: list[str],
        out_of_scope_uids: dict[str, int] | None = None,
    ) -> None:
        self.output = output
        self.reconstructed = reconstructed
        self.errors = errors
        self.out_of_scope_uids = out_of_scope_uids or {}


def _drift_config(tmp_path: Path) -> Path:
    _make_plan(tmp_path, "plan_a.py")
    _make_plan(tmp_path, "plan_b.py")
    return _write(
        tmp_path,
        "lamin_schemas.yml",
        "instances:\n  - slug: acme/dev\n    plans: [plan_a.py:a, plan_b.py:b]\n",
    )


def _drift_config_two_instances(tmp_path: Path) -> Path:
    _make_plan(tmp_path, "plan_a.py")
    return _write(
        tmp_path,
        "lamin_schemas.yml",
        "instances:\n"
        "  - slug: acme/dev\n    plans: [plan_a.py:a]\n"
        "  - slug: acme/prod\n    plans: [plan_a.py:a]\n",
    )


def _untracked_summary(objects: list[object]) -> object:
    from lamindb_setup.evolve.render import PlanRef, SummaryModel

    return SummaryModel(
        title="Untracked objects",
        plan=PlanRef(name="drift", default_space=None),
        objects=objects,  # type: ignore[arg-type]
    )


def test_ci_drift_cli_runs(instance, run_cli, tmp_path: Path) -> None:
    config = _live_config(tmp_path, instance.slug)
    out = tmp_path / "drift.md"

    result = run_cli("ci", "drift", "-c", str(config), "-o", str(out))

    assert result.returncode == 0, result.stderr
    assert "lamin evolve drift" in out.read_text(encoding="utf-8")


def test_ci_drift_cli_exit_code_on_drift(instance, ln, run_cli, tmp_path: Path) -> None:
    config = _live_config(tmp_path, instance.slug)
    ln.Feature(name="stray", dtype=str).save()

    drifted = run_cli("ci", "drift", "-c", str(config))
    tolerated = run_cli("ci", "drift", "-c", str(config), "--exit-zero")

    assert drifted.returncode == 1, drifted.stderr
    assert "stray" in drifted.stdout
    assert tolerated.returncode == 0, tolerated.stderr


def test_ci_drift_cli_format_max_chars_truncates(
    instance, ln, run_cli, tmp_path: Path
) -> None:
    config = _live_config(tmp_path, instance.slug)
    ln.Feature(name="stray", dtype=str).save()
    drift = ["ci", "drift", "-c", str(config), "--exit-zero"]

    full = run_cli(*drift)
    condensed = run_cli(*drift, "--format-max-chars", "200")

    assert full.returncode == condensed.returncode == 0, condensed.stderr
    assert "Untracked objects by type" in full.stdout
    assert "Untracked objects by type" not in condensed.stdout
    assert "Report condensed to fit GitHub" in condensed.stdout


# --- ci apply ---------------------------------------------------------------


def _record_apply_one() -> tuple[list[tuple[Path, str, str]], object]:
    """Return a `(calls, apply_one)` pair recording each `apply_one` call."""
    calls: list[tuple[Path, str, str]] = []

    def apply_one(
        plan_obj: object,
        plan_file: Path,
        stem_uid: str,
        instance: str,
        instance_slug: str,
        *,
        claimed_keys: set[str],
        confirm: object,
        output_format: object,
        strict_renames: bool = True,
        allow_data_validated_changes: bool = False,
        strict_uids: bool = True,
        track_config: object = None,
    ) -> str | None:
        # ci apply runs unattended: it must never hand apply_one a prompt.
        assert confirm is None
        calls.append((plan_file, instance, instance_slug))
        return (
            f"applied {plan_file.name} to {instance}"
            if output_format is not OutputFormat.terminal
            else None
        )

    return calls, apply_one


# --- verify-config ----------------------------------------------------------


def _verify_layout(tmp_path: Path, *, ignored: list[str] | None = None) -> Path:
    """Lay out plans/plan_a.py (referenced) + plan_b.py and return the config."""
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "plan_a.py").write_text("# a\n", encoding="utf-8")
    (plans / "plan_b.py").write_text("# b\n", encoding="utf-8")
    text = "instances:\n  - slug: a/b\n    plans: [plans/plan_a.py]\n"
    if ignored is not None:
        text += "ignored_plans:\n" + "".join(f"  - {g}\n" for g in ignored)
    return _write(tmp_path, "lamin_schemas.yml", text)


def test_ci_verify_config_cli_exits_nonzero(
    tmp_path: Path,
) -> None:
    from lamin_cli.evolve._commands import ci_verify_config

    config = _verify_layout(tmp_path)
    with pytest.raises(Exit) as exc:
        ci_verify_config.callback(config=config, output=tmp_path / "report.md")
    assert exc.value.exit_code == 1


def test_ci_verify_config_cli_passes(tmp_path: Path) -> None:
    from lamin_cli.evolve._commands import ci_verify_config

    config = _verify_layout(tmp_path, ignored=["plans/plan_b.py"])
    ci_verify_config.callback(config=config, output=tmp_path / "report.md")  # no raise


def test_ci_apply_cli_runs(
    instance, ln, run_cli, git_workspace, tmp_path: Path
) -> None:
    _plan, config = git_workspace.publish(
        {
            "plan_a.py": _LIVE_PLAN,
            "lamin_schemas.yml": _live_config_text(instance.slug),
        }
    )
    out = tmp_path / "apply.md"

    result = run_cli("ci", "apply", "-c", str(config), "-f", "markdown", "-o", str(out))

    assert result.returncode == 0, result.stderr
    assert "CiSchema" in out.read_text(encoding="utf-8")
    schema = ln.Schema.get(name="CiSchema")
    assert [feature.name for feature in schema.members] == ["ci_col"]
    assert list(git_workspace.root.glob("plan_a.*.lock"))


def test_ci_apply_cli_missing_config() -> None:
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app

    result = CliRunner().invoke(app, ["ci", "apply", "-c", "definitely_missing.yml"])
    assert result.exit_code == 1
    assert "Error" in result.output


def test_ci_apply_cli_rejects_unconfigured_changed_file(
    run_cli, tmp_path: Path
) -> None:
    config = _config_two_instances(tmp_path)
    # A plan file on disk that no instance lists is a new plan nobody registered.
    # (A changed file that no longer exists is a removed plan, and ignored.)
    _make_plan(tmp_path, "x.py")

    # Refused before any instance is reached: acme/dev does not exist.
    result = run_cli("ci", "apply", "-c", str(config), "--changed", "x.py")

    assert result.returncode == 1
    assert "not found in" in result.stderr


# --- ci missing-exports -----------------------------------------------------


class _KeyedLock:
    """A fake lock mapping natural keys to entries (uid/kind/name)."""

    def __init__(self, entries: dict[str, tuple[str, str, str]]) -> None:
        self.objects = {
            key: _FakeLockEntry(uid, kind, name)
            for key, (uid, kind, name) in entries.items()
        }

    def uid_for(self, key: str) -> str | None:  # pragma: no cover - unused here
        entry = self.objects.get(key)
        return entry.uid if entry else None


class _ReconSpec:
    def __init__(self, deps: set[str]) -> None:
        self.deps = deps


class _ReconPlan:
    def __init__(self, specs: list[_ReconSpec], errors: list[str]) -> None:
        self.specs = specs
        self.errors = errors


def _group_for(inst: object, target: str) -> object:
    return next(g for g in inst.groups if g.plan_target == target)  # type: ignore[attr-defined]


def _unexported_reference(instance, ln, run_cli, git_workspace) -> Path:
    """Apply plan_a, then use its unexported `ci_col` from outside; return the config."""
    plan_file, config = git_workspace.publish(
        {
            "plan_a.py": _LIVE_PLAN,
            "lamin_schemas.yml": _live_config_text(instance.slug),
        }
    )
    applied = run_cli("apply", str(plan_file), "-i", instance.slug, "-y")
    assert applied.returncode == 0, applied.stderr
    stray = ln.Feature(name="stray", dtype=str).save()
    ln.Schema(name="Borrower", features=[ln.Feature.get(name="ci_col"), stray]).save()
    return config


def test_ci_missing_exports_cli_runs_and_fails_when_requested(
    instance, ln, run_cli, git_workspace, tmp_path: Path
) -> None:
    config = _unexported_reference(instance, ln, run_cli, git_workspace)
    missing = ["ci", "missing-exports", "-c", str(config)]
    out = tmp_path / "missing.md"

    result = run_cli(*missing, "-o", str(out))
    assert result.returncode == 0, result.stderr
    report = out.read_text(encoding="utf-8")
    assert "lamin evolve missing-exports" in report
    assert "ci_col" in report

    # --fail-on-missing turns a non-empty closure into a non-zero exit
    assert run_cli(*missing, "--fail-on-missing").returncode == 1


def test_ci_missing_exports_cli_emits_json(
    instance, ln, run_cli, git_workspace
) -> None:
    import json

    config = _unexported_reference(instance, ln, run_cli, git_workspace)
    missing = ["ci", "missing-exports", "-c", str(config)]

    result = run_cli(*missing, "--format", "json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout[result.stdout.index("{") :])
    (group,) = payload["instances"][0]["groups"]
    assert [obj["name"] for obj in group["objects"]] == ["ci_col"]


def test_ci_missing_exports_offers_only_the_formats_it_renders() -> None:
    # 'terminal' used to be accepted and silently rendered Markdown.
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app

    runner = CliRunner()
    missing = ["ci", "missing-exports", "-c", "definitely_missing.yml"]

    result = runner.invoke(
        app, [*missing, "-f", "terminal"], terminal_width=200, env={"COLUMNS": "200"}
    )
    # Under FORCE_COLOR (as in CI) rich styles both; compare them unstyled.
    help_text = _collapse_rich_output(
        runner.invoke(
            app,
            ["ci", "missing-exports", "--help"],
            terminal_width=200,
            env={"COLUMNS": "200"},
        ).output
    )

    assert result.exit_code == 2
    assert "'terminal' is not one of 'markdown', 'json'" in _collapse_rich_output(
        result.output
    )
    assert "markdown|json" in help_text
    assert "terminal" not in help_text


def test_ci_drift_fails_when_an_instance_could_not_be_checked(
    instance, run_cli, tmp_path: Path
) -> None:
    config = _live_config(tmp_path, instance.slug)

    result = run_cli(
        "ci", "drift", "-c", str(config), faults=[_database_unreachable(instance)]
    )

    assert result.returncode == 1, result.stderr
    assert "database is unreachable" in result.stdout
    assert "drift detection failed" in result.stderr


def test_ci_drift_exit_zero_does_not_mask_an_unchecked_instance(
    instance, run_cli, tmp_path: Path
) -> None:
    """--exit-zero means 'report drift without failing', not 'pass regardless'."""
    config = _live_config(tmp_path, instance.slug)

    result = run_cli(
        "ci",
        "drift",
        "-c",
        str(config),
        "--exit-zero",
        faults=[_database_unreachable(instance)],
    )

    assert result.returncode == 1, result.stderr


def test_ci_drift_still_passes_a_clean_instance(
    instance, run_cli, tmp_path: Path
) -> None:
    config = _live_config(tmp_path, instance.slug)

    result = run_cli("ci", "drift", "-c", str(config))

    assert result.returncode == 0, result.stderr
    assert "No drift detected" in result.stdout


def test_ci_missing_exports_fails_when_an_instance_could_not_be_reconstructed(
    instance, run_cli, tmp_path: Path
) -> None:
    """Independent of --fail-on-missing: no gaps were found because none were sought."""
    config = _live_config(tmp_path, instance.slug)

    result = run_cli(
        "ci",
        "missing-exports",
        "-c",
        str(config),
        faults=[_database_unreachable(instance)],
    )

    assert result.returncode == 1, result.stderr
    assert "database is unreachable" in result.stdout
    assert "could not reconstruct" in result.stderr


# --- restricting a run to some instances (--instance/-i) ---------------------


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _collapse_rich_output(text: str) -> str:
    """`text` without rich's styling and box borders, whitespace collapsed."""
    plain = _ANSI_ESCAPE_RE.sub("", text)
    return " ".join(plain.replace("\u2502", " ").split())


@pytest.mark.parametrize(
    "command",
    [["plan"], ["apply"], ["drift", "-d", "drift"], ["missing-exports"]],
    ids=lambda command: command[0],
)
def test_ci_commands_refuse_an_unknown_instance_as_a_usage_error(
    run_cli, tmp_path: Path, command: list[str]
) -> None:
    _config_two_instances(tmp_path)

    result = run_cli(
        "ci", *command, "-c", "lamin_schemas.yml", "-i", "acme/prod", "-i", "acme/qa"
    )

    assert result.returncode == 2, result.stderr
    # Under FORCE_COLOR (as in CI) rich styles the error box and wraps it at 80
    # columns, so the message is compared with styling, borders and breaks gone.
    stderr = _collapse_rich_output(result.stderr)
    assert "Invalid value: unknown instance(s) 'acme/qa'" in stderr
    assert "lamin_schemas.yml configures acme/dev, acme/prod" in stderr
    # Refused before anything was loaded or written.
    assert not (tmp_path / "drift").exists()


def _live_config_for(directory: Path, *slugs: str) -> Path:
    """Write `plan_a.py` and a configuration deploying it to each of `slugs`."""
    _write(directory, "plan_a.py", _LIVE_PLAN)
    return _write(directory, "lamin_schemas.yml", _live_instances_text(*slugs))


def _live_instances_text(*slugs: str) -> str:
    return "instances:\n" + "".join(
        f"  - slug: {slug}\n    plans: [plan_a.py:plan_a]\n" for slug in slugs
    )


def test_ci_plan_diffs_only_the_selected_instance(
    instance, instance_factory, run_cli, tmp_path: Path
) -> None:
    other = instance_factory("other")
    # The selected instance is configured second; the unselected one first.
    config = _live_config_for(tmp_path, other.slug, instance.slug)
    out = tmp_path / "report.md"

    # A run that so much as opened the unselected instance would fail.
    result = run_cli(
        "ci",
        "plan",
        "-c",
        str(config),
        "-i",
        instance.slug,
        "-o",
        str(out),
        faults=[_database_unreachable(other)],
    )

    assert result.returncode == 0, result.stderr
    report = out.read_text(encoding="utf-8")
    assert f"`plan_a.py:plan_a` → `{instance.slug}`" in report
    assert f"`{other.slug}`" not in report


def test_ci_apply_rolls_out_one_instance_at_a_time(
    instance, ln, instance_factory, run_cli, git_workspace
) -> None:
    from lamindb_setup.evolve.engine import LockFile

    dev = instance_factory("dev")
    _plan, config = git_workspace.publish(
        {
            "plan_a.py": _LIVE_PLAN,
            "lamin_schemas.yml": _live_instances_text(dev.slug, instance.slug),
        }
    )
    root = git_workspace.root

    # On merge: dev only.
    applied = run_cli("ci", "apply", "-c", str(config), "-i", dev.slug, cwd=root)

    assert applied.returncode == 0, applied.stderr
    [dev_lock] = root.glob("plan_a.*.lock")
    assert not ln.Schema.filter(name="CiSchema").exists()

    # On release: prod only, in a run of its own -- which still creates every
    # object with the uid the dev run recorded.
    applied = run_cli("ci", "apply", "-c", str(config), "-i", instance.slug, cwd=root)

    assert applied.returncode == 0, applied.stderr
    [prod_lock] = set(root.glob("plan_a.*.lock")) - {dev_lock}
    assert [feature.name for feature in ln.Schema.get(name="CiSchema").members] == [
        "ci_col"
    ]

    def uids(lock_file: Path) -> dict[str, str | None]:
        lock = LockFile.read(lock_file)
        assert lock is not None
        return {key: entry.uid for key, entry in lock.objects.items()}

    assert uids(prod_lock) == uids(dev_lock)
    assert ln.Schema.get(name="CiSchema").uid == uids(dev_lock)["Schema:CiSchema"]


def test_ci_drift_checks_only_the_selected_instance(
    instance, instance_factory, run_cli, tmp_path: Path
) -> None:
    other = instance_factory("other")
    config = _live_config_for(tmp_path, other.slug, instance.slug)
    drift_dir = tmp_path / "drift"

    result = run_cli(
        "ci",
        "drift",
        "-c",
        str(config),
        "-i",
        instance.slug,
        "-d",
        str(drift_dir),
        faults=[_database_unreachable(other)],
    )

    assert result.returncode == 0, result.stderr
    assert len(_drift_plans(drift_dir)) == 1
    assert f"`{instance.slug}`" in result.stdout
    assert f"`{other.slug}`" not in result.stdout


def test_ci_missing_exports_checks_only_the_selected_instance(
    instance, instance_factory, run_cli, tmp_path: Path
) -> None:
    other = instance_factory("other")
    config = _live_config_for(tmp_path, other.slug, instance.slug)

    result = run_cli(
        "ci",
        "missing-exports",
        "-c",
        str(config),
        "-i",
        instance.slug,
        faults=[_database_unreachable(other)],
    )

    assert result.returncode == 0, result.stderr
    assert f"`{instance.slug}`" in result.stdout
    assert f"`{other.slug}`" not in result.stdout


# --- ci plan --preview-migrations -------------------------------------


class _Rehearsals:
    """`run_pr_check` hooks recording what is diffed, and in which rehearsal.

    `down` depends on `up`; `failing` names the plans whose diff raises,
    `renaming` those whose diff holds a rename not declared with `ctx.moved`.
    """

    def __init__(self, *failing: str, renaming: tuple[str, ...] = ()) -> None:
        self.events: list[str] = []
        self.failing = set(failing)
        self.renaming = set(renaming)
        self.up = _FakePlan()
        self.up.name = "up"
        self.up.own_dependencies = []  # type: ignore[attr-defined]
        self.down = _FakePlan()
        self.down.name = "down"
        self.down.depends_on = (self.up,)
        self._slug = ""

    def load(self, target: str) -> tuple[_FakePlan, Path, object]:
        plan = self.down if target.endswith(":down") else self.up
        return plan, Path(target.partition(":")[0]), object()

    def connect(self, slug: str) -> str:
        self._slug = slug
        return slug

    @contextmanager
    def rehearsal(self) -> Iterator[None]:
        self.events.append(f"open {self._slug}")
        yield
        self.events.append(f"close {self._slug}")

    def compute(
        self,
        plan: _FakePlan,
        plan_file: Path,
        slug: str,
        claimed_keys: set[str],
        claimed_by: dict[str, str] | None = None,
        allow_data_validated_changes: bool = False,
        co_applied_keys: set[str] | None = None,
    ) -> list[ChangeRecord]:
        self.events.append(f"{plan.name} on {slug}")
        if plan.name in self.failing:
            raise RuntimeError(f"{plan.name}: map_to_records cannot map 'gamma'")
        if plan.name in self.renaming:
            return _rename_changes()
        return [
            ChangeRecord(
                action="update",
                kind="Feature",
                name=plan.name,
                uid="u",
                details=["map_to_records: 2 matched"],
            )
        ]

    def run(self, config: Path, **kwargs: object) -> PRReport:
        return run_pr_check(
            load_config(config),
            load_plan=self.load,
            connect=self.connect,
            compute=self.compute,
            rehearsal=self.rehearsal,
            keys_of=lambda plan: {plan.name},
            lock_exists=lambda plan_file, slug: True,
            **kwargs,  # type: ignore[arg-type]
        )


def _rehearsal_config(tmp_path: Path, head: str = "") -> Path:
    """`down` (configured first) and its prerequisite `up` on dev; `up` on prod."""
    _make_plan(tmp_path, "plan_up.py")
    _make_plan(tmp_path, "plan_down.py")
    return _write(
        tmp_path,
        "lamin_schemas.yml",
        f"{head}instances:\n"
        "  - slug: acme/dev\n    plans: [plan_down.py:down, plan_up.py:up]\n"
        "  - slug: acme/prod\n    plans: [plan_up.py:up]\n",
    )


def _outcomes(report: PRReport) -> list[tuple[str, str, str | None]]:
    return [(d.plan_target, d.instance_slug, d.error) for d in report.diffs]


_NOT_REHEARSED = (
    "not rehearsed: plan_up.py:up failed before it, and ci apply stops the "
    "instance there too"
)


_PREVIEW_PLAN_STR = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan

PLAN_TRANSFORM_STEM_UID = "ciPrevUID001"


@create_plan
def preview_plan(ctx: PlanContext, exports: None) -> None:
    Feature("ci_pv", dtype=LaminDType(str), type=FeatureType("ci_pv_ft"))
"""

_PREVIEW_PLAN_VOCABULARY = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import Record
from lamindb_setup.evolve import RecordType
from lamindb_setup.evolve import create_plan
from lamindb_setup.evolve import map_to_records

PLAN_TRANSFORM_STEM_UID = "ciPrevUID001"


@create_plan
def preview_plan(ctx: PlanContext, exports: None) -> None:
    import lamindb as ln

    vocabulary = RecordType("CiPvVocab")
    Record("alpha", type=vocabulary)
    Record("beta", type=vocabulary)
    feature = Feature(
        "ci_pv",
        dtype=LaminDType(ln.Record, type__uid=vocabulary.uid),
        type=FeatureType("ci_pv_ft"),
    )
    ctx.migrate(feature, map_to_records())
"""


def _stage_a_migration(
    instance,
    ln,
    run_cli,
    git_workspace,
    *values: str,
    head: str = "",
    also_to: tuple[str, ...] = (),
) -> Path:
    """Apply `ci_pv` as `str`, store `values`, then publish its migration.

    Returns the configuration deploying the migrating plan to the instances
    `also_to` names, then to `instance`.
    """
    (plan_file,) = git_workspace.publish({"plan_pv.py": _PREVIEW_PLAN_STR})
    applied = run_cli("apply", str(plan_file), "-i", instance.slug, "-y")
    assert applied.returncode == 0, applied.stderr
    sheet = ln.Record(name="CiPvSheet", is_type=True).save()
    for index, value in enumerate(values):
        ln.Record(name=f"row{index}", type=sheet, features={"ci_pv": value}).save()
    _plan, config = git_workspace.publish(
        {
            "plan_pv.py": _PREVIEW_PLAN_VOCABULARY,
            "lamin_schemas.yml": f"{head}instances:\n"
            + "".join(
                f"  - slug: {slug}\n    plans: [plan_pv.py]\n"
                for slug in (*also_to, instance.slug)
            ),
        }
    )
    return config


def _instance_state(ln, root: Path) -> tuple[object, ...]:
    """What a rehearsal must leave as it found it: objects, runs and lock files."""
    return (
        ln.Feature.get(name="ci_pv", is_type=False).dtype_as_str,
        ln.Record.filter(name="CiPvVocab", is_type=True).exists(),
        ln.Transform.filter().count(),
        ln.Run.filter().count(),
        {path.name: path.read_text(encoding="utf-8") for path in root.glob("*.lock")},
    )


@pytest.mark.parametrize(
    ("flags", "head"),
    [(("--preview-migrations",), ""), ((), "preview_migrations: true\n")],
    ids=["flag", "config"],
)
def test_ci_plan_preview_migrations_reports_a_migrations_outcome(
    instance,
    ln,
    run_cli,
    git_workspace,
    tmp_path: Path,
    flags: tuple[str, ...],
    head: str,
) -> None:
    config = _stage_a_migration(
        instance, ln, run_cli, git_workspace, "alpha", "beta", head=head
    )
    before = _instance_state(ln, git_workspace.root)
    out = tmp_path / "report.md"

    result = run_cli(
        "ci", "plan", "-c", str(config), "-o", str(out), *flags, cwd=git_workspace.root
    )

    assert result.returncode == 0, result.stderr
    report = out.read_text(encoding="utf-8")
    assert "> Migrations rehearsed:" in report
    diff = report[report.index(f"`plan_pv.py` \u2192 `{instance.slug}`") :]
    feature = diff.index("~ Feature 'ci_pv'")
    assert diff.index("    map_to_records: 2 matched", feature) > feature
    assert "+ RecordType 'CiPvVocab'  (known after apply)" in diff
    assert _instance_state(ln, git_workspace.root) == before
    assert before[:2] == ("str", False)


def test_ci_plan_preview_migrations_fails_where_ci_apply_would(
    instance, ln, run_cli, git_workspace, tmp_path: Path
) -> None:
    config = _stage_a_migration(instance, ln, run_cli, git_workspace, "alpha", "gamma")
    before = _instance_state(ln, git_workspace.root)
    out = tmp_path / "report.md"

    result = run_cli(
        "ci",
        "plan",
        "-c",
        str(config),
        "-o",
        str(out),
        "--preview-migrations",
        cwd=git_workspace.root,
    )

    assert result.returncode == 1
    report = out.read_text(encoding="utf-8")
    assert "Validation failed" in report
    failed = report.index(f"#### \u274c `plan_pv.py` \u2192 `{instance.slug}`")
    assert "'gamma'" in report[failed:]
    assert _instance_state(ln, git_workspace.root) == before


def test_ci_plan_does_not_rehearse_without_preview_migrations(
    instance, ln, run_cli, git_workspace, tmp_path: Path
) -> None:
    # The gap the option closes: the read-only diff cannot see the failure.
    config = _stage_a_migration(instance, ln, run_cli, git_workspace, "alpha", "gamma")
    before = _instance_state(ln, git_workspace.root)
    out = tmp_path / "report.md"

    result = run_cli(
        "ci", "plan", "-c", str(config), "-o", str(out), cwd=git_workspace.root
    )

    assert result.returncode == 0, result.stderr
    report = out.read_text(encoding="utf-8")
    assert "Migrations rehearsed" not in report
    assert "map_to_records:" not in report
    assert "~ Feature 'ci_pv'" in report
    assert _instance_state(ln, git_workspace.root) == before


def test_ci_plan_preview_migrations_rehearses_every_instance(
    instance, ln, instance_factory, run_cli, git_workspace, tmp_path: Path
) -> None:
    # A staged roll-out: dev, configured first, already runs the migrating
    # plan; `instance` is rehearsed after the switch away from dev.
    dev = instance_factory("dev")
    config = _stage_a_migration(
        instance, ln, run_cli, git_workspace, "alpha", "beta", also_to=(dev.slug,)
    )
    applied = run_cli(
        "apply", "plan_pv.py", "-i", dev.slug, "-y", cwd=git_workspace.root
    )
    assert applied.returncode == 0, applied.stderr
    before = _instance_state(ln, git_workspace.root)
    out = tmp_path / "report.md"

    result = run_cli(
        "ci",
        "plan",
        "-c",
        str(config),
        "-o",
        str(out),
        "--preview-migrations",
        cwd=git_workspace.root,
    )

    assert result.returncode == 0, result.stderr
    report = out.read_text(encoding="utf-8")
    on_dev = report.index(f"`plan_pv.py` \u2192 `{dev.slug}`")
    here = report.index(f"`plan_pv.py` \u2192 `{instance.slug}`")
    assert "No changes. Instance is up to date." in report[on_dev:here]
    assert "map_to_records: 2 matched" in report[here:]
    assert _instance_state(ln, git_workspace.root) == before
