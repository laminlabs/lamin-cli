from __future__ import annotations

import subprocess
from types import SimpleNamespace
from pathlib import Path

import lamindb as ln
import click
import pytest
from click.testing import CliRunner

from lamin_cli.__main__ import (
    classify_exec_target,
    main,
    parse_lamin_exec_uri,
    rewrite_exec_argv,
)


@pytest.fixture(scope="module", autouse=True)
def connected_instance(tmp_path_factory):
    if ln.setup.settings._instance_exists:
        yield
        return

    storage = tmp_path_factory.mktemp("lamin-cli-exec-storage")
    ln.setup.init(storage=str(storage), name="lamin-cli-exec-tests")
    yield


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


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        (
            "lamin://laminlabs/demo/artifact/1234567890abcdef/path/to/data.csv",
            ("laminlabs/demo", "1234567890abcdef", Path("path/to/data.csv")),
        ),
        (
            "lamin://laminlabs/demo/artifact/1234567890abcdefghij",
            ("laminlabs/demo", "1234567890abcdefghij", None),
        ),
    ],
)
def test_parse_lamin_exec_uri(uri: str, expected: tuple[str, str, Path | None]):
    assert parse_lamin_exec_uri(uri) == expected


@pytest.mark.parametrize(
    "uri",
    [
        "https://lamin.ai/laminlabs/demo/artifact/1234567890abcdef",
        "lamin://laminlabs/demo/transform/1234567890abcdef",
        "lamin://laminlabs/demo/artifact/not-a-valid-uid",
        "lamin://laminlabs/demo/artifact/1234567890abcdef/",
    ],
)
def test_parse_lamin_exec_uri_rejects_invalid_inputs(uri: str):
    with pytest.raises(click.BadParameter):
        parse_lamin_exec_uri(uri)


def test_rewrite_exec_argv_replaces_lamin_uri_with_cached_path(monkeypatch, tmp_path: Path):
    cache_path = tmp_path / "artifact-cache"
    artifact = SimpleNamespace(cache=lambda: cache_path)

    monkeypatch.setattr("lamin_cli.__main__._load_exec_artifact", lambda instance, uid: artifact)

    argv = [
        "lamin://laminlabs/demo/artifact/1234567890abcdef/path/to/data.csv",
        "--flag",
        "value",
        "lamin://laminlabs/demo/artifact/1234567890abcdefghij",
    ]

    assert rewrite_exec_argv(argv) == [
        str(cache_path / "path/to/data.csv"),
        "--flag",
        "value",
        str(cache_path),
    ]


def test_exec_rewrites_lamin_uri_before_launch(monkeypatch, tmp_path: Path):
    recorded: dict[str, object] = {}

    cache_path = tmp_path / "artifact-cache"
    artifact = SimpleNamespace(cache=lambda: cache_path)

    def fake_run(command, **kwargs):
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("lamin_cli.__main__._load_exec_artifact", lambda instance, uid: artifact)
    monkeypatch.setattr("lamin_cli.__main__.subprocess.run", fake_run)

    target = "lamin://laminlabs/demo/artifact/1234567890abcdef/path/to/script.py"
    rewritten_target = cache_path / "path/to/script.py"
    rewritten_target.parent.mkdir(parents=True, exist_ok=True)
    rewritten_target.write_text("print('cached script')\n")

    result = CliRunner().invoke(main, ["exec", target, "--input", target])

    assert result.exit_code == 0, result.output
    assert recorded["command"] == [
        str(cache_path / "path/to/script.py"),
        "--input",
        str(cache_path / "path/to/script.py"),
    ]
    assert recorded["kwargs"] == {"check": False}