from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from lamin_cli.__main__ import classify_exec_target, main


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