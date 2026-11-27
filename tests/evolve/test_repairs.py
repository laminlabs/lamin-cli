from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from lamin_cli.evolve import _commands
from lamindb_setup.evolve import engine, introspect, maintenance


@pytest.fixture
def tracking(stub):
    stub(_commands, "safe_connect", error=lambda slug: slug.replace("/", "-"))
    stub(introspect, "read_instance")
    stub(engine, "track_operation", nullcontext("run"))
    return stub(engine, "attach_operation_report")


@pytest.mark.parametrize(
    ("command", "compute", "execute", "count", "expected"),
    [
        ("dangling", "prune", "prune", 2, "Pruned 2"),
        ("duplicates", "duplicates", "dedup", 2, "Removed 2"),
        ("dtypes", "verify_dtypes", "verify_dtypes", (1, 2), "Fixed 1"),
        ("stranded-values", "stranded_values", "stranded_values", 2, "Deleted 2"),
        ("sheet-schemas", "sheet_schemas", "sheet_schemas", 2, "Detached 2"),
    ],
)
def test_repair_previews_before_writing(
    command, compute, execute, count, expected, tracking, invoke, stub, ci_config
):
    plan = SimpleNamespace(
        is_empty=True,
        objects=[1],
        features=[1],
        fixable=[1],
        deletable=[],
        manual=[],
        groups=[],
        manual_groups=[1],
        has_changes=True,
        remove_count=2,
        absorb_count=1,
    )
    stub(maintenance, "compute_" + compute, plan)
    stub(maintenance, "compute_content_duplicates", [])
    stub(maintenance, "print_" + execute)
    stub(maintenance, "render_" + execute, "Report")
    write = stub(maintenance, "execute_" + execute, count)
    args = ("repair", command, "-i", "org/target")
    assert "Nothing to do" in invoke(*args).output
    plan.is_empty = False
    if command == "duplicates":
        plan.has_changes = False
        assert "nothing can be repaired" in invoke(*args, code=1).output
        plan.has_changes = True
    if command == "dtypes":
        plan.manual = [1]
        assert "manual resolution" in invoke(*args, "--dry-run").output
        plan.manual = []
    if command == "sheet-schemas":
        plan.fixable = []
        plan.unfixable = [1]
        assert "manual decision" in invoke(*args, code=1).output
        plan.fixable = [1]
    assert "Dry run" in invoke(*args, "--dry-run").output
    assert "Aborted" in invoke(*args, answer="n\n", code=1).output
    write.assert_not_called()
    assert expected in invoke(*args, "--yes", "-c", ci_config).stdout
    write.assert_called_once()
    assert expected in tracking.call_args.args[1]
    assert tracking.call_args.kwargs["instance"] == "org/target"


def test_alignment_target_selection(invoke, ci_config, plan_file):
    args = ("repair", "align-uids", "--source", "org/source")
    for extra, phrase in [
        ([], "pass plan targets"),
        ([plan_file], "name the target"),
        (["-c", ci_config, "-i", "other/db"], "not in"),
        (["-c", ci_config, "--source", "other/db"], "not an instance"),
    ]:
        assert phrase in invoke(*args, *extra, code=2).output
    assert "No target instance" in invoke(*args, plan_file, "-i", "org/source").output
    ci_config.write_text(
        ci_config.read_text()
        .replace("org/target", "other/db")
        .replace("plans: [plan_demo.py:demo]", "plans: [plan_demo.py:other]", 1)
    )
    assert "No target instance" in invoke(*args, "-c", ci_config).output


def test_align_uids_reports_and_confirms_each_target(
    invoke, stub, tracking, ci_config, plan_file
):
    identity = SimpleNamespace(missing_locks=["demo"])
    stub(maintenance, "read_source_identity", identity)
    align = SimpleNamespace(
        is_empty=True,
        ok=True,
        plans=["demo"],
        lock_files=[],
        renames=[1],
        executed=True,
        error="blocked",
    )
    compute = stub(maintenance, "compute_alignment", align)
    stub(maintenance, "collect_instance_locks")
    stub(maintenance, "print_alignment")
    stub(maintenance, "render_alignment", "alignment report")
    execute = stub(maintenance, "execute_alignment")
    rewrite = stub(maintenance, "rewrite_target_locks", ["updated.lock"])
    args = ("repair", "align-uids", "--source", "org/source", "-c", ci_config)
    assert "already matches" in invoke(*args, plan_file).output
    assert compute.call_args.kwargs["instance"] == "org/target"
    align.is_empty = False
    align.ok = False
    assert "real run would refuse" in invoke(*args, "--dry-run").output
    assert "was not changed" in invoke(*args, code=1).output
    align.ok = True
    assert "Dry run" in invoke(*args, "--dry-run").output
    assert "Skipped org/target" in invoke(*args, answer="n\n").output
    rewrite.assert_not_called()
    align.executed = False
    assert "rolled back" in invoke(*args, "-y", code=1).output
    align.executed = True
    assert "Rewrote lock file: updated.lock" in invoke(*args, "-y").output
    rewrite.assert_called_once_with(align)
    assert execute.call_args.args == (align,)
    assert tracking.call_args.kwargs["operation"] == "repair-align-uids"


def test_align_warns_about_uncommitted_locks_only_in_preview(stub, capsys):
    stub(
        engine,
        "ensure_lock_files_committed",
        error=engine.TrackError("uncommitted lock"),
    )
    _commands._ensure_locks_committed([], dry_run=True)
    assert "real run would refuse" in capsys.readouterr().err
    with pytest.raises(engine.TrackError, match="uncommitted lock"):
        _commands._ensure_locks_committed([], dry_run=False)
