"""Tests for lamindb_setup.evolve.maintenance._prune."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lamindb_setup.evolve import Feature, FeatureType, LaminDType, Schema
from lamindb_setup.evolve.authoring._plan import PlanContext, create_plan
from lamindb_setup.evolve.engine._apply import ApplyMode, compute_changes
from lamindb_setup.evolve.introspect._read import (
    FeatureInfo,
    FeatureTypeInfo,
    InstanceContents,
    ObjectMeta,
    RecordTypeInfo,
    SchemaInfo,
    read_instance,
)
from lamindb_setup.evolve.maintenance._prune import (
    compute_prune,
    execute_prune,
    render_prune,
)

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable
    from types import SimpleNamespace as TestInstanceInfo
    from typing import Any as LamindbModuleType


def _schema(uid: str, name: str | None, **kwargs: object) -> SchemaInfo:
    defaults: dict[str, object] = {
        "description": None,
        "type_uid": None,
        "itype": None,
        "otype": "DataFrame",
        "schema_dtype": None,
        "flexible": None,
        "coerce": None,
        "minimal_set": True,
        "ordered_set": False,
        "maximal_set": False,
    }
    defaults.update(kwargs)
    return SchemaInfo(uid=uid, name=name, **defaults)  # type: ignore[arg-type]


# --- pure unit tests on hand-built InstanceContents -------------------------


# --- integration tests against a real instance ------------------------------


@create_plan
def _prune_seed_plan(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("prune_ft")
    f = Feature("prune_col", dtype=LaminDType(str), type=ft)
    Schema(name="PruneSchema", features=[f])


def test_repair_dangling_dry_run_reports_and_changes_nothing(
    instance: TestInstanceInfo,
    ln: LamindbModuleType,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    compute_changes(_prune_seed_plan, mode=ApplyMode.APPLY, lock=None)
    ln.Feature.filter(name="prune_ft", is_type=True).one().delete()
    runs = ln.Run.filter().count()

    result = run_cli("repair", "dangling", "-i", instance.slug, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "PruneSchema" in result.stdout
    assert "[y/N]" not in result.stdout
    assert result.stdout.rstrip().endswith("Dry run: nothing was changed.")

    assert ln.Schema.filter(name="PruneSchema").one_or_none() is not None
    assert ln.Run.filter().count() == runs

    declined = run_cli("repair", "dangling", "-i", instance.slug, input="n\n")

    assert declined.returncode == 1
    assert "Aborted." in declined.stdout
    assert "Dry run" not in declined.stdout
    assert ln.Schema.filter(name="PruneSchema").one_or_none() is not None
    assert ln.Run.filter().count() == runs


def test_repair_dangling_keeps_its_report_as_the_run_report(
    instance: TestInstanceInfo,
    ln: LamindbModuleType,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    from lamindb_setup.evolve.engine import TrackConfig

    compute_changes(_prune_seed_plan, mode=ApplyMode.APPLY, lock=None)
    ln.Feature.filter(name="prune_ft", is_type=True).one().delete()

    result = run_cli("repair", "dangling", "-i", instance.slug, "-y")

    assert result.returncode == 0, result.stderr
    assert "Pruned 1 object(s) (to trash)." in result.stdout
    # `ln.finish` no longer takes `run.report` over, nor says so.
    assert "overwriting run.report" not in result.stdout + result.stderr
    config = TrackConfig.resolve()
    run = ln.Run.get(transform__key=config.key_for("repair-dangling"))
    assert run.status == "completed"
    slug = instance.slug.replace("/", "-")
    assert run.report.key == f"{config.prefix}/repair-dangling-reports/{slug}.txt"
    assert run.report.description == f"repair-dangling report for {instance.slug}"
    report = run.report.cache().read_text()
    assert "PruneSchema" in report
    assert report.endswith("Pruned 1 object(s) (to trash).\n")
    # The run's logs are one of its outputs.
    (logs,) = ln.Artifact.filter(run=run, kind="__lamindb_run__")
    assert logs.description == f"log streams of run {run.uid}"
    assert f"Run('{run.uid}'" in logs.cache().read_text()
