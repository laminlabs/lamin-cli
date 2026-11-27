import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from lamin_cli.evolve import _commands
from lamindb_setup.evolve import core, engine, introspect, render


def test_validate_all_plans_and_invalid_targets(invoke, plan_file, monkeypatch):
    monkeypatch.chdir(plan_file.parent)
    plan_file = plan_file.name
    assert "score" in invoke("validate", plan_file, "--all-plans").stdout
    for args, expected in [
        ([str(plan_file) + ":demo", "--all-plans"], "explicit"),
        ([plan_file, plan_file, "--all-plans"], "more than once"),
        ([str(plan_file) + ":absent"], "not found"),
        ([plan_file, "-o", "unused.md"], "needs a format"),
    ]:
        assert expected in invoke("validate", *args, code=2).output


def test_expected_backend_errors_can_show_tracebacks(
    monkeypatch, stub, invoke, plan_file
):
    stub(_commands, "load_all_plans", error=core.LaminSchemasError("backend failure"))
    assert "Error: backend failure" in invoke("validate", plan_file, code=1).stderr
    monkeypatch.setenv(_commands.TRACEBACK_ENV_VAR, "1")
    with pytest.raises(core.LaminSchemasError, match="backend failure"):
        invoke("validate", plan_file)


def test_init_rejects_overwrite_and_can_add_missing_uid(invoke, plan_file):
    assert (
        "already exists"
        in invoke("init", "demo", "-d", plan_file.parent, code=1).stderr
    )
    plan_file.write_text(
        plan_file.read_text().replace('PLAN_TRANSFORM_STEM_UID = "testUID00001"', "")
    )
    assert (
        "Added PLAN_TRANSFORM_STEM_UID" in invoke("utils", "add-uid", plan_file).stdout
    )
    assert "PLAN_TRANSFORM_STEM_UID" in plan_file.read_text()


def test_fingerprint_compares_baseline_and_reports_counts(
    invoke, plan_file, tmp_path, capsys
):
    args = ("utils", "fingerprint", plan_file)
    baseline = tmp_path / "base.json"
    original = json.loads(invoke(*args).stdout)
    invoke(*args, "-o", baseline)
    assert json.loads(baseline.read_text()) == original
    assert "IDENTICAL" in invoke(*args, "--baseline", baseline).stdout
    (key,) = original
    name, config = key.split("\n")
    # Include old entries with malformed and non-object payloads.
    baseline.write_text(
        json.dumps({name + "\n{}": 2, "Legacy\nbad": 1, "Legacy\n[]": 1})
    )
    result = invoke(
        *args, "--baseline", baseline, "-o", tmp_path / "current.json", code=1
    )
    assert "CHANGED" in result.stdout and "MISSING" in result.stdout
    baseline.write_text("broken")
    assert (
        "could not read baseline"
        in invoke(*args, "--baseline", baseline, code=1).stderr
    )
    from collections import Counter

    # Two current declarations with the same key leave one extra after pairing.
    _commands._echo_fingerprint_drift(Counter({name + "\n{}": 1}), Counter({key: 2}))
    assert "EXTRA x1" in " ".join(capsys.readouterr().out.split())


def test_plan_renders_backend_refusals(invoke, stub, plan_file):
    stub(_commands, "safe_connect", "org-target")
    result = SimpleNamespace(
        changes=[],
        missing_prerequisites=["base"],
        suspected_renames=["rename"],
        move_outcome=None,
        uid_misalignments=[],
    )
    stub(engine, "compute_changes", result)
    preview = stub(engine, "preview_changes", result)
    stub(engine, "rehearsal", nullcontext())
    stub(render, "render_move_outcome", ["moved"])
    stub(render, "render_uid_misalignments", [])
    stub(render, "unmoved_rename_refusal", "declare the rename")
    stub(render, "missing_prerequisite_message", "apply base")
    stub(render, "print_changes")
    stub(render, "render_changes_markdown", "planned")
    stub(render, "render_changes_json", '{"planned":true}')
    args = ("plan", plan_file, "-i", "org/target")
    terminal = invoke(*args, code=1)
    assert "moved" in terminal.stdout and "apply base" in terminal.stderr
    assert "declare the rename" in terminal.stderr
    assert "planned" in invoke(*args, "-f", "markdown", code=1).stdout
    assert (
        "planned" in invoke(*args, "--preview-migrations", "-f", "json", code=1).stdout
    )
    preview.assert_called_once()


def test_apply_reports_preflight_abort_and_partial_export(
    invoke, stub, plan_file, ci_config
):
    args = ("apply", plan_file, "-i", "org/target", "-c", ci_config, "-f", "markdown")
    verify = stub(
        engine,
        "ensure_all_committed_and_pushed",
        error=engine.TrackError("unpublished"),
    )
    assert "unpublished" in invoke(*args, code=1).stderr
    verify.side_effect = None
    stub(_commands, "safe_connect", "org-target")
    apply = stub(engine, "apply_one", error=engine.ApplyAbortedError("declined"))
    assert "Aborted" in invoke(*args, code=1).stderr
    apply.side_effect = engine.SheetExportError(
        SimpleNamespace(error="failed"), section="committed changes"
    )
    assert "committed changes" in invoke(*args, code=1).stdout


def test_introspection_reports_verification_outcome(invoke, stub, tmp_path):
    result = SimpleNamespace(
        output=tmp_path / "plan_demo.py",
        reconstructed=2,
        func="demo",
        stem_uid="planUID",
        skipped_unnamed_schemas=1,
        errors=["invalid feature"],
        warnings=["ambiguous label"],
        verification={"error": "verification failed"},
        verification_failed=True,
        actionable_changes=1,
        ok=False,
    )
    operation = stub(introspect, "introspect_instance", result)
    args = ("introspect", "demo", "-i", "org/target", "-d", tmp_path)
    failed = invoke(*args, code=1)
    assert "verification failed" in failed.stderr and "invalid feature" in failed.stderr
    assert "ambiguous label" in failed.stderr and "skipped 1" in failed.stdout
    result.verification_failed = False
    result.verification = {"changes": [{"action": "create", "object": "score"}]}
    assert "create: score" in invoke(*args, code=1).stderr
    result.ok = True
    result.actionable_changes = 0
    assert "apply with:" in invoke(*args).stdout
    operation.side_effect = FileExistsError("existing plan")
    assert "existing plan" in invoke(*args, code=1).stderr
