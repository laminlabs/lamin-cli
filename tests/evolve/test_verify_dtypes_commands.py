"""Tests for lamindb_setup.evolve.maintenance._verify_dtypes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from lamindb_setup.evolve.introspect._read import (
    FeatureInfo,
    InstanceContents,
    RecordTypeInfo,
    read_instance,
)
from lamindb_setup.evolve.maintenance._verify_dtypes import (
    compute_verify_dtypes,
    execute_verify_dtypes,
    render_verify_dtypes,
)

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable
    from types import SimpleNamespace as TestInstanceInfo
    from typing import Any as LamindbModuleType


def _record_type(uid: str, name: str) -> RecordTypeInfo:
    return RecordTypeInfo(
        uid=uid,
        name=name,
        description=None,
        type_uid=None,
        schema_uid=None,
    )


# --- pure unit tests on hand-built InstanceContents -------------------------


# --- integration tests against a real instance ------------------------------


@pytest.mark.parametrize(
    ("args", "returncode"), [((), 1), (("--dry-run",), 0)], ids=["real", "dry-run"]
)
def test_repair_dtypes_manual_resolution_refuses_unless_a_dry_run(
    instance: TestInstanceInfo,
    ln: LamindbModuleType,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    args: tuple[str, ...],
    returncode: int,
) -> None:
    bare = ln.Feature(name="verify_bare", dtype="cat[ULabel]").save()
    bare._dtype_str = "cat"
    bare.save()
    # Two live record types share the name the dtype addresses.
    ln.Record(name="VerifyDupRT", is_type=True).save()
    other = ln.Record(name="VerifyDupRT_tmp", is_type=True).save()
    ln.Record.objects.filter(uid=other.uid).update(name="VerifyDupRT")
    ambiguous = ln.Feature(name="verify_ambiguous", dtype="cat[ULabel]").save()
    ambiguous._dtype_str = "cat[Record[VerifyDupRT]]"
    ambiguous.save()
    runs = ln.Run.filter().count()

    result = run_cli("repair", "dtypes", "-i", instance.slug, *args)

    # A dry run reports the refusal a real run makes, and exits 0.
    assert result.returncode == returncode, result.stderr
    assert "verify_ambiguous" in result.stdout
    assert "1 dtype(s) require manual resolution" in result.stderr
    assert ("Dry run: nothing was changed." in result.stdout) == bool(args)
    assert "[y/N]" not in result.stdout
    assert ln.Feature.get(name="verify_bare")._dtype_str == "cat"
    assert ln.Run.filter().count() == runs
