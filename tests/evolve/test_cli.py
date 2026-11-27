"""Command-line arguments, output and errors for lamin evolve."""

import json
from pathlib import Path

import pytest
from click import unstyle
from click.testing import CliRunner
from lamin_cli.evolve._commands import app


def test_init_prints_the_workflow_for_the_file_it_wrote(
    run_cli, tmp_path: Path
) -> None:
    # The hint names the file where -d put it, and walks the validate -> plan
    # -> commit and push -> apply flow 'apply' requires, in that order.
    result = run_cli("init", "newthing", "-d", "plans")

    assert result.returncode == 0, result.stderr
    plan_file = Path("plans") / "plan_newthing.py"
    assert (tmp_path / plan_file).exists()
    target = f"{plan_file}:newthing"
    lines = result.stdout.splitlines()
    assert f"Created plan: {plan_file}" in lines
    assert lines[-4:] == [
        "Next steps:",
        f"  lamin evolve validate {target}",
        f"  lamin evolve plan {target} -i <instance>",
        f"  commit and push it, then: lamin evolve apply {target} -i <instance>",
    ]

    # the hint is runnable as printed
    validated = run_cli("validate", target)
    assert validated.returncode == 0, validated.stderr


def test_validate_format_json_emits_a_parseable_document(tmp_path: Path) -> None:
    """--format json used to fall through to the terminal view and write nothing."""

    runner = CliRunner()
    assert runner.invoke(app, ["init", "jsonplan", "-d", str(tmp_path)]).exit_code == 0
    out = tmp_path / "summary.json"

    result = runner.invoke(
        app,
        [
            "validate",
            str(tmp_path / "plan_jsonplan.py"),
            "--format",
            "json",
            "-o",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["plan"]["name"] == "jsonplan"
    assert payload["objects"] == []
    assert payload["declarations"] == []
    # ...and the same document went to stdout
    assert json.loads(result.output)["heading"] == "Plan summary: jsonplan"


def test_utils_generate_stem_uid_checks_every_given_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # --instance is repeatable: a plan applied to dev *and* prod needs a uid
    # that is unclaimed on both, so every one of them reaches the generator.
    from lamindb_setup.evolve import engine

    recorded: dict[str, object] = {}

    def fake_generate_stem_uids(count: int = 1, **kwargs: object) -> list[str]:
        recorded.update(count=count, **kwargs)
        return ["fakeUID00001", "fakeUID00002"][:count]

    # the command reads the name off the engine front door, which binds it once
    # at import -- patching `engine._track` would not reach it
    monkeypatch.setattr(engine, "generate_stem_uids", fake_generate_stem_uids)

    result = CliRunner().invoke(
        app,
        ["utils", "generate-stem-uid", "-i", "acme/dev", "-i", "acme/prod", "-n", "2"],
    )

    assert result.exit_code == 0, result.output
    assert list(recorded["instances"]) == ["acme/dev", "acme/prod"]  # type: ignore[call-overload]
    assert recorded["count"] == 2
    assert result.stdout.split() == ["fakeUID00001", "fakeUID00002"]


@pytest.mark.parametrize(
    "command",
    [
        ("repair", "dangling"),
        ("repair", "duplicates"),
        ("repair", "dtypes"),
        ("repair", "stranded-values"),
        ("repair", "sheet-schemas"),
        ("repair", "align-uids", "plan_x.py", "--source", "acc/src"),
    ],
    ids=lambda command: " ".join(command[:2]),
)
def test_repair_dry_run_cannot_be_combined_with_yes(command: tuple[str, ...]) -> None:
    result = CliRunner().invoke(
        app, [*command, "-i", "acc/inst", "--dry-run", "--yes"], terminal_width=200
    )

    assert result.exit_code == 2
    assert "cannot be combined with --yes" in " ".join(unstyle(result.output).split())


def test_profile_option_writes_html_for_html_suffix(run_cli, tmp_path: Path) -> None:
    report = tmp_path / "profile.html"

    result = run_cli("--profile", str(report), "utils", "generate-stem-uid")

    assert result.returncode == 0, result.stderr
    assert report.read_text().lstrip().startswith("<!DOCTYPE html>")


def test_ci_plan_cli_missing_config() -> None:
    result = CliRunner().invoke(app, ["ci", "plan", "-c", "definitely_missing.yml"])
    assert result.exit_code == 1
    assert "Error" in result.output


def test_log_queries_registers_summary_at_command_exit(invoke, stub, plan_file):
    from lamin_cli.evolve import _commands

    summary = stub(_commands, "echo_query_log_summary")
    assert "score" in invoke("--log-queries", "validate", plan_file).stdout
    summary.assert_called_once_with()


def test_lazy_group_delegates_discovery_and_execution():
    import click
    from lamin_cli.evolve import evolve

    ctx = click.Context(evolve)
    assert "validate" in evolve.list_commands(ctx)
    assert evolve.get_command(ctx, "validate") is app.commands["validate"]
    assert evolve.get_command(ctx, "missing") is None
    with evolve.make_context("evolve", ["utils", "generate-stem-uid"]) as ctx:
        evolve.invoke(ctx)


def test_backend_import_preserves_unrelated_dependency_errors(monkeypatch):
    import builtins

    from lamin_cli.evolve import _backend_app

    original = builtins.__import__
    error = ModuleNotFoundError("missing transitive dependency", name="dependency")

    def import_module(name, *args, **kwargs):
        if name == "_commands":
            raise error
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_module)
    with pytest.raises(ModuleNotFoundError) as caught:
        _backend_app()
    assert caught.value is error
