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
    _probe_exec_version,
    parse_mount_storage_mappings,
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


def test_probe_exec_version_times_out(monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, timeout=kwargs["timeout"])

    monkeypatch.setattr("lamin_cli.__main__.subprocess.run", fake_run)

    assert _probe_exec_version("python") is None


def test_exec_returns_clean_exit_code_for_missing_executable(monkeypatch):
    def fake_run(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr("lamin_cli.__main__._probe_exec_version", lambda executable: None)
    monkeypatch.setattr("lamin_cli.__main__.subprocess.run", fake_run)

    _delete_exec_records("missing-command")
    try:
        result = CliRunner().invoke(main, ["exec", "missing-command"])

        assert result.exit_code == 127
    finally:
        _delete_exec_records("missing-command")


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
        "lamin://laminlabs/demo/artifact/1234567890abc-de",
        "lamin://laminlabs/demo/artifact/1234567890abcdef/",
        "lamin://laminlabs/demo/artifact/1234567890abcde!/path/to/data.csv",
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


def test_rewrite_exec_argv_prefers_mounted_storage_path_when_present(
    monkeypatch, tmp_path: Path
):
    storage_root = tmp_path / "storage-root"
    artifact_path = storage_root / "dataset" / "input.csv"
    mount_root = tmp_path / "mount-root"
    mounted_path = mount_root / "dataset" / "input.csv"
    mounted_path.parent.mkdir(parents=True, exist_ok=True)
    mounted_path.write_text("mounted")
    cache_path = tmp_path / "artifact-cache"

    artifact = SimpleNamespace(
        path=artifact_path,
        storage=SimpleNamespace(root=storage_root),
        cache=lambda: cache_path,
    )

    monkeypatch.setattr("lamin_cli.__main__._load_exec_artifact", lambda instance, uid: artifact)

    argv = ["lamin://laminlabs/demo/artifact/1234567890abcdef"]

    assert rewrite_exec_argv(
        argv,
        parse_mount_storage_mappings((f"{storage_root}={mount_root}",)),
    ) == [str(mounted_path)]


def test_rewrite_exec_argv_falls_back_to_cache_when_mount_path_missing(
    monkeypatch, tmp_path: Path
):
    storage_root = tmp_path / "storage-root"
    artifact_path = storage_root / "dataset" / "input.csv"
    mount_root = tmp_path / "mount-root"
    cache_path = tmp_path / "artifact-cache"

    artifact = SimpleNamespace(
        path=artifact_path,
        storage=SimpleNamespace(root=storage_root),
        cache=lambda: cache_path,
    )

    monkeypatch.setattr("lamin_cli.__main__._load_exec_artifact", lambda instance, uid: artifact)

    argv = ["lamin://laminlabs/demo/artifact/1234567890abcdef"]

    assert rewrite_exec_argv(
        argv,
        parse_mount_storage_mappings((f"{storage_root}={mount_root}",)),
    ) == [str(cache_path)]


def test_rewrite_exec_argv_applies_optional_subpath_to_mounted_roots(
    monkeypatch, tmp_path: Path
):
    storage_root = tmp_path / "storage-root"
    artifact_path = storage_root / "dataset"
    mount_root = tmp_path / "mount-root"
    mounted_path = mount_root / "dataset" / "nested" / "input.csv"
    mounted_path.parent.mkdir(parents=True, exist_ok=True)
    mounted_path.write_text("mounted")
    cache_path = tmp_path / "artifact-cache"

    artifact = SimpleNamespace(
        path=artifact_path,
        storage=SimpleNamespace(root=storage_root),
        cache=lambda: cache_path,
    )

    monkeypatch.setattr("lamin_cli.__main__._load_exec_artifact", lambda instance, uid: artifact)

    argv = ["lamin://laminlabs/demo/artifact/1234567890abcdef/nested/input.csv"]

    assert rewrite_exec_argv(
        argv,
        parse_mount_storage_mappings((f"{storage_root}={mount_root}",)),
    ) == [str(mounted_path)]


def test_rewrite_exec_argv_supports_repeated_mount_mappings(monkeypatch, tmp_path: Path):
    first_storage_root = tmp_path / "storage-root-a"
    second_storage_root = tmp_path / "storage-root-b"
    artifact_path = second_storage_root / "dataset" / "input.csv"
    first_mount_root = tmp_path / "mount-root-a"
    second_mount_root = tmp_path / "mount-root-b"
    mounted_path = second_mount_root / "dataset" / "input.csv"
    mounted_path.parent.mkdir(parents=True, exist_ok=True)
    mounted_path.write_text("mounted")
    cache_path = tmp_path / "artifact-cache"

    artifact = SimpleNamespace(
        path=artifact_path,
        storage=SimpleNamespace(root=second_storage_root),
        cache=lambda: cache_path,
    )

    monkeypatch.setattr("lamin_cli.__main__._load_exec_artifact", lambda instance, uid: artifact)

    argv = ["lamin://laminlabs/demo/artifact/1234567890abcdef"]

    assert rewrite_exec_argv(
        argv,
        parse_mount_storage_mappings(
            (
                f"{first_storage_root}={first_mount_root}",
                f"{second_storage_root}={second_mount_root}",
            )
        ),
    ) == [str(mounted_path)]


def test_rewrite_exec_argv_supports_s3_mount_storage_mappings(
    monkeypatch, tmp_path: Path
):
    storage_root = "s3://bucket/prefix"
    artifact_path = "s3://bucket/prefix/dataset/input.csv"
    mount_root = tmp_path / "mount-root"
    mounted_path = mount_root / "dataset" / "input.csv"
    mounted_path.parent.mkdir(parents=True, exist_ok=True)
    mounted_path.write_text("mounted")
    cache_path = tmp_path / "artifact-cache"

    artifact = SimpleNamespace(
        path=artifact_path,
        storage=SimpleNamespace(root=storage_root),
        cache=lambda: cache_path,
    )

    monkeypatch.setattr("lamin_cli.__main__._load_exec_artifact", lambda instance, uid: artifact)

    argv = ["lamin://laminlabs/demo/artifact/1234567890abcdef"]

    assert rewrite_exec_argv(
        argv,
        parse_mount_storage_mappings((f"{storage_root}={mount_root}",)),
    ) == [str(mounted_path)]


def test_exec_mount_storage_option_rewrites_target_before_launch(
    monkeypatch, tmp_path: Path
):
    recorded: dict[str, object] = {}

    storage_root = "s3://bucket/prefix"
    artifact_path = "s3://bucket/prefix/dataset/script.py"
    mount_root = tmp_path / "mount-root"
    mounted_path = mount_root / "dataset" / "script.py"
    mounted_path.parent.mkdir(parents=True, exist_ok=True)
    mounted_path.write_text("print('mounted script')\n")
    artifact = SimpleNamespace(
        path=artifact_path,
        storage=SimpleNamespace(root=storage_root),
        cache=lambda: tmp_path / "artifact-cache",
    )

    def fake_run(command, **kwargs):
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("lamin_cli.__main__._load_exec_artifact", lambda instance, uid: artifact)
    monkeypatch.setattr("lamin_cli.__main__.subprocess.run", fake_run)

    result = CliRunner().invoke(
        main,
        [
            "exec",
            "--mount-storage",
            f"{storage_root}={mount_root}",
            "lamin://laminlabs/demo/artifact/1234567890abcdef",
        ],
    )

    assert result.exit_code == 0, result.output
    assert recorded["command"] == [str(mounted_path)]
    assert recorded["kwargs"] == {"check": False}


@pytest.mark.parametrize("mapping", ["missing-separator", "=mount-root", "storage-root="])
def test_parse_mount_storage_mappings_rejects_invalid_syntax(mapping: str):
    with pytest.raises(click.BadParameter):
        parse_mount_storage_mappings((mapping,))


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