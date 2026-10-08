from __future__ import annotations

import json
import os
import re
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
    # `lamin run` resolves its access methods alongside where to run
    monkeypatch.setattr(
        _run, "_access_setting_path", lambda: tmp_path / "run-access.txt"
    )
    monkeypatch.delenv(_run.ACCESS_ENV, raising=False)
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


def test_probe_version_reads_the_first_line_of_a_working_tool():
    version = _run._probe_version(sys.executable)
    assert version is not None and version.lower().startswith("python")


def test_probe_version_is_none_for_a_tool_without_the_flag():
    assert _run._probe_version("lamin-no-such-tool") is None


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


def _fake_translation(uri, *args, **kwargs):
    return _run.Translation(uri=uri, local_path=Path(f"/mnt/{uri[-4:]}"), via="mount")


def test_translation_rewrites_bare_and_flag_uris_only(monkeypatch):
    monkeypatch.setattr(_run, "resolve_uri", _fake_translation)
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


# -- access methods ----------------------------------------------------------


class _FakeFs:
    def sign(self, path, expiration):
        return f"https://signed.example/{path.split('://')[1]}?exp={expiration}"


class _FakePath:
    fs = _FakeFs()

    def __init__(self, text, options=None):
        self.text = text
        self.storage_options = options or {
            "key": "AKIA",
            "secret": "shh",
            "token": "tok",
        }

    def __truediv__(self, other):
        return _FakePath(f"{self.text}/{other}", self.storage_options)

    def __str__(self):
        return self.text

    def is_dir(self):
        return self.text.endswith("/dir")


URI = f"lamin://acme/data/artifact/{UID16}"


def _resolved(storage_type="s3", subpath=None, n_files=None, root=None, key="AKIA"):
    from lamin_cli import _uri

    root = root or f"{'s3' if storage_type == 'local' else storage_type}://bucket"
    artifact = SimpleNamespace(
        storage=SimpleNamespace(type=storage_type, root=root),
        path=_FakePath(f"{root}/data/a.bam", {"key": key, "secret": "shh"}),
        n_files=n_files,
        cache=lambda is_run_input: f"/cache/{root.split('://')[1]}/a.bam",
    )
    return _uri.ResolvedUri(
        uri=None,
        artifact=artifact,
        subpath=PurePosixPath(subpath) if subpath else None,
        in_current_instance=True,
    )


def _fake_resolved(monkeypatch, *args, by_uri=None, mounted=None, **kwargs):
    """Fake URI resolution; storage roots in `mounted` resolve through a mount."""
    from lamin_cli import _uri

    single = _resolved(*args, **kwargs)
    monkeypatch.setattr(
        _uri, "resolve_lamin_uri", lambda value: (by_uri or {}).get(value, single)
    )

    def access_mount(uri, resolved, remount, note):
        storage = resolved.artifact.storage
        if storage.type == "local":
            return _run.Translation(uri, Path("/local/a.bam"), "local")
        if storage.root in (mounted or ()):
            return _run.Translation(uri, Path("/mnt/a.bam"), "mount")
        raise _run.AccessUnavailable("the storage location is not mounted", True)

    monkeypatch.setattr(_run, "_access_mount", access_mount)


def _translate(*argv, access, **kwargs):
    return _run.translate_argv(list(argv), access=tuple(access.split(",")), **kwargs)


def test_parse_access_keeps_order_and_rejects_typos_and_repeats():
    assert _run.parse_access(" presigned, mount ") == ("presigned", "mount")
    with pytest.raises(_run.RunError, match="Unknown access method.*'local'"):
        _run.parse_access("local")
    with pytest.raises(_run.RunError, match="listed twice: cache"):
        _run.parse_access("cache,mount,cache")
    with pytest.raises(_run.RunError, match="No access method"):
        _run.parse_access(" , ")


def test_access_precedence_is_flag_then_env_then_setting(where_setting, monkeypatch):
    assert _run.resolve_access(None) == (("mount", "cache"), "default")
    _run.write_access_setting("presigned,mount")
    assert _run.resolve_access(None) == (
        ("presigned", "mount"),
        "lamin settings run-access",
    )
    monkeypatch.setenv(_run.ACCESS_ENV, "credentials")
    assert _run.resolve_access(None) == (("credentials",), _run.ACCESS_ENV)
    assert _run.resolve_access("cache") == (("cache",), "--access")
    monkeypatch.setenv(_run.ACCESS_ENV, "bogus")
    with pytest.raises(_run.RunError, match=f"from {_run.ACCESS_ENV}"):
        _run.resolve_access(None)


def test_an_invalid_access_setting_is_never_persisted(where_setting):
    with pytest.raises(_run.RunError):
        _run.write_access_setting("mount,ftp")
    assert not (where_setting.parent / "run-access.txt").exists()


def test_the_default_falls_back_to_the_cache_without_noise(monkeypatch):
    _fake_resolved(monkeypatch)
    argv, translations = _translate(URI, access="mount,cache")
    assert argv == ["/cache/bucket/a.bam"]
    assert translations[0].via == "cache"
    # an unmounted storage location is the expected case, not worth a note
    assert translations[0].skipped == []


def test_a_mount_wins_over_urls_when_listed_first(monkeypatch):
    _fake_resolved(monkeypatch, mounted={"s3://bucket"})
    argv, translations = _translate(URI, access="mount,presigned")
    assert argv == ["/mnt/a.bam"]
    assert translations[0].via == "mount"


def test_presigned_access_replaces_uris_but_never_shows_the_signature(monkeypatch):
    _fake_resolved(monkeypatch)
    argv, translations = _translate(
        "view", URI, f"--in={URI}", access="presigned", expiry=60
    )
    signed = "https://signed.example/bucket/data/a.bam?exp=60"
    assert argv == ["view", signed, f"--in={signed}"]
    assert translations[0].via == "presigned"
    assert translations[0].shown == "s3://bucket/data/a.bam"
    assert "exp=" not in _run.describe_translation(translations[0])
    assert translations[0].env == {}


def test_presigned_access_needs_a_file(monkeypatch):
    _fake_resolved(monkeypatch, n_files=3)
    with pytest.raises(_run.RunError, match="presigned: it is a folder"):
        _translate(URI, access="presigned")
    _fake_resolved(monkeypatch, n_files=3, subpath="x.bam")
    argv, _ = _translate(f"{URI}/x.bam", access="presigned")
    assert argv[0].startswith("https://signed.example/bucket/data/a.bam/x.bam")
    _fake_resolved(monkeypatch, n_files=3, subpath="dir")
    with pytest.raises(_run.RunError, match="folder inside the artifact"):
        _translate(f"{URI}/dir", access="presigned")


def test_a_folder_falls_through_to_the_next_method_and_says_why(monkeypatch):
    _fake_resolved(monkeypatch, n_files=3)
    argv, translations = _translate(URI, access="presigned,credentials")
    assert argv == ["s3://bucket/data/a.bam"]
    assert translations[0].via == "credentials"
    assert translations[0].skipped == [
        ("presigned", "it is a folder, and only files can be presigned")
    ]
    assert "skipped presigned: it is a folder" in _run.describe_translation(
        translations[0]
    )


def test_credentials_access_passes_an_unsigned_url_and_env(monkeypatch, tmp_path):
    from lamin_cli.mount import _registry

    monkeypatch.setattr(_registry, "_registry_path", lambda: tmp_path / "mounts.json")
    _fake_resolved(monkeypatch)
    argv, translations = _translate(URI, access="credentials")
    assert argv == ["s3://bucket/data/a.bam"]
    assert translations[0].via == "credentials"
    env = _run.child_environment("RunUid00000000000000", None, translations)
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA"
    assert env["AWS_SECRET_ACCESS_KEY"] == "shh"
    assert "AKIA" not in " ".join(argv)


def _gcs_token(monkeypatch):
    class Credentials:
        credentials = SimpleNamespace(token="ya29.tok")

        def maybe_refresh(self):
            pass

    monkeypatch.setattr(_FakeFs, "credentials", Credentials(), raising=False)


def test_credentials_access_passes_a_token_for_gcs(monkeypatch):
    _fake_resolved(monkeypatch, storage_type="gs")
    _gcs_token(monkeypatch)
    argv, translations = _translate(URI, access="credentials")
    assert argv == ["gs://bucket/data/a.bam"]
    assert translations[0].env == {
        "CLOUDSDK_AUTH_ACCESS_TOKEN": "ya29.tok",
        "GOOGLE_OAUTH_ACCESS_TOKEN": "ya29.tok",
    }


def test_gcs_without_a_signing_key_falls_back_to_its_token(monkeypatch):
    def refuse(self, path, expiration):
        raise AttributeError("you need a private key to sign credentials")

    _fake_resolved(monkeypatch, storage_type="gs")
    _gcs_token(monkeypatch)
    monkeypatch.setattr(_FakeFs, "sign", refuse)
    with pytest.raises(_run.RunError, match="service account"):
        _translate(URI, access="presigned")
    argv, translations = _translate(URI, access="presigned,credentials")
    assert argv == ["gs://bucket/data/a.bam"]
    assert translations[0].skipped[0][0] == "presigned"


def test_credentials_access_needs_s3_or_gcs(monkeypatch):
    _fake_resolved(monkeypatch, storage_type="hf")
    with pytest.raises(_run.RunError, match="only s3 and gs are supported"):
        _translate(URI, access="credentials")


def test_local_storage_has_no_url(monkeypatch):
    _fake_resolved(monkeypatch, storage_type="local")
    with pytest.raises(_run.RunError) as error:
        _translate(URI, access="presigned,credentials")
    # every allowed method explains itself
    message = str(error.value)
    assert "--access presigned,credentials" in message
    assert "presigned: local storage has no URL" in message
    assert "credentials: local storage has no URL" in message
    argv, translations = _translate(URI, access="presigned,mount")
    assert (argv, translations[0].via) == (["/local/a.bam"], "local")


def test_inputs_with_other_credentials_fall_through_per_uri(monkeypatch):
    """Two buckets with two identities: the first claims the process's identity,
    the second gets the next allowed method instead of failing the run."""
    first, second = f"{URI}/a", f"lamin://acme/data/artifact/{UID20}"
    _fake_resolved(
        monkeypatch,
        by_uri={
            first: _resolved(root="s3://one", key="KEY1"),
            second: _resolved(root="s3://two", key="KEY2"),
        },
    )
    argv, translations = _translate(first, second, access="credentials,presigned")
    assert argv[0] == "s3://one/data/a.bam"
    assert argv[1].startswith("https://signed.example/two/data/a.bam")
    assert translations[1].skipped[0][0] == "credentials"
    assert "one process holds one identity" in translations[1].skipped[0][1]
    with pytest.raises(_run.RunError, match="credentials: it needs other"):
        _translate(first, second, access="credentials")


def test_inputs_sharing_credentials_share_the_process(monkeypatch):
    first, second = f"{URI}/a", f"lamin://acme/data/artifact/{UID20}"
    _fake_resolved(
        monkeypatch,
        by_uri={first: _resolved(root="s3://one"), second: _resolved(root="s3://two")},
    )
    argv, translations = _translate(first, second, access="credentials")
    assert [t.via for t in translations] == ["credentials", "credentials"]


def test_dry_run_neither_signs_nor_fetches_credentials(monkeypatch):
    _fake_resolved(monkeypatch)
    argv, _ = _translate(URI, access="presigned", sign=False)
    assert argv == ["<presigned URL for s3://bucket/data/a.bam>"]
    _, translations = _translate(URI, access="credentials", sign=False)
    assert translations[0].env == {}


def test_credentials_of_different_identities_cannot_share_a_process():
    a = _run.Translation(
        "u1", "s3://a/x", "credentials", env={"AWS_ACCESS_KEY_ID": "1"}
    )
    b = _run.Translation(
        "u2", "s3://b/y", "credentials", env={"AWS_ACCESS_KEY_ID": "2"}
    )
    with pytest.raises(_run.RunError, match="presigned"):
        _run.merge_credential_env([a, b])


def _credential_translation(storage_type="s3", **env):
    artifact = SimpleNamespace(
        storage=SimpleNamespace(type=storage_type, root="s3://bucket")
    )
    env = env or {"AWS_ACCESS_KEY_ID": "AKIA", "AWS_SECRET_ACCESS_KEY": "shh"}
    return _run.Translation(
        "lamin://x", "s3://bucket/a", "credentials", artifact=artifact, env=env
    )


def test_refreshed_credentials_file_is_private_updated_and_removed(monkeypatch):
    import time

    from lamin_cli.mount import _credentials

    fresh = {"key": "NEWKEY", "secret": "newsecret", "token": "newtoken"}
    monkeypatch.setattr(_credentials, "fetch_aws_credentials", lambda root: fresh)
    monkeypatch.setattr(_credentials.RefreshedCredentialsFile, "MIN_INTERVAL", 0.05)
    monkeypatch.setattr(_credentials.RefreshedCredentialsFile, "MAX_INTERVAL", 0.05)
    file = _credentials.RefreshedCredentialsFile(
        "s3://bucket", {"key": "OLD", "secret": "old", "token": None}
    )
    file.start()
    path = file.path
    try:
        assert path.stat().st_mode & 0o777 == 0o600
        assert "OLD" in path.read_text() or "NEWKEY" in path.read_text()
        for _ in range(100):
            if "NEWKEY" in path.read_text():
                break
            time.sleep(0.05)
        content = path.read_text()
        assert "[lamin]" in content
        assert "aws_access_key_id = NEWKEY" in content
        assert "aws_session_token = newtoken" in content
    finally:
        file.stop()
    assert not path.exists()


def test_file_delivery_replaces_secret_env_vars_with_a_profile(monkeypatch):
    from lamin_cli.mount import _credentials

    monkeypatch.setattr(_credentials, "fetch_aws_credentials", lambda root: None)
    translation = _credential_translation(
        AWS_ACCESS_KEY_ID="AKIA",
        AWS_SECRET_ACCESS_KEY="shh",
        AWS_ENDPOINT_URL="https://s3.example",
    )
    managed = _run.start_managed_credentials([translation], "file")
    try:
        assert managed.env["AWS_PROFILE"] == "lamin"
        assert managed.env["AWS_ENDPOINT_URL"] == "https://s3.example"
        assert "AKIA" in Path(managed.env["AWS_SHARED_CREDENTIALS_FILE"]).read_text()
        assert not set(managed.env) & set(_run._AWS_SECRET_ENV)
    finally:
        managed.stop()
    assert not Path(managed.env["AWS_SHARED_CREDENTIALS_FILE"]).exists()


def test_process_delivery_writes_a_config_without_secrets(monkeypatch, tmp_path):
    from lamin_cli.mount import _credentials

    monkeypatch.setattr(
        _credentials, "fetch_aws_credentials", lambda root: {"key": "k", "secret": "s"}
    )
    monkeypatch.setattr(
        "lamindb_setup.core._settings_store.settings_dir", tmp_path, raising=False
    )
    translation = _credential_translation()
    _run.validate_credentials_via([translation], "process")
    managed = _run.start_managed_credentials([translation], "process")
    config = Path(managed.env["AWS_CONFIG_FILE"]).read_text()
    assert "credential_process" in config
    assert "settings mount credentials --root s3://bucket" in config
    assert "AKIA" not in config and "shh" not in config
    assert managed.env["AWS_PROFILE"] == "lamin"
    assert managed.env["AWS_SHARED_CREDENTIALS_FILE"] == os.devnull


def test_process_delivery_needs_reissuable_credentials(monkeypatch):
    from lamin_cli.mount import _credentials

    monkeypatch.setattr(_credentials, "fetch_aws_credentials", lambda root: None)
    with pytest.raises(_run.RunError, match="reissue"):
        _run.validate_credentials_via([_credential_translation()], "process")


def test_file_and_process_delivery_are_s3_only():
    translation = _credential_translation(
        storage_type="gs", CLOUDSDK_AUTH_ACCESS_TOKEN="t"
    )
    for via in ("file", "process"):
        with pytest.raises(_run.RunError, match="only supports s3"):
            _run.validate_credentials_via([translation], via)
    _run.validate_credentials_via([translation], "env")


def test_s3_and_gs_inputs_mix_with_gs_staying_in_the_environment(monkeypatch):
    from lamin_cli.mount import _credentials

    monkeypatch.setattr(_credentials, "fetch_aws_credentials", lambda root: None)
    s3 = _credential_translation()
    gs = _credential_translation(storage_type="gs", CLOUDSDK_AUTH_ACCESS_TOKEN="t")
    gs.artifact.storage.root = "gs://other"
    _run.validate_credentials_via([gs, s3], "file")
    managed = _run.start_managed_credentials([gs, s3], "file")
    try:
        # the profile is for the s3 root, not whichever input came first
        assert managed.file.storage_root == "s3://bucket"
        assert _run.merge_credential_env([gs, s3])["CLOUDSDK_AUTH_ACCESS_TOKEN"] == "t"
    finally:
        managed.stop()


def test_two_s3_identities_are_refused():
    a = _credential_translation(AWS_ACCESS_KEY_ID="1", AWS_SECRET_ACCESS_KEY="x")
    b = _credential_translation(AWS_ACCESS_KEY_ID="2", AWS_SECRET_ACCESS_KEY="y")
    with pytest.raises(_run.RunError, match="presigned"):
        _run.merge_credential_env([a, b])


def test_env_delivery_leaves_the_environment_alone():
    managed = _run.start_managed_credentials([_credential_translation()], "env")
    assert managed.env == {}


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


def _plain(output: str) -> str:
    """Error text without the styling and line wrapping of rich-click's error box.

    GitHub Actions forces a color terminal, so rich-click adds ANSI codes there.
    """
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    return " ".join(re.sub(r"[│╭╮╰╯─]", " ", output).split())


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
    # GitHub Actions forces a color terminal, and rich-click then styles option
    # names, which splits these phrases with ANSI codes.
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "only applies to --where modal" in output
    assert "lamin run train.py -- --gpu" in output


def test_access_options_reach_the_request(captured):
    result = _invoke(
        "--access", "mount,presigned", "--presign-expiry", "60", "s.py", "--", "x"
    )
    assert result.exit_code == 0, result.output
    request = captured[0][1]
    assert request.access == ("mount", "presigned")
    assert request.presign_expiry == 60


def test_access_defaults_to_local_paths_and_honors_the_environment(
    captured, monkeypatch
):
    assert _invoke("s.py").exit_code == 0
    assert captured[0][1].access == ("mount", "cache")
    monkeypatch.setenv(_run.ACCESS_ENV, "credentials,cache")
    assert _invoke("s.py").exit_code == 0
    assert captured[1][1].access == ("credentials", "cache")


def test_credentials_via_reaches_the_request_and_needs_credentials_access(captured):
    result = _invoke(
        "--access", "presigned,credentials", "--credentials-via", "file", "s.py"
    )
    assert result.exit_code == 0, result.output
    assert captured[0][1].credentials_via == "file"
    result = _invoke("--credentials-via", "file", "s.py")
    assert result.exit_code != 0
    assert "only applies when --access allows credentials" in _plain(result.output)


def test_access_misuse_is_rejected(captured):
    result = _invoke("--presign-expiry", "60", "s.py")
    assert result.exit_code != 0
    assert "only applies when --access allows presigned" in _plain(result.output)
    result = _invoke("--access", "credentials", "--where", "modal", "s.py")
    assert result.exit_code != 0
    assert "--where local" in _plain(result.output)
    result = _invoke("--access", "mount,local", "s.py")
    assert result.exit_code != 0
    assert "Unknown access method" in _plain(result.output)
    assert not captured


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
    assert run.params["argv"] == [uri, "--out", "out.txt"]
    assert run.params["tool_version"].lower().startswith("python")
    assert input_artifact.uid in {a.uid for a in run.input_artifacts.all()}
    assert run.environment is not None
    assert run.environment.description == "requirements.txt"
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


def test_a_reused_executables_version_lands_on_each_run_not_the_transform(
    tmp_path, where_setting
):
    """Two different versions of a same-named tool must not rewrite each other's
    history: lamindb never auto-versions an executable transform (no source code to
    hash it by), so it's the same transform record for both runs.
    """
    import lamindb as ln

    tool_v1 = tmp_path / "mytool"
    tool_v1.write_text(
        '#!/bin/bash\nif [ "$1" = "--version" ]; then echo "mytool 1.0.0"; fi\nexit 0\n'
    )
    tool_v1.chmod(0o755)
    result = _lamin("run", str(tool_v1), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    run_1 = ln.Run.filter(transform__key="mytool").order_by("-started_at").first()
    assert run_1.params["tool_version"] == "mytool 1.0.0"

    tool_v1.write_text(
        '#!/bin/bash\nif [ "$1" = "--version" ]; then echo "mytool 2.0.0"; fi\nexit 0\n'
    )
    result = _lamin("run", str(tool_v1), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    run_2 = ln.Run.filter(transform__key="mytool").order_by("-started_at").first()
    assert run_2.uid != run_1.uid
    assert run_2.transform.uid == run_1.transform.uid  # same, reused transform
    assert run_2.params["tool_version"] == "mytool 2.0.0"

    # re-fetch: run_1's own record must still show what it actually ran
    run_1.refresh_from_db()
    assert run_1.params["tool_version"] == "mytool 1.0.0"


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


def test_non_python_scripts_get_no_environment_snapshot(tmp_path, where_setting):
    import lamindb as ln

    script = tmp_path / "greet.sh"
    script.write_text("echo hi\n")
    result = _lamin("run", str(script), cwd=tmp_path)
    assert result.returncode == 0, result.stderr

    run = ln.Run.filter(transform__key="greet.sh").order_by("-started_at").first()
    assert run.environment is None
    # the interpreter is still versioned, just not its packages, and per run rather
    # than on the (possibly reused) transform
    assert run.params["tool_version"].lower().startswith("gnu bash")


# -- output key, upload policy, branch/space, dry-run ------------------------


def test_output_key_preserves_the_relative_path(tmp_path, where_setting):
    import lamindb as ln

    script = _script(
        tmp_path,
        """
        from pathlib import Path
        Path("results").mkdir(exist_ok=True)
        Path("results/summary.csv").write_text("a,b\\n1,2\\n")
        """,
    )
    result = _lamin(
        "run", str(script), "--register-output", "results/summary.csv", cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr

    run = ln.Run.filter(transform__key="script.py").order_by("-started_at").first()
    output = ln.Artifact.filter(run=run).one()
    assert output.key == "results/summary.csv"
    _lamin("delete", "artifact", "--uid", output.uid, "--permanent", cwd=tmp_path)


def test_output_outside_cwd_falls_back_to_the_basename(tmp_path, where_setting):
    import lamindb as ln

    outside = tmp_path.parent / f"outside-{tmp_path.name}.csv"
    outside.write_text("a,b\n1,2\n")
    script = _script(tmp_path, "pass")
    try:
        result = _lamin(
            "run", str(script), "--register-output", str(outside), cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        run = ln.Run.filter(transform__key="script.py").order_by("-started_at").first()
        output = ln.Artifact.filter(run=run).one()
        assert output.key == outside.name
        _lamin("delete", "artifact", "--uid", output.uid, "--permanent", cwd=tmp_path)
    finally:
        outside.unlink(missing_ok=True)


def test_keep_artifacts_local_is_honored_and_warns(tmp_path, monkeypatch, capsys):
    """`monkeypatch` can't reach `--register-output`'s subprocess, so this exercises
    `_register_outputs` directly, the same function `lamin run` calls after a
    successful target.
    """
    import lamindb as ln
    import lamindb_setup as ln_setup

    monkeypatch.setattr(
        type(ln_setup.settings.instance),
        "keep_artifacts_local",
        property(lambda self: True),
    )
    monkeypatch.chdir(tmp_path)
    transform = ln.Transform(key="unit-test-keep-local-warns", kind="pipeline").save()
    run = ln.Run(transform=transform).save()
    try:
        output = tmp_path / "out.txt"
        output.write_text("x")
        _run._register_outputs(
            run,
            [output],
            ln_setup.settings.branch,
            ln_setup.settings.space,
            upload_outputs=False,
        )
        stderr = capsys.readouterr().err
        assert "kept local" in stderr
        assert "--upload-outputs" in stderr
        artifact = ln.Artifact.filter(run=run).one()
        artifact.delete(permanent=True)
    finally:
        run.delete()
        transform.delete()


def test_upload_outputs_suppresses_the_keep_local_warning(
    tmp_path, monkeypatch, capsys
):
    import lamindb as ln
    import lamindb_setup as ln_setup

    monkeypatch.setattr(
        type(ln_setup.settings.instance),
        "keep_artifacts_local",
        property(lambda self: True),
    )
    monkeypatch.chdir(tmp_path)
    transform = ln.Transform(
        key="unit-test-keep-local-override", kind="pipeline"
    ).save()
    run = ln.Run(transform=transform).save()
    try:
        output = tmp_path / "out.txt"
        output.write_text("x")
        _run._register_outputs(
            run,
            [output],
            ln_setup.settings.branch,
            ln_setup.settings.space,
            upload_outputs=True,
        )
        assert "kept local" not in capsys.readouterr().err
        artifact = ln.Artifact.filter(run=run).one()
        artifact.delete(permanent=True)
    finally:
        run.delete()
        transform.delete()


def test_branch_and_space_apply_to_transform_run_and_outputs(tmp_path, where_setting):
    import lamindb as ln
    import lamindb_setup as ln_setup

    branch_name = f"lamin-run-test-branch-{tmp_path.name}"
    branch = ln.Branch(name=branch_name).save()
    script = _script(tmp_path, "__import__('pathlib').Path('out.txt').write_text('x')")
    result = _lamin(
        "run",
        str(script),
        "--branch",
        branch_name,
        "--register-output",
        "out.txt",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr

    run = (
        ln.Run.filter(transform__key="script.py", branch=branch)
        .order_by("-started_at")
        .first()
    )
    assert run is not None
    assert run.transform.branch_id == branch.id
    # lamindb's default queryset scoping only includes [ambient branch, main]
    # unless branch is referenced explicitly, so this needs it too
    output = ln.Artifact.filter(run=run, branch=branch).one()
    assert output.branch_id == branch.id
    # the ambient local branch is untouched
    assert ln_setup.settings.branch.id != branch.id
    _lamin("delete", "artifact", "--uid", output.uid, "--permanent", cwd=tmp_path)
    # the branch is left in place: Transform/Run still reference it (PROTECT), and
    # the session-scoped test instance is torn down wholesale at the end anyway


def test_an_unknown_branch_errors_clearly(tmp_path, where_setting):
    script = _script(tmp_path, "pass")
    result = _lamin("run", str(script), "--branch", "no-such-branch", cwd=tmp_path)
    assert result.returncode != 0
    assert "no-such-branch" in result.stderr


def test_dry_run_reports_without_executing_or_saving(
    tmp_path, input_artifact, where_setting
):
    import lamindb as ln
    import lamindb_setup as ln_setup

    marker = tmp_path / "ran"
    script = _script(tmp_path, f"open({str(marker)!r}, 'w').close()")
    slug = ln_setup.settings.instance.slug
    uri = f"lamin://{slug}/artifact/key/lamin-run-test/input.txt"
    before = ln.Run.filter(transform__key="script.py").count()

    result = _lamin(
        "run", "--dry-run", str(script), "--", uri, "--out", "out.txt", cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert "dry run: nothing was executed or saved" in result.stderr
    assert "would run:" in result.stderr
    assert uri in result.stderr

    # nothing actually happened
    assert not marker.exists()
    assert not (tmp_path / "out.txt").exists()
    after = ln.Run.filter(transform__key="script.py").count()
    assert after == before


def test_dry_run_does_not_support_modal(tmp_path, where_setting):
    script = _script(tmp_path, "pass")
    result = _lamin("run", "--dry-run", "--where", "modal", str(script), cwd=tmp_path)
    assert result.returncode != 0
    assert "--dry-run only supports --where local" in result.stderr
