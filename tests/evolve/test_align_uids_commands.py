# Adapted source tests use deliberately invalid inputs and lightweight stand-ins.
# mypy: disable-error-code="arg-type, return-value"
"""Tests for keeping plan-managed uids aligned across instances.

Two halves: `apply` pinning an object to the uid the plan's lock files for its
other instances record (:mod:`lamindb_setup.evolve.engine._alignment`), and
`repair align-uids` rewriting an instance's uids to a source's
(:mod:`lamindb_setup.evolve.maintenance._align_uids`).

A single test instance plays every role: "other instances" are lock files and
:class:`SiblingUids` built by hand, and a source is a :class:`SourceIdentity`
built from uids the instance does not have yet.
"""

from __future__ import annotations

import json
import secrets
import string
from pathlib import Path

_CLI_LAUNCHER = Path(__file__).resolve().parent / "_support" / "cli_child.py"
from typing import TYPE_CHECKING, Any

import pytest
from lamindb_setup.evolve import (
    Feature,
    FeatureType,
    LaminDType,
    Plan,
    PlanContext,
    PlanExports,
    Record,
    RecordType,
    Schema,
    SchemaType,
    Sheet,
    create_plan,
)
from lamindb_setup.evolve.ci._config import ConfigError, load_config
from lamindb_setup.evolve.ci._markdown import render_report
from lamindb_setup.evolve.ci._pr import ChangeRecord, DeploymentDiff, PRReport
from lamindb_setup.evolve.core import (
    Action,
    ApplyMode,
    ChangeItem,
    UidMisalignment,
    WarningCategory,
)
from lamindb_setup.evolve.engine._alignment import (
    SiblingUids,
    UidAlignmentError,
    read_sibling_uids,
    sibling_lock_paths,
)
from lamindb_setup.evolve.engine._apply import compute_changes, write_lock
from lamindb_setup.evolve.engine._lock import (
    LOCK_VERSION,
    LockEntry,
    LockFile,
    lock_path,
    natural_key,
)
from lamindb_setup.evolve.maintenance._align_uids import (
    HOLDER_CONFLICT,
    HOLDER_FREE,
    HOLDER_MERGE,
    HOLDER_REASSIGN,
    AlignError,
    SourceIdentity,
    compute_alignment,
    execute_alignment,
    read_source_identity,
    render_alignment,
    rewrite_target_locks,
    with_prerequisites,
)
from lamindb_setup.evolve.render import (
    render_changes,
    render_changes_json,
    render_changes_markdown,
    render_uid_misalignments,
    uid_misalignment_message,
)

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable, Iterator
    from types import SimpleNamespace as TestInstanceInfo
    from typing import Any as InstanceFactory
    from typing import Any as LamindbModuleType

    from conftest import GitWorkspace


_BASE62 = string.digits + string.ascii_letters
_SOURCE = "acme-source"
_TARGET = "acme-target"


def _uid(kind: str) -> str:
    length = 12 if kind in ("Feature", "FeatureType") else 16
    return "".join(secrets.choice(_BASE62) for _ in range(length))


@create_plan
def _align_plan(ctx: PlanContext, exports: None) -> None:
    import lamindb as ln

    ft = FeatureType("al_ft")
    f_name = Feature("al_name", dtype=LaminDType(str), type=ft)
    f_note = Feature("al_note", dtype=LaminDType(str), type=ft)
    rt = RecordType("al_rt")
    f_link = Feature(
        "al_link", dtype=LaminDType(ln.models.Record, type__uid=rt.uid), type=ft
    )
    st = SchemaType("al_st")
    schema = Schema(
        name="AlSchema",
        features=[f_name, f_note, f_link],
        index=f_name,
        optionals=[f_note],
        type=st,
    )
    Sheet("al_sheet", schema=schema, type=rt)
    Record("al_value", type=rt)


class _CoreExports(PlanExports):
    feature_type: FeatureType


@create_plan
def _core_plan(ctx: PlanContext, exports: _CoreExports) -> None:
    exports.feature_type = FeatureType("core_ft")


@create_plan
def _downstream_plan(
    ctx: PlanContext, exports: None, core: Plan[_CoreExports] = _core_plan
) -> None:
    Feature("down_col", dtype=LaminDType(str), type=core.exports.feature_type)


@create_plan
def _moved_plan(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("mv_ft")
    ctx.moved(Feature("mv_new", dtype=LaminDType(str), type=ft), "mv_old")


@create_plan
def _optionals_plan(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("op_ft")
    first = Feature("op_a", dtype=LaminDType(str), type=ft)
    second = Feature("op_b", dtype=LaminDType(str), type=ft)
    Schema(name="OpSchema", features=[first, second], optionals=[first, second])


def _twin_plan(*, optional: bool) -> Plan[None]:
    """A typed plan schema with `tw_b` optional, or made required."""

    @create_plan
    def _twin(ctx: PlanContext, exports: None) -> None:
        ft = FeatureType("tw_ft")
        a = Feature("tw_a", dtype=LaminDType(str), type=ft)
        b = Feature("tw_b", dtype=LaminDType(str), type=ft)
        Schema(
            name="TwSchema",
            features=[a, b],
            optionals=[b] if optional else None,
            type=SchemaType("tw_st"),
        )

    return _twin


_TWIN_OPTIONAL = _twin_plan(optional=True)
_TWIN_REQUIRED = _twin_plan(optional=False)

_PLANS = (
    _align_plan,
    _core_plan,
    _downstream_plan,
    _moved_plan,
    _optionals_plan,
    _TWIN_OPTIONAL,
    _TWIN_REQUIRED,
)


@pytest.fixture(autouse=True)
def _fresh_plans() -> Iterator[None]:
    """Module-level plans keep their finalized objects (and resolved promises)."""
    for plan in _PLANS:
        plan.reset()
    yield
    for plan in _PLANS:
        plan.reset()


def _own_keys(plan: Plan[Any]) -> dict[str, str]:
    """Natural key -> kind for every object `plan` declares itself."""
    plan.finalize()
    return {natural_key(d.obj): type(d.obj).__name__ for d in plan.own_dependencies}


def _siblings(
    tracked: dict[str, str], seeded: dict[str, str] | None = None
) -> SiblingUids:
    return SiblingUids(
        tracked={key: {_SOURCE: uid} for key, uid in tracked.items()},
        seeded={key: {_SOURCE: uid} for key, uid in (seeded or {}).items()},
    )


def _pinned_uids(plan: Plan[Any]) -> tuple[dict[str, str], dict[str, str]]:
    tracked: dict[str, str] = {}
    seeded: dict[str, str] = {}
    for key, kind in _own_keys(plan).items():
        (seeded if kind == "Record" else tracked)[key] = _uid(kind)
    return tracked, seeded


def _apply(
    plan: Plan[Any], lock_file: Path, **kwargs: Any
) -> Any:  # pragma: no cover - thin helper
    ctx = compute_changes(
        plan, mode=ApplyMode.APPLY, lock=LockFile.read(lock_file), **kwargs
    )
    write_lock(ctx, plan, lock_file)
    return ctx


def _registry(ln: LamindbModuleType, kind: str) -> Any:
    return {
        "FeatureType": ln.Feature,
        "Feature": ln.Feature,
        "SchemaType": ln.Schema,
        "Schema": ln.Schema,
        "RecordType": ln.Record,
        "Sheet": ln.Record,
        "Record": ln.Record,
    }[kind]


# === Lock file v6 ===============================================================


# === Sibling locks ==============================================================


def _write_lock(path: Path, plan: str, uids: dict[str, str]) -> None:
    lock = LockFile(plan=plan)
    for key, uid in uids.items():
        lock.add(LockEntry(key=key, kind="FeatureType", name=key, uid=uid))
    lock.write(path)


# === Pinning at apply ===========================================================


# === Rendering ==================================================================


# === Rendering ==================================================================


def _misaligned_change() -> ChangeItem:
    return ChangeItem(
        obj_id=1,
        key="FeatureType:ft",
        kind="FeatureType",
        name="ft",
        action=Action.NO_CHANGE,
        uid="S1",
        uid_misalignment=UidMisalignment(
            uid="S1", others={"acme-general": "G1"}, reason="uid S1 here, G1 there"
        ),
    )


def _misaligned_diff(*extra: ChangeRecord) -> DeploymentDiff:
    record = ChangeRecord(
        action="no-change",
        kind="FeatureType",
        name="ft",
        uid="S1",
        uid_misalignment="uid S1 here, G1 there (acme-general: G1)",
    )
    return DeploymentDiff(
        instance_slug="acme/sandbox",
        plan_target="plan.py:p",
        ok=True,
        changes=[record, *extra],
    )


class _FakeCIPlan:
    name = "a"
    depends_on: tuple[()] = ()
    dependencies: list[object] = []

    def finalize(self) -> None:
        return None

    def reset(self) -> None:
        return None


@pytest.mark.parametrize(("flags", "code"), [((), 1), (("--no-strict-uids",), 0)])
def test_ci_plan_fail_on_diff_honours_strict_uids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: tuple[str, ...],
    code: int,
) -> None:
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app
    from lamindb_setup.evolve import ci

    def fake_check(config: Any, *, strict_uids: bool = True, **kwargs: Any) -> PRReport:
        return PRReport(
            diffs=[_misaligned_diff()],
            strict_uids=strict_uids and config.strict_uids,
        )

    monkeypatch.setattr(ci, "run_pr_check", fake_check)
    config = _config(tmp_path)
    result = CliRunner().invoke(
        app, ["ci", "plan", "-c", str(config), "--fail-on-diff", *flags]
    )

    assert result.exit_code == code, result.output


# === Configuration ==============================================================


def _config(tmp_path: Path, extra: str = "") -> Path:
    (tmp_path / "plan_a.py").write_text("")
    path = tmp_path / "lamin_schemas.yml"
    path.write_text(
        f"{extra}instances:\n  - slug: acme/dev\n    plans:\n      - plan_a.py:a\n"
    )
    return path


# === align-uids =================================================================


def _identity(
    plan: Plan[Any], tracked: dict[str, str], seeded: dict[str, str]
) -> SourceIdentity:
    return SourceIdentity(
        slug=_SOURCE, tracked={plan.name: tracked}, seeded={plan.name: seeded}
    )


def _applied_target(tmp_path: Path) -> tuple[Path, Path, Any]:
    plan_file = tmp_path / "plan_align.py"
    target_lock = lock_path(plan_file, _TARGET)
    ctx = _apply(_align_plan, target_lock)
    return plan_file, target_lock, ctx


def _live_stub_record_type(ln: LamindbModuleType, name: str, uid: str) -> Any:
    previous = ln.settings.creation.search_names
    ln.settings.creation.search_names = False
    try:
        stub = ln.Record(name=name, is_type=True)
        stub.uid = uid
        stub.save()
    finally:
        ln.settings.creation.search_names = previous
    return stub


# --- a content two live schemas already share ------------------------------


@pytest.fixture
def writable_storage(ln: LamindbModuleType) -> None:
    """Point the cached instance's `Storage` row at where the run actually is."""
    import lamindb_setup as ln_setup

    storage = ln_setup.settings.storage
    root = storage.root_as_str
    ln.Storage.objects.filter(uid=Path(root).name).update(root=root)
    storage._record = None


def _shared_content(ln: LamindbModuleType, tmp_path: Path) -> tuple[Path, Any, Any]:
    """The plan schema and an inferred schema already sharing their content.

    An artifact is curated while the plan's `tw_b` is
    still optional somewhere, so lamindb records its columns -- `tw_a` and
    `tw_b`, both required -- as a new unnamed schema.  A later apply makes
    `tw_b` required, giving the plan schema exactly that content, which
    lamindb before 2.12 let through; here the inferred schema waits in the
    trash meanwhile.  Returns the plan file, the inferred schema and the
    artifact recording it.
    """
    import pandas as pd
    from lamindb.models.schema import ArtifactSchema

    plan_file = tmp_path / "plan_twin.py"
    lock_file = lock_path(plan_file, _TARGET)
    _apply(_TWIN_OPTIONAL, lock_file)
    a, b = (ln.Feature.get(name=name) for name in ("tw_a", "tw_b"))
    c = ln.Feature(name="tw_c", dtype=str).save()
    validator = ln.Schema(
        name="TwValidator",
        features=[a, b.with_config(optional=True), c.with_config(optional=True)],
    ).save()
    frame = pd.DataFrame({"tw_a": ["x"], "tw_b": ["y"]})
    artifact = ln.Artifact.from_dataframe(
        frame, key="tw/frame.parquet", schema=validator
    ).save()
    inferred = ArtifactSchema.objects.get(artifact=artifact, slot="columns").schema
    assert inferred.name is None

    inferred.delete(permanent=False)
    _apply(_TWIN_REQUIRED, lock_file, allow_data_validated_changes=True)
    ln.Schema.objects.filter(pk=inferred.pk).update(branch_id=1)
    inferred = ln.Schema.objects.get(pk=inferred.pk)
    assert inferred.hash == ln.Schema.get(name="TwSchema").hash
    return plan_file, inferred, artifact


def _align_twins(
    plan_file: Path, **kwargs: Any
) -> tuple[Any, dict[str, str], dict[str, str]]:
    tracked, seeded = _pinned_uids(_TWIN_REQUIRED)
    alignment = compute_alignment(
        [(_TWIN_REQUIRED, plan_file)],
        _identity(_TWIN_REQUIRED, tracked, seeded),
        instance="acme/target",
        slug=_TARGET,
        **kwargs,
    )
    return alignment, tracked, seeded


def _plan_schema_uid(tracked: dict[str, str]) -> str:
    return next(uid for key, uid in tracked.items() if key.endswith("Schema:TwSchema"))


def _named_twin(ln: LamindbModuleType, tmp_path: Path) -> tuple[Path, Any, Any]:
    """A hand-made, named schema with the plan schema's content, validating data."""
    plan_file, twin, artifact = _shared_content(ln, tmp_path)
    ln.Schema.objects.filter(pk=twin.pk).update(name="TwStrict")
    ln.Artifact.objects.filter(pk=artifact.pk).update(schema_id=twin.pk)
    return plan_file, ln.Schema.objects.get(pk=twin.pk), artifact


# === CLI ========================================================================


def test_repair_align_uids_command_is_registered() -> None:
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "repair",
            "align-uids",
            "--help",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"},
    )
    assert result.returncode == 0, result.stderr
    for option in ("--source", "--merge-duplicates", "--reassign-colliding"):
        assert option in result.stdout


_ALIGN_PLAN_FILE = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import Record
from lamindb_setup.evolve import RecordType
from lamindb_setup.evolve import Schema
from lamindb_setup.evolve import SchemaType
from lamindb_setup.evolve import Sheet
from lamindb_setup.evolve import create_plan

PLAN_TRANSFORM_STEM_UID = "alCliUID0001"


@create_plan
def plan_align(ctx: PlanContext, exports: None) -> None:
    import lamindb as ln

    ft = FeatureType("al_ft")
    f_name = Feature("al_name", dtype=LaminDType(str), type=ft)
    f_note = Feature("al_note", dtype=LaminDType(str), type=ft)
    rt = RecordType("al_rt")
    f_link = Feature(
        "al_link", dtype=LaminDType(ln.models.Record, type__uid=rt.uid), type=ft
    )
    schema = Schema(
        name="AlSchema",
        features=[f_name, f_note, f_link],
        index=f_name,
        optionals=[f_note],
        type=SchemaType("al_st"),
    )
    Sheet("al_sheet", schema=schema, type=rt)
    Record("al_value", type=rt)
"""


def _lock_uids(lock_file: Path) -> dict[str, str | None]:
    lock = LockFile.read(lock_file)
    assert lock is not None
    return {key: entry.uid for key, entry in lock.objects.items()} | lock.seeded


def _apply_apart(
    instance: TestInstanceInfo,
    instance_factory: InstanceFactory,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    git_workspace: GitWorkspace,
) -> tuple[TestInstanceInfo, Path, Path, Path]:
    """Apply the align plan to a new source and to `instance`, each on its own.

    Returns the source, the plan file and the source's and target's committed
    lock files.
    """
    source = instance_factory("source")
    root = git_workspace.root
    [plan_file] = git_workspace.publish({"plan_align.py": _ALIGN_PLAN_FILE})

    # Each instance assigns its own uids while the other's lock is out of sight,
    # which is how two instances drifted apart before apply pinned uids.
    applied = run_cli("apply", str(plan_file), "-i", source.slug, "-y", cwd=root)
    assert applied.returncode == 0, applied.stderr
    [source_lock] = root.glob("plan_align.*.lock")
    aside = source_lock.rename(root.parent / source_lock.name)
    applied = run_cli("apply", str(plan_file), "-i", instance.slug, "-y", cwd=root)
    assert applied.returncode == 0, applied.stderr
    aside.rename(source_lock)
    [target_lock] = set(root.glob("plan_align.*.lock")) - {source_lock}
    git_workspace.git("add", source_lock.name, target_lock.name)
    git_workspace.git("commit", "-m", "lock both instances")
    return source, plan_file, source_lock, target_lock


def test_repair_align_uids_command_rewrites_target_and_lock(
    instance: TestInstanceInfo,
    ln: LamindbModuleType,
    instance_factory: InstanceFactory,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    git_workspace: GitWorkspace,
) -> None:
    source, plan_file, source_lock, target_lock = _apply_apart(
        instance, instance_factory, run_cli, git_workspace
    )
    root = git_workspace.root

    source_uids = _lock_uids(source_lock)
    assert source_uids.keys() == _lock_uids(target_lock).keys()
    assert all(_lock_uids(target_lock)[key] != uid for key, uid in source_uids.items())

    result = run_cli(
        "repair",
        "align-uids",
        str(plan_file),
        "--source",
        source.slug,
        "-i",
        instance.slug,
        "-y",
        cwd=root,
    )

    assert result.returncode == 0, result.stderr
    assert f"Rewrote lock file: {target_lock}" in result.stdout
    # Every phase says what it is waiting on, so a slow instance does not look hung.
    for stage in (
        f"Connecting to source {source.slug}",
        f"Connecting to target {instance.slug}",
        f"Previewing the rewrite on {instance.slug} (rolled back)",
        f"Rewriting {instance.slug}",
        "Rehashing 1 schema(s)",
    ):
        assert stage in result.stderr
    assert _lock_uids(target_lock) == source_uids
    assert _lock_uids(source_lock) == source_uids
    assert ln.Feature.get(name="al_ft").uid == source_uids["FeatureType:al_ft"]
    assert (
        ln.Record.get(name="al_value").uid
        == source_uids["RecordType:al_rt/Record:al_value"]
    )
    record_type_uid = source_uids["RecordType:al_rt"]
    assert record_type_uid in ln.Feature.get(name="al_link").dtype_as_str
    report = ln.Artifact.get(description__startswith="repair-align-uids report")
    assert "Align uids" in report.cache().read_text()


def test_repair_align_uids_dry_run_stops_after_the_preview(
    instance: TestInstanceInfo,
    ln: LamindbModuleType,
    instance_factory: InstanceFactory,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    git_workspace: GitWorkspace,
) -> None:
    source, plan_file, source_lock, target_lock = _apply_apart(
        instance, instance_factory, run_cli, git_workspace
    )
    # A dry run rewrites no lock file, so an uncommitted one is only noted.
    target_lock.write_text(
        target_lock.read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    target_text = target_lock.read_text(encoding="utf-8")
    target_uid = ln.Feature.get(name="al_ft").uid
    runs = ln.Run.filter().count()

    args = (
        "repair",
        "align-uids",
        str(plan_file),
        "--source",
        source.slug,
        "-i",
        instance.slug,
    )

    result = run_cli(*args, "--dry-run", cwd=git_workspace.root)

    assert result.returncode == 0, result.stderr
    assert f"Align uids (preview): {instance.slug}" in result.stdout
    assert "[y/N]" not in result.stdout
    assert result.stdout.rstrip().endswith("Dry run: nothing was changed.")
    stderr = " ".join(result.stderr.split())
    assert "Note: a real run would refuse: lock file" in stderr
    assert "has uncommitted changes" in stderr
    assert "Note: a real run would refuse " + instance.slug not in stderr
    assert target_lock.read_text(encoding="utf-8") == target_text
    assert _lock_uids(target_lock) != _lock_uids(source_lock)
    assert ln.Feature.get(name="al_ft").uid == target_uid
    assert ln.Run.filter().count() == runs

    # A live record holding a uid the target needs refuses its preview: the dry
    # run reports that too, and still exits 0.
    squatter = ln.Feature(name="al_squatter", dtype=str).save()
    ln.Feature.objects.filter(id=squatter.id).update(
        uid=_lock_uids(source_lock)["FeatureType:al_ft"]
    )
    refused = run_cli(*args, "--dry-run", cwd=git_workspace.root)

    assert refused.returncode == 0, refused.stderr
    assert "refused:" in refused.stdout
    assert f"Note: a real run would refuse {instance.slug}." in refused.stderr
    assert refused.stdout.rstrip().endswith("Dry run: nothing was changed.")

    # What the dry run only noted, a real run refuses.
    real = run_cli(*args, "-y", cwd=git_workspace.root)

    assert real.returncode == 1
    assert "has uncommitted changes" in " ".join(real.stderr.split())
    assert target_lock.read_text(encoding="utf-8") == target_text
    assert ln.Feature.get(name="al_ft").uid == target_uid
    assert ln.Run.filter().count() == runs


def test_repair_align_uids_selects_configured_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lamin_cli.evolve._commands import _align_uids_selection
    from lamindb_setup.evolve import engine

    for name in ("plan_a.py", "plan_b.py"):
        (tmp_path / name).write_text("")
    config = tmp_path / "lamin_schemas.yml"
    config.write_text(
        "instances:\n"
        "  - slug: acme/general\n    plans: [plan_a.py:a, plan_b.py:b]\n"
        "  - slug: acme/sandbox\n    plans: [plan_a.py:a]\n"
        "  - slug: acme/other\n    plans: [plan_b.py:b]\n"
    )
    loaded: list[str] = []

    def fake_load(target: str) -> tuple[object, Path, object]:
        loaded.append(target)
        return target, Path(target.partition(":")[0]), object()

    monkeypatch.setattr(engine, "load_plan", fake_load)
    plans, per_instance = _align_uids_selection(
        None,
        "acme/general",
        None,
        load_config(config),
    )

    assert set(per_instance) == {"acme/sandbox", "acme/other"}
    [(sandbox_plan, _path)] = per_instance["acme/sandbox"]
    assert str(sandbox_plan).endswith("plan_a.py:a")
    [(other_plan, _path)] = per_instance["acme/other"]
    assert str(other_plan).endswith("plan_b.py:b")
    assert len(plans) == 2
