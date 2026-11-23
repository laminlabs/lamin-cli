"""Tests for lamindb_setup.evolve.maintenance._dedup."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from lamindb_setup.evolve import Feature, FeatureType, LaminDType, Schema
from lamindb_setup.evolve.authoring._plan import PlanContext, create_plan
from lamindb_setup.evolve.engine._apply import ApplyMode, compute_changes
from lamindb_setup.evolve.engine._lock import LockEntry, LockFile, lock_path
from lamindb_setup.evolve.introspect._read import (
    FeatureInfo,
    FeatureTypeInfo,
    InstanceContents,
    SchemaInfo,
    read_instance,
)
from lamindb_setup.evolve.maintenance._dedup import (
    CONTENT_ABSORB,
    CONTENT_KEEP,
    CONTENT_MANUAL,
    ContentMember,
    DuplicatePlan,
    compute_content_duplicates,
    compute_duplicates,
    execute_dedup,
    render_dedup,
)

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable
    from types import SimpleNamespace as TestInstanceInfo
    from typing import Any as LamindbModuleType

    from lamindb_setup.evolve.maintenance._dedup import (
        ContentDuplicateGroup,
    )


def _feature(uid: str, name: str, **kwargs: object) -> FeatureInfo:
    defaults: dict[str, object] = {"type_uid": None, "dtype_str": "str"}
    defaults.update(kwargs)
    return FeatureInfo(uid=uid, name=name, **defaults)  # type: ignore[arg-type]


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


# --- rendering --------------------------------------------------------------


# --- integration tests against a real instance ------------------------------


@create_plan
def _dedup_seed_plan(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("dedup_ft")
    f = Feature("dedup_col", dtype=LaminDType(str), type=ft)
    Schema(name="DedupSchema", features=[f])


def test_repair_duplicates_config_records_the_run_in_the_track_space(
    instance: TestInstanceInfo,
    ln: LamindbModuleType,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    tmp_path: Path,
) -> None:
    ln.Feature(name="dup_cfg", dtype="str").save()
    tmp = ln.Feature(name="dup_cfg_tmp", dtype="str").save()
    ln.Feature.objects.filter(uid=tmp.uid).update(name="dup_cfg")
    space = ln.Space(name="RepairMaintenance", uid="spaceUID0275").save()
    # An artifact saved in a space needs a storage location managed through it;
    # `Storage.save` would ask the hub, so the location is moved in place.
    ln.Storage.filter(id=ln.settings.storage.record.id).update(space=space)
    (tmp_path / "plan_x.py").write_text("", encoding="utf-8")
    (tmp_path / "lamin_schemas.yml").write_text(
        "track_prefix: org/schemas\n"
        f"instances:\n  - slug: {instance.slug}\n    plans: [plan_x.py]\n"
        "    track_space: RepairMaintenance\n",
        encoding="utf-8",
    )

    result = run_cli(
        "repair", "duplicates", "-i", instance.slug, "-y", "-c", "lamin_schemas.yml"
    )

    assert result.returncode == 0, result.stderr
    assert "Removed 1 duplicate object(s) (to trash)." in result.stdout
    assert ln.Feature.filter(name="dup_cfg").count() == 1
    run = ln.Run.get(transform__key="org/schemas/repair-duplicates")
    assert run.params["yes"] is True
    report = run.report
    assert report.description.startswith("repair-duplicates report")
    assert report.key == (
        f"org/schemas/repair-duplicates-reports/{instance.slug.replace('/', '-')}.txt"
    )
    for record in (run, run.transform, report):
        assert record.space.name == "RepairMaintenance"


# --- schemas sharing one content hash ----------------------------------------


@pytest.fixture
def writable_storage(ln: LamindbModuleType) -> None:
    """Point the cached instance's `Storage` row at where the run actually is."""
    import lamindb_setup as ln_setup

    storage = ln_setup.settings.storage
    root = storage.root_as_str
    ln.Storage.objects.filter(uid=Path(root).name).update(root=root)
    storage._record = None


def _curated(ln: LamindbModuleType, key: str, *columns: str) -> tuple[Any, Any]:
    """An artifact curated against a validator, and its inferred columns schema.

    The validator makes every column optional, so lamindb records the columns
    the frame carries -- all required -- as a new, unnamed schema.
    """
    import pandas as pd
    from lamindb.models.schema import ArtifactSchema

    features = [
        ln.Feature.filter(name=name).one_or_none()
        or ln.Feature(name=name, dtype=str).save()
        for name in ("cd_a", "cd_b", "cd_c")
    ]
    validator = ln.Schema(
        name="CdValidator", features=[f.with_config(optional=True) for f in features]
    ).save()
    frame = pd.DataFrame({column: ["x"] for column in columns})
    artifact = ln.Artifact.from_dataframe(frame, key=key, schema=validator).save()
    inferred = ArtifactSchema.objects.get(artifact=artifact, slot="columns").schema
    assert inferred.name is None
    return artifact, inferred


def _share_content(ln: LamindbModuleType, schema: Any, holder: Any) -> None:
    """Give `schema` the content hash of `holder`, as lamindb < 2.12 could."""
    ln.Schema.objects.filter(pk=schema.pk).update(hash=holder.hash)


def _actions(group: ContentDuplicateGroup) -> list[tuple[str, str]]:
    return [(member.uid, member.action) for member in group.members]


def _slug(instance: TestInstanceInfo) -> str:
    """The slug `safe_connect` names the instance's lock files with."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", instance.slug).strip("-._")


def test_repair_duplicates_absorbs_inferred_schemas_sharing_a_content(
    instance: TestInstanceInfo,
    ln: LamindbModuleType,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
    tmp_path: Path,
    writable_storage: None,
) -> None:
    _, older = _curated(ln, "cd/one.parquet", "cd_a", "cd_b")
    _, newer = _curated(ln, "cd/two.parquet", "cd_a", "cd_c")
    _share_content(ln, newer, older)
    # A plan records the newer one, so it is kept over the older.
    (tmp_path / "plan_x.py").write_text("", encoding="utf-8")
    (tmp_path / "lamin_schemas.yml").write_text(
        f"instances:\n  - slug: {instance.slug}\n    plans: [plan_x.py]\n",
        encoding="utf-8",
    )
    lock = LockFile(plan="x")
    lock.add(LockEntry(key="Schema:x", kind="Schema", name=None, uid=newer.uid))
    lock.write(lock_path(tmp_path / "plan_x.py", _slug(instance)))

    preview = run_cli(
        "repair",
        "duplicates",
        "-i",
        instance.slug,
        "--dry-run",
        "-c",
        "lamin_schemas.yml",
    )
    assert preview.returncode == 0, preview.stderr
    assert f"absorb {older.uid}" in preview.stdout
    assert f"keep   {newer.uid}" in preview.stdout
    assert ln.Schema.objects.filter(pk=older.pk).exists()

    result = run_cli(
        "repair", "duplicates", "-i", instance.slug, "-y", "-c", "lamin_schemas.yml"
    )

    assert result.returncode == 0, result.stderr
    assert (
        "Absorbed 1 inferred schema(s) into the schema sharing their content."
        in result.stdout
    )
    assert "Removed" not in result.stdout
    assert not ln.Schema.objects.filter(pk=older.pk).exists()
    assert ln.Schema.objects.filter(pk=newer.pk).exists()


def test_repair_duplicates_refuses_when_only_a_decision_by_hand_is_left(
    instance: TestInstanceInfo,
    ln: LamindbModuleType,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    first = ln.Schema(
        name="CdFirst", features=[ln.Feature(name="cd_one", dtype=str).save()]
    ).save()
    second = ln.Schema(
        name="CdSecond", features=[ln.Feature(name="cd_two", dtype=str).save()]
    ).save()
    _share_content(ln, second, first)

    result = run_cli("repair", "duplicates", "-i", instance.slug, "-y")

    assert result.returncode == 1
    assert (
        "1 group(s) of schemas sharing their content need a decision by hand; "
        "nothing can be repaired automatically."
    ) in result.stderr
    assert ln.Schema.filter(name__in=["CdFirst", "CdSecond"]).count() == 2


def test_repair_duplicates_notes_a_decision_by_hand_left_beside_its_changes(
    instance: TestInstanceInfo,
    ln: LamindbModuleType,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    first = ln.Schema(
        name="CdFirst", features=[ln.Feature(name="cd_one", dtype=str).save()]
    ).save()
    second = ln.Schema(
        name="CdSecond", features=[ln.Feature(name="cd_two", dtype=str).save()]
    ).save()
    _share_content(ln, second, first)
    ln.Feature(name="cd_dup", dtype="str").save()
    tmp = ln.Feature(name="cd_dup_tmp", dtype="str").save()
    ln.Feature.objects.filter(uid=tmp.uid).update(name="cd_dup")

    result = run_cli("repair", "duplicates", "-i", instance.slug, "-y")

    assert result.returncode == 0, result.stderr
    assert (
        "Note: 1 group(s) of schemas sharing their content need a decision by "
        "hand and are left as they are."
    ) in result.stderr
    assert "Removed 1 duplicate object(s) (to trash)." in result.stdout
    assert ln.Feature.filter(name="cd_dup").count() == 1
