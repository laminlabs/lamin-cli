"""Tests for lamindb_setup.evolve.maintenance._stranded_values."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from lamindb_setup.evolve import (
    Feature,
    FeatureType,
    LaminDType,
    drop_values,
    keep_values,
)
from lamindb_setup.evolve.authoring._plan import PlanContext, create_plan
from lamindb_setup.evolve.engine._apply import (
    ApplyMode,
    compute_changes,
    write_lock,
)
from lamindb_setup.evolve.engine._lock import LockFile
from lamindb_setup.evolve.maintenance._stranded_values import (
    compute_stranded_values,
    execute_stranded_values,
    render_stranded_values,
)

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable
    from pathlib import Path
    from types import SimpleNamespace as TestInstanceInfo
    from typing import Any as LamindbModuleType

_KEY = "FeatureType:sv_ft/Feature:sv_drop"


@create_plan
def _sv_v1(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("sv_ft")
    Feature("sv_keep", dtype=LaminDType(str), type=ft)
    Feature("sv_drop", dtype=LaminDType(str), type=ft)


@create_plan
def _sv_v2(ctx: PlanContext, exports: None) -> None:
    """`sv_drop` gone, with its values deliberately kept."""
    ft = FeatureType("sv_ft")
    Feature("sv_keep", dtype=LaminDType(str), type=ft)
    ctx.migrate(_KEY, keep_values())


@create_plan
def _sv_v2_dropped(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("sv_ft")
    Feature("sv_keep", dtype=LaminDType(str), type=ft)
    ctx.migrate(_KEY, drop_values())


def _apply_v1(ln: LamindbModuleType, tmp_path: Path) -> LockFile:
    from lamindb.models import JsonValue

    _sv_v1.reset()
    applied = compute_changes(_sv_v1, mode=ApplyMode.APPLY, lock=None)
    write_lock(applied, _sv_v1, tmp_path / "sv.lock")  # type: ignore[arg-type]
    lock = LockFile.read(tmp_path / "sv.lock")
    assert lock is not None
    feat = ln.Feature.get(name="sv_drop", is_type=False)
    JsonValue(feature=feat, value="a").save()
    JsonValue(feature=feat, value="b").save()
    return lock


def test_repair_stranded_values_dry_run_reports_and_changes_nothing(
    instance: TestInstanceInfo,
    ln: LamindbModuleType,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    tmp_path: Path,
) -> None:
    _apply_v1(ln, tmp_path)
    ln.Feature.get(name="sv_drop", is_type=False).delete()
    runs = ln.Run.filter().count()

    result = run_cli("repair", "stranded-values", "-i", instance.slug, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "sv_drop" in result.stdout
    assert "2 scalar values" in result.stdout
    assert "[y/N]" not in result.stdout
    assert result.stdout.rstrip().endswith("Dry run: nothing was changed.")
    assert ln.Feature.filter(name="sv_drop", branch_id=-1).one().values.count() == 2
    assert ln.Run.filter().count() == runs
