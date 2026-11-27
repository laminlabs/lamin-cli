from dataclasses import dataclass
from types import SimpleNamespace

from lamindb_setup.evolve import ci, engine


def test_ci_plan_and_drift_exit_codes(invoke, stub, ci_config, tmp_path):
    for command, operation, render, flags, attr in [
        ("plan", "run_pr_check", "render_report", ["--fail-on-diff"], "has_changes"),
        ("drift", "run_drift_detection", "render_drift_report", [], "has_drift"),
    ]:
        report = SimpleNamespace(has_errors=False)
        setattr(report, attr, False)
        run = stub(ci, operation, report)
        stub(ci, render, "preview")
        out = tmp_path / command / "report.md"
        args = ("ci", command, "-c", ci_config, "-i", "org/target", "-o", out)
        assert "preview" in invoke(*args, *flags).stdout
        assert out.read_text() == "preview"
        assert run.call_args.kwargs["instances"] == ["org/target"]
        setattr(report, attr, True)
        invoke(*args, *flags, code=1)
        report.has_errors = True
        invoke(*args, code=1)


def test_ci_apply_preserves_completed_sections_on_failure(invoke, stub, ci_config):
    args = ("ci", "apply", "-c", ci_config, "-f", "markdown")
    run = stub(ci, "run_apply", ["completed"])
    assert "completed" in invoke(*args).stdout
    export = engine.SheetExportError(
        SimpleNamespace(error="export failed"), section="completed"
    )
    export.sections = ["completed"]
    for exc in (
        export,
        ci.CIApplyError([("org/target", "failed")], sections=["completed"]),
    ):
        run.side_effect = exc
        assert "completed" in invoke(*args, code=1).stdout
    for exc in (ci.ConfigError("bad config"), engine.TrackError("uncommitted plan")):
        run.side_effect = exc
        assert str(exc) in invoke(*args, code=1).stderr


def test_ci_missing_exports_reports_partial_results(invoke, stub, ci_config):
    @dataclass
    class Report:
        has_missing: bool = True
        has_errors: bool = False

    report = Report()
    run = stub(ci, "run_missing_exports", report)
    stub(ci, "render_missing_exports_report", "missing export")
    args = ("ci", "missing-exports", "-c", ci_config)
    assert "missing export" in invoke(*args, "--fail-on-missing", code=1).stdout
    assert '"has_missing": true' in invoke(*args, "-f", "json").stdout
    report.has_errors = True
    assert "could not reconstruct" in invoke(*args, code=1).stderr
    run.side_effect = ci.ConfigError("bad exports config")
    assert "bad exports config" in invoke(*args, code=1).stderr


def test_verify_config_writes_report_even_when_invalid(
    invoke, stub, ci_config, tmp_path
):
    out = tmp_path / "report.md"
    report = SimpleNamespace(ok=True)
    stub(ci, "verify_config", report)
    stub(ci, "render_verify_report", "checked")
    args = ("ci", "verify-config", "-c", ci_config, "-o", out)
    invoke(*args)
    assert out.read_text() == "checked"
    report.ok = False
    assert "checked" in invoke(*args, code=1).stdout
    ci_config.unlink()
    assert "Error" in invoke(*args, code=1).stderr
