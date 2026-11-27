from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from lamin_cli.evolve import _commands
from lamindb_setup.evolve import engine
from lamindb_setup.evolve.engine._destroy import ManagingPlan, PlanReference
from lamindb_setup.evolve.engine._lock import LockEntry, LockFile


@pytest.fixture
def destroy_plan(stub):
    target = SimpleNamespace(key="Feature:score")
    plan = Mock(targets=[target], is_safe=False, total_references=2)
    plan.references.return_value = [Mock(describe=lambda: "shared feature")]
    stub(_commands, "safe_connect", "org-target")
    stub(engine, "compute_destroy_group", [plan])
    stub(engine, "merge_destroy_plans", plan)
    stub(engine, "print_destroy")
    stub(engine, "render_destroy", "Destroy preview")
    stub(engine, "managing_plans", [])
    stub(engine, "plan_held_references", [])
    stub(engine, "track_operation", nullcontext("run"))
    execute = stub(engine, "execute_destroy")
    attach = stub(engine, "attach_operation_report")
    return plan, execute, attach


def test_destroy_refusals_do_not_execute(invoke, plan_file, destroy_plan):
    plan, execute, _ = destroy_plan
    args = ("destroy", plan_file, "-i", "org/target")
    for flag in ("--permanent", "--force"):
        assert (
            "cannot be combined"
            in invoke(*args, flag, "--trash-references", code=2).stderr
        )
    for flags, message in [
        (["--permanent"], "Nothing was destroyed"),
        (["-y"], "Nothing was destroyed"),
        (["--force"], "Aborted"),
        (["--trash-references"], "Aborted"),
    ]:
        assert message in invoke(*args, *flags, answer="n\n", code=1).output
    plan.is_safe = True
    assert "Aborted" in invoke(*args, answer="n\n", code=1).output
    plan.targets = []
    assert "Nothing to do" in invoke(*args).stdout
    execute.assert_not_called()


@pytest.mark.parametrize(
    ("flags", "expected"),
    [([], "trashable blocking"), (["--force", "-y"], "Deleted 2 referencing")],
)
def test_destroy_confirms_and_tracks_references(
    invoke, plan_file, destroy_plan, flags, expected
):
    plan, execute, attach = destroy_plan
    args = ("destroy", plan_file, "-i", "org/target")
    lock_path = engine.lock_path(plan_file, "org-target")
    lock = LockFile(plan="demo")
    lock.add(
        LockEntry(key="Feature:score", kind="Feature", name="score", uid="abc123456789")
    )
    lock.write(lock_path)
    result = invoke(*args, *flags, answer="y\n")
    assert expected in result.stdout
    assert not lock_path.exists()
    assert expected in attach.call_args.args[1]
    execute.assert_called_once()
    # Destruction can also find managed objects without a local lock file.
    plan.is_safe = True
    assert "Destroyed 1" in invoke(*args, "-y").stdout


def test_destroy_reports_the_plans_holding_references(
    invoke, stub, plan_file, destroy_plan, monkeypatch
):
    plan, execute, _ = destroy_plan
    monkeypatch.setattr(_commands, "_MAX_LISTED_REFERENCES", 1)
    holders = tuple(
        ManagingPlan(name, name + ".py", plan_file.with_suffix(".lock"))
        for name in ("child", "other")
    )
    ref = PlanReference(
        Mock(describe=lambda: "shared feature"), frozenset({"uid"}), holders
    )
    stub(engine, "plan_held_references", [ref, ref])
    plan.total_references = 3
    args = ("destroy", plan_file, "-i", "org/target", "--permanent")
    err = invoke(*args, code=1).stderr
    assert "Destroy them together" in err and "--permanent" in err
    assert "1 more reference" in err and "1 other reference" in err
    execute.assert_not_called()
    assert "stale" in invoke(*args, "--force", "-y").stderr


def test_pruning_preserves_unrelated_lock_entries(tmp_path):
    lock = LockFile(plan="demo")
    for name in ("score", "label"):
        lock.add(LockEntry(key=name, kind="Feature", name=name, uid="abc123456789"))
    path = tmp_path / "demo.lock"
    lock.write(path)
    assert _commands._prune_lock_after_destroy(lock, path, ["unknown"]) == 0
    assert _commands._prune_lock_after_destroy(lock, path, ["score"]) == 1
    assert set(LockFile.read(path).objects) == {"label"}
