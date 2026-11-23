"""Tests for lamindb_setup.evolve.maintenance._sheet_schemas."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from lamindb_setup.evolve import Feature, LaminDType, RecordType, Schema, Sheet
from lamindb_setup.evolve.authoring._plan import PlanContext, create_plan
from lamindb_setup.evolve.engine._apply import ApplyMode, compute_changes
from lamindb_setup.evolve.introspect._read import (
    InstanceContents,
    RecordTypeInfo,
    SchemaTypeInfo,
    read_instance,
)
from lamindb_setup.evolve.introspect._reverse import build_plan
from lamindb_setup.evolve.maintenance._sheet_schemas import (
    compute_sheet_schemas,
    execute_sheet_schemas,
    render_sheet_schemas,
)

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable
    from types import SimpleNamespace as TestInstanceInfo
    from typing import Any as LamindbModuleType


def _contents(*, feature_uids: list[str] | None = None) -> InstanceContents:
    return InstanceContents(
        schema_types=[
            SchemaTypeInfo(
                uid="ust1",
                name="MySchemas",
                description=None,
                type_uid=None,
                feature_uids=feature_uids or [],
            )
        ],
        record_types=[
            RecordTypeInfo(
                uid="urt1",
                name="Experiments",
                description=None,
                type_uid=None,
                schema_uid="ust1",
            ),
        ],
    )


# --- unit tests (no instance) -----------------------------------------------


# --- integration tests (against an instance) ---------------------------------


@create_plan
def _seed_plan(ctx: PlanContext, exports: None) -> None:
    schema = Schema(name="ShSheet", features=[Feature("sh_col", dtype=LaminDType(str))])
    Sheet("ShExperiments", schema=schema, type=RecordType("ShRecords"))


def _degenerate_sheet(ln: LamindbModuleType) -> None:
    """Point the seeded sheet at a schema type, as lamindb's FK allows."""
    compute_changes(_seed_plan, mode=ApplyMode.APPLY, lock=None)
    schema_type = ln.Schema(name="ShSchemas", is_type=True).save()
    sheet = ln.Record.get(name="ShExperiments", is_type=True)
    sheet.schema = schema_type
    sheet.save()


@pytest.mark.parametrize(
    ("args", "returncode"), [((), 1), (("--dry-run",), 0)], ids=["real", "dry-run"]
)
def test_repair_sheet_schemas_reports_a_manual_decision_on_stderr(
    instance: TestInstanceInfo,
    ln: LamindbModuleType,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    args: tuple[str, ...],
    returncode: int,
) -> None:
    # The sheet's schema is a schema mislabeled as a type: it carries members,
    # so nothing can be detached automatically.
    compute_changes(_seed_plan, mode=ApplyMode.APPLY, lock=None)
    ln.Schema.filter(name="ShSheet").update(is_type=True)
    runs = ln.Run.filter().count()

    result = run_cli("repair", "sheet-schemas", "-i", instance.slug, *args)

    assert result.returncode == returncode, result.stderr
    assert "ShExperiments" in result.stdout
    message = "1 record type(s) need a manual decision"
    assert message in result.stderr
    assert message not in result.stdout
    assert ("Dry run: nothing was changed." in result.stdout) == bool(args)
    assert "[y/N]" not in result.stdout
    sheet = ln.Record.get(name="ShExperiments", is_type=True)
    assert sheet.schema.name == "ShSheet"
    assert ln.Run.filter().count() == runs
