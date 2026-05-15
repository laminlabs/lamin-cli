from __future__ import annotations

import sys
import subprocess
from pathlib import Path

import lamindb as ln
from click.testing import CliRunner

from lamin_cli.__main__ import classify_exec_target, main


def _delete_exec_records(key: str) -> None:
    for run in ln.Run.filter(transform__key=key).all():
        run.delete(permanent=True)
    for transform in ln.Transform.filter(key=key).all():
        transform.delete(permanent=True)


def test_exec_forwards_child_argv(monkeypatch, tmp_path: Path):
    recorded: dict[str, object] = {}

    def fake_run(command, **kwargs):
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("lamin_cli.__main__.subprocess.run", fake_run)

    target = tmp_path / "run-me.py"
    target.write_text("print('hello')\n")

    result = CliRunner().invoke(main, ["exec", str(target), "--flag", "value", "-x"])

    assert result.exit_code == 0, result.output
    assert recorded["command"] == [str(target), "--flag", "value", "-x"]
    assert recorded["kwargs"] == {"check": False}


def test_exec_propagates_child_exit_code(monkeypatch, tmp_path: Path):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr("lamin_cli.__main__.subprocess.run", fake_run)

    target = tmp_path / "run-me.py"
    target.write_text("print('hello')\n")

    result = CliRunner().invoke(main, ["exec", str(target)])

    assert result.exit_code == 17


def test_exec_classifies_local_script_vs_opaque_executable(tmp_path: Path):
    script = tmp_path / "run-me.py"
    script.write_text("print('hello')\n")

    executable = tmp_path / "run-me"
    executable.write_text("#!/bin/sh\necho hello\n")
    executable.chmod(executable.stat().st_mode | 0o111)

    assert classify_exec_target(str(script)) == "script"
    assert classify_exec_target(str(executable)) == "executable"
    assert classify_exec_target("python") == "executable"


def test_exec_creates_and_reuses_source_backed_transform(monkeypatch, tmp_path: Path):
    recorded: list[list[str]] = []

    def fake_run(command, **kwargs):
        recorded.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("lamin_cli.__main__.subprocess.run", fake_run)

    target = tmp_path / "tracked-script.py"
    target.write_text("print('hello from script')\n")

    _delete_exec_records(target.name)
    try:
        result = CliRunner().invoke(main, ["exec", str(target), "--first"])
        assert result.exit_code == 0, result.output

        first_transform = ln.Transform.get(key=target.name)
        first_run = first_transform.latest_run

        assert first_transform.kind == "script"
        assert first_transform.source_code == target.read_text()
        assert first_run.cli_args == f"{target} --first"

        result = CliRunner().invoke(main, ["exec", str(target), "--second"])
        assert result.exit_code == 0, result.output

        second_transform = ln.Transform.get(key=target.name)
        runs = list(ln.Run.filter(transform=second_transform).order_by("started_at"))

        assert second_transform.id == first_transform.id
        assert [run.cli_args for run in runs] == [
            f"{target} --first",
            f"{target} --second",
        ]
        assert recorded == [[str(target), "--first"], [str(target), "--second"]]
    finally:
        _delete_exec_records(target.name)


def test_exec_creates_metadata_only_transform_for_executable_and_captures_version(
    monkeypatch,
):
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command == ["python", "--version"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="Python 3.14.2\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("lamin_cli.__main__.subprocess.run", fake_run)

    _delete_exec_records("python")
    try:
        result = CliRunner().invoke(main, ["exec", "python", "--debug"])

        assert result.exit_code == 0, result.output

        transform = ln.Transform.get(key="python")
        run = transform.latest_run

        assert transform.kind == "pipeline"
        assert transform.source_code is None
        assert transform.description == "python (Python 3.14.2)"
        assert run.cli_args == "python --debug"
        assert calls == [["python", "--version"], ["python", "--debug"]]
    finally:
        _delete_exec_records("python")


def test_exec_persists_exact_cli_args_and_child_status_with_logs(
    monkeypatch,
    tmp_path: Path,
):
    def fake_run(command, **kwargs):
        print("hello stdout from child")
        print("hello stderr from child", file=sys.stderr)
        return subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr("lamin_cli.__main__.subprocess.run", fake_run)

    target = tmp_path / "logged-script.py"
    target.write_text("print('log me')\n")

    _delete_exec_records(target.name)
    try:
        result = CliRunner().invoke(
            main,
            ["exec", str(target), "--message", "two words", "--flag"],
        )

        assert result.exit_code == 17

        transform = ln.Transform.get(key=target.name)
        run = transform.latest_run

        assert run.cli_args == f"{target} --message 'two words' --flag"
        assert run.started_at is not None
        assert run.finished_at is not None
        assert run._status_code == 17
        assert run.report is not None
        logs_path = run.report.cache()
        assert "hello stdout from child" in logs_path.read_text()
        assert "hello stderr from child" in logs_path.read_text()
    finally:
        _delete_exec_records(target.name)