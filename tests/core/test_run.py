from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from lamin_cli import _run, _uri
from lamin_cli._uri import (
    InvalidLaminUri,
    UnresolvableLaminUri,
    parse_lamin_uri,
    resolve_lamin_uri,
)

UID16 = "3TrLu3AbQx9dZq2K"
UID20 = "3TrLu3AbQx9dZq2K0000"


# -- parsing lamin:// URIs ---------------------------------------------------


def test_uid_form_is_the_canonical_nf_lamin_format():
    uri = parse_lamin_uri(f"lamin://acme/data/artifact/{UID16}")
    assert (uri.instance, uri.uid, uri.subpath) == ("acme/data", UID16, None)
    assert not uri.is_key_form


def test_uid_form_with_subpath_and_full_uid():
    uri = parse_lamin_uri(f"lamin://acme/data/artifact/{UID20}/sub/dir/f.txt")
    assert uri.uid == UID20
    assert uri.subpath == PurePosixPath("sub/dir/f.txt")


def test_key_form_keeps_segments_for_database_side_splitting():
    uri = parse_lamin_uri("lamin://acme/data/artifact/key/my-study/rna.h5ad")
    assert uri.is_key_form
    assert uri.key_segments == ("my-study", "rna.h5ad")


def test_key_form_qualifiers():
    uri = parse_lamin_uri(
        "lamin://acme/data/artifact/key/a.csv?space=team&branch=dev&version=2"
    )
    assert (uri.space, uri.branch, uri.version) == ("team", "dev", "2")


def test_key_segments_are_percent_decoded():
    uri = parse_lamin_uri("lamin://acme/data/artifact/key/my%20study/a.csv")
    assert uri.key_segments == ("my study", "a.csv")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("s3://bucket/key", "Not a lamin:// URI"),
        ("lamin://acme/data", "Incomplete"),
        (f"lamin://acme/data/collection/{UID16}", "Unsupported entity"),
        ("lamin://acme/data/artifact/not-a-uid", "is not a uid"),
        ("lamin://acme/data/artifact/key", "Missing key"),
        (f"lamin://acme/data/artifact/{UID16}/../../.ssh", "are not allowed"),
        ("lamin://acme/data/artifact/key/a/../b", "are not allowed"),
        (f"lamin://acme/data/artifact/{UID16}//x", "Empty path segment"),
        ("lamin://acme/data/artifact/key/a?brnach=dev", "Unknown query parameter"),
        (f"lamin://acme/data/artifact/{UID16}?space=x", "only allowed in the key form"),
        ("lamin://acme/data/artifact/key/a?space=", "exactly one value"),
        (f"lamin://acme/data/artifact/{UID16}#frag", "Fragments"),
    ],
)
def test_malformed_uris_are_rejected(value, message):
    with pytest.raises(InvalidLaminUri, match=message):
        parse_lamin_uri(value)


def test_a_key_mistaken_for_a_uid_gets_a_hint_towards_the_key_form():
    with pytest.raises(InvalidLaminUri, match="artifact/key/<key>"):
        parse_lamin_uri("lamin://acme/data/artifact/rna.h5ad")


# -- resolving key URIs ------------------------------------------------------


class _FakeQuerySet:
    """Just enough of a Django queryset for key resolution."""

    def __init__(self, artifacts):
        self.artifacts = artifacts

    def filter(self, *args, **kwargs):
        rows = self.artifacts
        if "key" in kwargs:
            rows = [a for a in rows if a.key == kwargs["key"]]
        if kwargs.get("is_latest"):
            rows = [a for a in rows if a.is_latest]
        return _FakeQuerySet(rows)

    def __getitem__(self, item):
        return self.artifacts[item]


def _artifact(key, uid=UID20, n_files=None, is_latest=True):
    return SimpleNamespace(key=key, uid=uid, n_files=n_files, is_latest=is_latest)


def _with_artifacts(monkeypatch, *artifacts):
    monkeypatch.setattr(
        _uri, "_artifact_queryset", lambda instance: (_FakeQuerySet(artifacts), True)
    )


def test_an_exact_key_wins(monkeypatch):
    _with_artifacts(monkeypatch, _artifact("data/folder", n_files=3))
    resolved = resolve_lamin_uri("lamin://acme/data/artifact/key/data/folder")
    assert resolved.subpath is None


def test_longest_prefix_resolves_a_file_inside_a_folder_artifact(monkeypatch):
    _with_artifacts(monkeypatch, _artifact("data/folder", n_files=3))
    resolved = resolve_lamin_uri("lamin://acme/data/artifact/key/data/folder/x/y.txt")
    assert resolved.artifact.key == "data/folder"
    assert resolved.subpath == PurePosixPath("x/y.txt")


def test_a_file_artifact_cannot_contain_a_subpath(monkeypatch):
    _with_artifacts(monkeypatch, _artifact("data/file.csv"))
    with pytest.raises(UnresolvableLaminUri, match="No artifact with key"):
        resolve_lamin_uri("lamin://acme/data/artifact/key/data/file.csv/x")


def test_ambiguous_keys_fail_and_list_the_candidates(monkeypatch):
    _with_artifacts(
        monkeypatch,
        _artifact("a.csv", uid="A" * 20),
        _artifact("a.csv", uid="B" * 20),
    )
    with pytest.raises(UnresolvableLaminUri) as error:
        resolve_lamin_uri("lamin://acme/data/artifact/key/a.csv")
    assert "A" * 20 in str(error.value)
    assert "B" * 20 in str(error.value)
    assert "?space=" in str(error.value)


def test_older_versions_are_not_resolved_by_key(monkeypatch):
    _with_artifacts(monkeypatch, _artifact("a.csv", is_latest=False))
    with pytest.raises(UnresolvableLaminUri):
        resolve_lamin_uri("lamin://acme/data/artifact/key/a.csv")


# -- where to run ------------------------------------------------------------


@pytest.fixture
def where_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(_run, "_where_setting_path", lambda: tmp_path / "run-where.txt")
    monkeypatch.delenv(_run.WHERE_ENV, raising=False)
    return tmp_path / "run-where.txt"


def test_where_defaults_to_local(where_setting):
    assert _run.resolve_where(None) == ("local", "default")


def test_where_precedence_is_flag_then_env_then_setting(where_setting, monkeypatch):
    _run.write_where_setting("modal")
    assert _run.resolve_where(None) == ("modal", "lamin settings run-where")
    monkeypatch.setenv(_run.WHERE_ENV, "local")
    assert _run.resolve_where(None) == ("local", _run.WHERE_ENV)
    assert _run.resolve_where("modal") == ("modal", "--where")


def test_an_invalid_where_names_its_source(where_setting, monkeypatch):
    monkeypatch.setenv(_run.WHERE_ENV, "lambda")
    with pytest.raises(_run.RunError, match=_run.WHERE_ENV):
        _run.resolve_where(None)


def test_an_invalid_where_is_never_persisted(where_setting):
    with pytest.raises(_run.RunError):
        _run.write_where_setting("lambda")
    assert not where_setting.exists()


# -- exit codes and outputs --------------------------------------------------


@pytest.mark.parametrize(
    ("returncode", "status"),
    [
        (0, _run.STATUS_COMPLETED),
        (1, _run.STATUS_ERRORED),
        # 2 is a usage error, not lamindb's "aborted"
        (2, _run.STATUS_ERRORED),
        # 127 is not a lamindb status at all
        (127, _run.STATUS_ERRORED),
        (-signal.SIGINT, _run.STATUS_ABORTED),
        (128 + signal.SIGINT, _run.STATUS_ABORTED),
    ],
)
def test_exit_codes_map_onto_run_statuses(returncode, status):
    assert _run.status_code_for(returncode) == status


def test_outputs_are_collected_from_flags_and_explicit_registration():
    argv = ["tool", "--out", "a.txt", "--output=b.txt", "--out", "--verbose"]
    paths = _run.collect_output_paths(argv, ("c.txt",))
    assert paths == [Path("c.txt"), Path("a.txt"), Path("b.txt")]


# -- how a target is started -------------------------------------------------


def test_a_plain_python_script_runs_like_python_script_py(tmp_path):
    script = tmp_path / "train.py"
    script.write_text("print(1)")
    argv = _run.command_for(str(script))
    assert Path(argv[0]).name.startswith("python")
    assert argv[1:] == [str(script)]


def test_an_executable_script_runs_directly_to_respect_its_shebang(tmp_path):
    script = tmp_path / "train.py"
    script.write_text("#!/usr/bin/env python3\nprint(1)")
    script.chmod(0o755)
    assert _run.command_for(str(script)) == [str(script)]


@pytest.mark.parametrize(
    ("name", "prefix"),
    [
        ("align.sh", ["bash"]),
        ("plot.R", ["Rscript"]),
        ("report.qmd", ["quarto", "render"]),
    ],
)
def test_scripts_get_the_interpreter_for_their_suffix(tmp_path, name, prefix):
    script = tmp_path / name
    script.write_text("")
    assert _run.command_for(str(script)) == [*prefix, str(script)]


def test_executables_on_path_run_as_given():
    assert _run.command_for("samtools") == ["samtools"]


# -- teeing the target's output ----------------------------------------------

_SPLIT_CHAR_CHILD = r"""
import sys, time
sys.stdout.buffer.write(b"caf\xc3"); sys.stdout.flush(); time.sleep(0.2)
sys.stdout.buffer.write(b"\xa9\n"); sys.stdout.flush()
sys.stderr.write("warning\n")
sys.exit(4)
"""


def test_tee_keeps_streams_apart_and_decodes_split_characters(monkeypatch):
    import io

    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    # "é" is two bytes that arrive in separate writes; they must not be garbled
    returncode = _run.run_teed(
        [sys.executable, "-c", _SPLIT_CHAR_CHILD], dict(os.environ)
    )
    assert returncode == 4
    assert out.getvalue() == "café\n"
    assert err.getvalue() == "warning\n"


# -- argument translation ----------------------------------------------------


def _fake_translation(uri, remount=False, note=None):
    return _run.Translation(uri=uri, local_path=Path(f"/mnt/{uri[-4:]}"), via="mount")


def test_translation_rewrites_bare_and_flag_uris_only(monkeypatch):
    monkeypatch.setattr(_run, "resolve_uri_to_local_path", _fake_translation)
    uri = f"lamin://acme/data/artifact/{UID16}"
    argv, translations = _run.translate_argv(
        ["tool", uri, f"--in={uri}", "--name=lamin", "plain", "-x"]
    )
    assert argv == [
        "tool",
        "/mnt/Zq2K",
        "--in=/mnt/Zq2K",
        "--name=lamin",
        "plain",
        "-x",
    ]
    # the same URI is resolved, and linked as an input, once
    assert len(translations) == 1


def test_child_environment(monkeypatch, tmp_path):
    from lamin_cli.mount import _registry

    monkeypatch.setattr(_registry, "_registry_path", lambda: tmp_path / "mounts.json")
    translation = _fake_translation(f"lamin://acme/data/artifact/{UID16}")
    env = _run.child_environment("RunUid00000000000000", "my-project", [translation])
    assert env["LAMIN_INITIATED_BY_RUN_UID"] == "RunUid00000000000000"
    assert env["LAMIN_CURRENT_PROJECT"] == "my-project"
    assert json.loads(env["LAMIN_INPUT_PATHS"]) == {translation.uri: "/mnt/Zq2K"}
    assert json.loads(env["LAMIN_MOUNTS"]) == {}


# -- command-line parsing ----------------------------------------------------


@pytest.fixture
def captured(monkeypatch, where_setting):
    requests = []

    def fake_dispatch(where, request):
        requests.append((where, request))
        return 0

    monkeypatch.setattr(_run, "dispatch", fake_dispatch)
    return requests


def _invoke(*args):
    from lamin_cli.__main__ import main

    return CliRunner().invoke(main, ["run", *args])


def test_only_arguments_after_the_separator_reach_the_target(captured):
    result = _invoke("--project", "p", "train.py", "--", "--project", "q", "--gpu", "0")
    assert result.exit_code == 0, result.output
    where, request = captured[0]
    assert request.project == "p"
    assert request.args == ["--project", "q", "--gpu", "0"]


def test_lamin_options_may_follow_the_target_before_the_separator(captured):
    result = _invoke("train.py", "--where", "local", "--", "x")
    assert result.exit_code == 0, result.output
    assert captured[0][0] == "local"
    assert captured[0][1].args == ["x"]


def test_target_arguments_without_separator_fail_loudly(captured):
    result = _invoke("train.py", "--epochs", "3")
    assert result.exit_code != 0
    assert "after `--`" in result.output
    assert not captured


def test_modal_only_options_hint_at_the_separator(captured):
    result = _invoke("train.py", "--gpu", "0")
    assert result.exit_code != 0
    assert "only applies to --where modal" in result.output
    assert "lamin run train.py -- --gpu" in result.output


def test_modal_needs_a_project(where_setting):
    request = _run.RunRequest(target="s.py", args=[])
    with pytest.raises(_run.RunError, match="needs --project"):
        _run.run_modal(request)


# -- end to end, on the test instance ----------------------------------------


def _lamin(*args, cwd, env=None):
    return subprocess.run(
        [sys.executable, "-m", "lamin_cli", *args],
        cwd=cwd,
        env={**os.environ, "NO_RICH": "1", **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def input_artifact(tmp_path):
    # created in a fresh interpreter: earlier tests re-initialize lamindb in this
    # process, which breaks in-process writes, and `lamin save` would link a stale
    # run left behind by `lamin track` tests
    import lamindb as ln

    source = tmp_path / "input.txt"
    source.write_text("hello from lamin\n")
    key = "lamin-run-test/input.txt"
    code = f"import lamindb as ln; ln.Artifact({str(source)!r}, key={key!r}).save()"
    saved = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert saved.returncode == 0, saved.stderr
    artifact = (
        ln.Artifact.filter(key=key, is_latest=True).order_by("-created_at").first()
    )
    yield artifact
    _lamin(
        "delete",
        "artifact",
        "--uid",
        artifact.uid,
        "--permanent",
        cwd=tmp_path,
    )


def _script(tmp_path, body: str) -> Path:
    script = tmp_path / "script.py"
    script.write_text(textwrap.dedent(body))
    return script


def test_run_translates_uris_links_inputs_and_registers_outputs(
    tmp_path, input_artifact, where_setting
):
    import lamindb as ln
    import lamindb_setup as ln_setup

    script = _script(
        tmp_path,
        """
        import json, os, sys
        src, dst = sys.argv[1], sys.argv[3]
        with open(src) as f, open(dst, "w") as out:
            out.write(f.read().upper())
        with open("env.json", "w") as f:
            json.dump({k: v for k, v in os.environ.items() if k.startswith("LAMIN_")}, f)
        """,
    )
    slug = ln_setup.settings.instance.slug
    uri = f"lamin://{slug}/artifact/key/lamin-run-test/input.txt"
    result = _lamin("run", str(script), "--", uri, "--out", "out.txt", cwd=tmp_path)
    assert result.returncode == 0, result.stderr

    assert (tmp_path / "out.txt").read_text() == "HELLO FROM LAMIN\n"
    assert "(via local)" in result.stderr

    run = ln.Run.filter(transform__key="script.py").order_by("-started_at").first()
    assert run.status == "completed"
    assert run.cli_args == f"{uri} --out out.txt"
    assert input_artifact.uid in {a.uid for a in run.input_artifacts.all()}
    output = ln.Artifact.filter(run=run, key="out.txt").one()
    env = json.loads((tmp_path / "env.json").read_text())
    assert env["LAMIN_INITIATED_BY_RUN_UID"] == run.uid
    assert uri in json.loads(env["LAMIN_INPUT_PATHS"])
    _lamin("delete", "artifact", "--uid", output.uid, "--permanent", cwd=tmp_path)


def test_run_resolves_through_a_registered_mount(
    tmp_path, input_artifact, where_setting
):
    import lamindb_setup as ln_setup

    storage_root = Path(ln_setup.settings.storage.root_as_str)
    mountpoint = tmp_path / "mnt"
    mountpoint.symlink_to(storage_root, target_is_directory=True)
    registered = _lamin("settings", "mount", "register", str(mountpoint), cwd=tmp_path)
    assert registered.returncode == 0, registered.stderr
    try:
        uri = f"lamin://{ln_setup.settings.instance.slug}/artifact/{input_artifact.uid}"
        path = _lamin("settings", "mount", "path", uri, cwd=tmp_path)
        assert path.returncode == 0, path.stderr
        assert path.stdout.strip().startswith(str(mountpoint))

        script = _script(tmp_path, "import sys; print(sys.argv[1])")
        result = _lamin("run", str(script), "--", uri, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        # lamin run and `lamin settings mount path` resolve to the same place
        assert result.stdout.strip() == path.stdout.strip()
        assert "(via mount)" in result.stderr
    finally:
        _lamin("settings", "mount", "unregister", str(mountpoint), cwd=tmp_path)


def test_a_failing_target_propagates_its_exit_code(tmp_path, where_setting):
    import lamindb as ln

    script = _script(tmp_path, "import sys; sys.exit(3)")
    script = script.rename(tmp_path / "failing.py")
    result = _lamin("run", str(script), cwd=tmp_path)
    assert result.returncode == 3
    run = ln.Run.filter(transform__key="failing.py").order_by("-started_at").first()
    assert run.status == "errored"


def test_a_missing_executable_is_recorded_as_errored(tmp_path, where_setting):
    import lamindb as ln

    result = _lamin("run", "lamin-no-such-tool", cwd=tmp_path)
    assert result.returncode == 127
    run = (
        ln.Run.filter(transform__key="lamin-no-such-tool")
        .order_by("-started_at")
        .first()
    )
    # before this was fixed, the raw exit code 127 was stored as the status code
    assert run.status == "errored"


def test_an_unresolvable_uri_fails_before_anything_runs(tmp_path, where_setting):
    import lamindb_setup as ln_setup

    marker = tmp_path / "ran"
    script = _script(tmp_path, f"open({str(marker)!r}, 'w').close()")
    uri = f"lamin://{ln_setup.settings.instance.slug}/artifact/key/does/not/exist.txt"
    result = _lamin("run", str(script), "--", uri, cwd=tmp_path)
    assert result.returncode != 0
    assert "No artifact with key" in result.stderr
    assert not marker.exists()


def test_a_non_executable_target_exits_126(tmp_path, where_setting):
    import lamindb as ln

    target = tmp_path / "tool"
    target.write_text("not a program")
    result = _lamin("run", str(target), cwd=tmp_path)
    assert result.returncode == 126
    assert "is not executable" in result.stderr
    run = ln.Run.filter(transform__key="tool").order_by("-started_at").first()
    assert run.status == "errored"


def test_stdout_carries_only_the_targets_output_and_logs_capture_both(
    tmp_path, where_setting
):
    import lamindb as ln

    script = tmp_path / "teed.py"
    script.write_text(
        "import sys\nprint('result line')\nprint('progress note', file=sys.stderr)\n"
    )
    result = _lamin("run", str(script), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    # pipeable: nothing from lamin itself reaches stdout
    assert result.stdout == "result line\n"
    assert "progress note" in result.stderr

    run = ln.Run.filter(transform__key="teed.py").order_by("-started_at").first()
    logs = Path(run.report.cache()).read_text()
    assert "result line" in logs
    assert "progress note" in logs
