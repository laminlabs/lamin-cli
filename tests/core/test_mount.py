from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from lamin_cli.mount import _backends, _registry
from lamin_cli.mount._commands import MountOptions, build_command
from lamin_cli.mount._credentials import credential_env, endpoint_url, is_anonymous
from lamin_cli.mount._resolve import StorageTarget, split_root


@pytest.fixture(autouse=True)
def _isolate_settings_dir(tmp_path, monkeypatch):
    """Keep profile files that backends need out of the real settings directory."""
    import lamindb_setup.core._settings_store as store

    monkeypatch.setattr(store, "settings_dir", tmp_path / "lamin-settings")
    return tmp_path


CREDENTIALS = {
    "key": "AKIAEXAMPLE",
    "secret": "s3cr3t",
    "token": "session-token",
}


def _target(
    root: str, protocol: str, storage_options: dict | None = None
) -> StorageTarget:
    options = CREDENTIALS if storage_options is None else storage_options
    fs = SimpleNamespace(storage_options=options, **options)
    path = SimpleNamespace(storage_options=options, fs=fs, path=root)
    return StorageTarget(uid="3TrLu3Ab0000", root=root, protocol=protocol, path=path)


def _options(**kwargs) -> MountOptions:
    kwargs.setdefault("mountpoint", Path("/tmp/mnt"))
    return MountOptions(**kwargs)


# -- backend registry --------------------------------------------------------


def test_every_protocol_has_a_backend():
    for protocol in ("local", "s3", "gs", "hf", "http", "https"):
        assert _backends.backends_for_protocol(protocol), protocol


def test_backends_are_sorted_by_preference():
    names = [b.name for b in _backends.backends_for_protocol("s3")]
    assert names.index("mount-s3") < names.index("s3fs")
    assert names.index("mount-s3") < names.index(_backends.FSSPEC_BACKEND)


def test_available_backends_filters_on_installation(monkeypatch):
    monkeypatch.setattr(
        _backends.shutil,
        "which",
        lambda name: "/usr/bin/mount-s3" if name == "mount-s3" else None,
    )
    monkeypatch.setattr(_backends, "_fsspec_fuse_available", lambda: False)
    assert [b.name for b in _backends.available_backends("s3")] == ["mount-s3"]


def test_local_falls_back_to_symlink(monkeypatch):
    monkeypatch.setattr(_backends.shutil, "which", lambda name: None)
    assert [b.name for b in _backends.available_backends("local")] == ["symlink"]


def test_symlink_does_not_report_fsspec_version():
    assert _backends.BACKENDS_BY_NAME["symlink"].version() == "built-in"


# -- root parsing ------------------------------------------------------------


@pytest.mark.parametrize(
    ("root", "protocol", "expected"),
    [
        ("s3://my-bucket", "s3", ("my-bucket", "", None)),
        ("s3://my-bucket/some/prefix", "s3", ("my-bucket", "some/prefix", None)),
        ("gs://my-bucket/prefix", "gs", ("my-bucket", "prefix", None)),
        ("/data/store", "local", ("/data/store", "", None)),
        (
            "s3://my-bucket?endpoint_url=https://s3.example.com",
            "s3",
            ("my-bucket", "", "https://s3.example.com"),
        ),
    ],
)
def test_split_root(root, protocol, expected):
    assert split_root(root, protocol) == expected


# -- credentials -------------------------------------------------------------


def test_credential_env_extracts_session_credentials():
    env = credential_env(_target("s3://b", "s3").path, "s3")
    assert env == {
        "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "s3cr3t",
        "AWS_SESSION_TOKEN": "session-token",
    }


def test_anonymous_access_passes_no_credentials():
    target = _target("s3://b", "s3", storage_options={"anon": True})
    assert is_anonymous(target.path)
    assert credential_env(target.path, "s3") == {}


def test_endpoint_url_from_client_kwargs():
    target = _target(
        "s3://b", "s3", storage_options={"client_kwargs": {"endpoint_url": "https://x"}}
    )
    assert endpoint_url(target.path) == "https://x"


def test_no_credentials_for_non_s3_protocols():
    assert credential_env(_target("gs://b", "gs").path, "gs") == {}


# -- command construction ----------------------------------------------------

EXTERNAL_BACKENDS = ["mount-s3", "s3fs", "goofys", "rclone", "gcsfuse", "bindfs"]


@pytest.mark.parametrize("backend_name", EXTERNAL_BACKENDS)
def test_every_external_backend_mounts_read_only(backend_name):
    backend = _backends.BACKENDS_BY_NAME[backend_name]
    protocol = "local" if backend_name == "bindfs" else backend.protocols[0]
    root = "/data/store" if protocol == "local" else f"{protocol}://my-bucket"
    command = build_command(backend, _target(root, protocol), _options())
    joined = " ".join(command.argv)
    assert "ro" in joined or "--read-only" in joined
    assert command.enforces_read_only


@pytest.mark.parametrize("backend_name", EXTERNAL_BACKENDS)
def test_secrets_never_appear_in_argv(backend_name):
    backend = _backends.BACKENDS_BY_NAME[backend_name]
    protocol = "local" if backend_name == "bindfs" else backend.protocols[0]
    root = "/data/store" if protocol == "local" else f"{protocol}://my-bucket"
    command = build_command(backend, _target(root, protocol), _options())
    joined = " ".join(command.argv)
    for secret in CREDENTIALS.values():
        assert secret not in joined


def test_mount_s3_argv():
    backend = _backends.BACKENDS_BY_NAME["mount-s3"]
    target = _target("s3://my-bucket/some/prefix", "s3")
    command = build_command(backend, target, _options())
    assert command.argv[:4] == ["mount-s3", "my-bucket", "/tmp/mnt", "--read-only"]
    # a prefix must be passed with a trailing slash
    assert "--prefix" in command.argv
    assert command.argv[command.argv.index("--prefix") + 1] == "some/prefix/"
    # ttl 0 means always revalidate against the origin
    assert command.argv[command.argv.index("--metadata-ttl") + 1] == "minimal"
    assert "--foreground" in command.argv


# -- expiring credentials ----------------------------------------------------


def test_expiring_credentials_use_a_refreshing_profile():
    backend = _backends.BACKENDS_BY_NAME["mount-s3"]
    command = build_command(backend, _target("s3://my-bucket", "s3"), _options())
    assert command.refreshes_credentials
    assert command.argv[command.argv.index("--profile") + 1] == "lamin"
    # a static snapshot must not be handed over, it would outlive its validity
    assert "AWS_SESSION_TOKEN" not in command.env
    assert "AWS_CONFIG_FILE" in command.env


def test_profile_holds_no_secrets_and_is_private():
    backend = _backends.BACKENDS_BY_NAME["mount-s3"]
    command = build_command(backend, _target("s3://my-bucket", "s3"), _options())
    config = Path(command.env["AWS_CONFIG_FILE"])
    text = config.read_text()
    assert "credential_process" in text
    for secret in CREDENTIALS.values():
        assert secret not in text
    assert config.stat().st_mode & 0o777 == 0o600


def test_static_credentials_can_be_forced():
    backend = _backends.BACKENDS_BY_NAME["mount-s3"]
    command = build_command(
        backend, _target("s3://my-bucket", "s3"), _options(refresh_credentials=False)
    )
    assert not command.refreshes_credentials
    assert command.env["AWS_SESSION_TOKEN"] == "session-token"
    assert "--profile" not in command.argv


def test_long_term_credentials_are_not_wrapped_in_a_profile():
    backend = _backends.BACKENDS_BY_NAME["mount-s3"]
    target = _target(
        "s3://b", "s3", storage_options={"key": "AKIA", "secret": "s3cr3t"}
    )
    command = build_command(backend, target, _options())
    # without a session token there is nothing to expire
    assert not command.refreshes_credentials
    assert command.env["AWS_ACCESS_KEY_ID"] == "AKIA"


def test_credential_process_payload_matches_the_aws_schema():
    import json

    from lamin_cli.mount._credentials import credential_process_payload

    payload = json.loads(
        credential_process_payload(
            {
                "key": "AKIA",
                "secret": "s3cr3t",
                "token": "session-token",
                "expiry_time": "2030-01-01T00:00:00+00:00",
            }
        )
    )
    assert payload["Version"] == 1
    assert payload["AccessKeyId"] == "AKIA"
    assert payload["SecretAccessKey"] == "s3cr3t"
    assert payload["SessionToken"] == "session-token"
    # an Expiration is what makes the SDK rerun the command before it expires
    assert payload["Expiration"] == "2030-01-01T00:00:00Z"


def test_payload_without_expiry_is_treated_as_long_term():
    import json

    from lamin_cli.mount._credentials import credential_process_payload

    payload = json.loads(credential_process_payload({"key": "A", "secret": "B"}))
    assert "Expiration" not in payload
    assert "SessionToken" not in payload


def test_mount_s3_passes_endpoint_and_anonymous_flag():
    backend = _backends.BACKENDS_BY_NAME["mount-s3"]
    target = _target(
        "s3://my-bucket",
        "s3",
        storage_options={"anon": True, "client_kwargs": {"endpoint_url": "https://x"}},
    )
    command = build_command(backend, target, _options())
    assert "--no-sign-request" in command.argv
    assert command.argv[command.argv.index("--endpoint-url") + 1] == "https://x"


def test_metadata_ttl_is_forwarded():
    backend = _backends.BACKENDS_BY_NAME["mount-s3"]
    command = build_command(backend, _target("s3://b", "s3"), _options(metadata_ttl=60))
    assert command.argv[command.argv.index("--metadata-ttl") + 1] == "60"


def test_gcsfuse_argv():
    backend = _backends.BACKENDS_BY_NAME["gcsfuse"]
    command = build_command(backend, _target("gs://my-bucket/pre", "gs"), _options())
    assert command.argv[-2:] == ["my-bucket", "/tmp/mnt"]
    assert command.argv[command.argv.index("--only-dir") + 1] == "pre"
    assert "--metadata-cache-ttl-secs=0" in command.argv


def test_rclone_passes_credentials_through_env_only():
    backend = _backends.BACKENDS_BY_NAME["rclone"]
    command = build_command(backend, _target("s3://my-bucket/pre", "s3"), _options())
    assert ":s3:my-bucket/pre" in command.argv
    assert "--read-only" in command.argv
    assert command.env["RCLONE_S3_SESSION_TOKEN"] == "session-token"
    assert "session-token" not in " ".join(command.argv)


def test_symlink_backend_reports_that_it_cannot_enforce_read_only():
    backend = _backends.BACKENDS_BY_NAME["symlink"]
    command = build_command(backend, _target("/data/store", "local"), _options())
    assert not command.enforces_read_only
    assert command.in_process


def test_extra_args_are_appended():
    backend = _backends.BACKENDS_BY_NAME["mount-s3"]
    command = build_command(
        backend, _target("s3://b", "s3"), _options(extra_args=("--debug",))
    )
    assert command.argv[-1] == "--debug"


# -- mount registry ----------------------------------------------------------


def test_registry_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(_registry, "_registry_path", lambda: tmp_path / "mounts.json")
    record = _registry.MountRecord(
        mountpoint="/tmp/mnt",
        storage_uid="3TrLu3Ab0000",
        storage_root="s3://my-bucket",
        protocol="s3",
        backend="mount-s3",
    )
    _registry.add(record)
    assert [r.mountpoint for r in _registry.load()] == ["/tmp/mnt"]
    # adding the same mountpoint twice must not duplicate it
    _registry.add(record)
    assert len(_registry.load()) == 1
    assert _registry.remove("/tmp/mnt").storage_uid == "3TrLu3Ab0000"
    assert _registry.load() == []


def test_registry_prunes_dead_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(_registry, "_registry_path", lambda: tmp_path / "mounts.json")
    _registry.add(
        _registry.MountRecord(
            mountpoint="/tmp/dead",
            storage_uid="u",
            storage_root="s3://b",
            protocol="s3",
            backend="mount-s3",
            pid=2**22,  # a pid that cannot be running
        )
    )
    assert _registry.prune() == []


# -- locating artifacts inside a mount ---------------------------------------


def test_local_path_maps_storage_key_onto_the_mountpoint():
    from lamin_cli.mount._lookup import local_path_for

    # the mountpoint always corresponds to the storage root, so the physical
    # storage key can be appended verbatim
    assert local_path_for("/mnt/store", ".lamindb/abc123.txt") == Path(
        "/mnt/store/.lamindb/abc123.txt"
    )


def test_find_mount_prefers_storage_uid(tmp_path, monkeypatch):
    monkeypatch.setattr(_registry, "_registry_path", lambda: tmp_path / "mounts.json")
    from lamin_cli.mount._lookup import find_mount

    _registry.add(
        _registry.MountRecord(
            mountpoint=str(tmp_path / "other"),
            storage_uid="other0000000",
            storage_root="s3://other",
            protocol="s3",
            backend="mount-s3",
            in_process=True,
        )
    )
    _registry.add(
        _registry.MountRecord(
            mountpoint=str(tmp_path / "wanted"),
            storage_uid="wanted000000",
            storage_root="s3://wanted",
            protocol="s3",
            backend="mount-s3",
            in_process=True,
        )
    )
    (tmp_path / "other").mkdir()
    (tmp_path / "wanted").mkdir()
    record = find_mount("wanted000000", "s3://wanted")
    assert record is not None
    assert record.storage_root == "s3://wanted"


def test_find_mount_returns_none_when_not_mounted(tmp_path, monkeypatch):
    monkeypatch.setattr(_registry, "_registry_path", lambda: tmp_path / "mounts.json")
    from lamin_cli.mount._lookup import find_mount

    assert find_mount("nope00000000", "s3://nope") is None


def test_visible_when_the_file_is_there(tmp_path):
    from lamin_cli.mount import _lookup

    target = tmp_path / ".lamindb" / "abc.txt"
    target.parent.mkdir(parents=True)
    target.write_text("data")
    assert _lookup.check_visibility(target, "s3://b/.lamindb/abc.txt") is (
        _lookup.Visibility.FOUND
    )


def test_missing_in_origin_is_distinguished_from_a_stale_mount(tmp_path, monkeypatch):
    from lamin_cli.mount import _lookup

    monkeypatch.setattr(_lookup, "origin_exists", lambda origin: False)
    missing = tmp_path / ".lamindb" / "abc.txt"
    assert _lookup.check_visibility(missing, "s3://b/.lamindb/abc.txt") is (
        _lookup.Visibility.MISSING_IN_ORIGIN
    )


def test_stale_mount_is_detected_when_origin_has_the_file(tmp_path, monkeypatch):
    from lamin_cli.mount import _lookup

    # the origin is authoritative and says the object exists ...
    monkeypatch.setattr(_lookup, "origin_exists", lambda origin: True)
    missing = tmp_path / ".lamindb" / "abc.txt"
    missing.parent.mkdir(parents=True)
    # ... but it never shows up through the mount
    assert _lookup.check_visibility(missing, "s3://b/.lamindb/abc.txt") is (
        _lookup.Visibility.STALE
    )


def test_refresh_makes_a_stale_entry_visible(tmp_path, monkeypatch):
    from lamin_cli.mount import _lookup

    monkeypatch.setattr(_lookup, "origin_exists", lambda origin: True)
    target = tmp_path / ".lamindb" / "abc.txt"
    target.parent.mkdir(parents=True)

    # emulate a backend that reveals the entry once its parent is listed
    def invalidate(path):
        target.write_text("data")

    monkeypatch.setattr(_lookup, "invalidate", invalidate)
    assert _lookup.check_visibility(target, "s3://b/.lamindb/abc.txt") is (
        _lookup.Visibility.FOUND_AFTER_REFRESH
    )


def test_invalidate_survives_a_disconnected_mount(tmp_path):
    from lamin_cli.mount._lookup import invalidate

    # must not raise even though nothing here exists
    invalidate(tmp_path / "gone" / "deeper" / "file.txt")


# -- mounts managed outside of lamin -----------------------------------------


def _write_marker(root: Path, uid: str, legacy: bool = False) -> None:
    key = ".lamindb/_is_initialized" if legacy else ".lamindb/storage_uid.txt"
    marker = root / key
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        f"{uid}\ncreation info:\ninstance_slug=acc/inst\ninstance_id=abc\n"
    )


def test_storage_marker_identifies_the_mounted_storage(tmp_path):
    from lamin_cli.mount._external import read_storage_marker

    _write_marker(tmp_path, "3TrLu3Ab0000")
    assert read_storage_marker(tmp_path) == "3TrLu3Ab0000"


def test_legacy_storage_marker_is_supported(tmp_path):
    from lamin_cli.mount._external import read_storage_marker

    _write_marker(tmp_path, "3TrLu3Ab0000", legacy=True)
    assert read_storage_marker(tmp_path) == "3TrLu3Ab0000"


def test_no_marker_returns_none(tmp_path):
    from lamin_cli.mount._external import read_storage_marker

    assert read_storage_marker(tmp_path) is None
    assert read_storage_marker(tmp_path / "does-not-exist") is None


def test_proc_mounts_is_parsed(tmp_path):
    from lamin_cli.mount._external import _parse_proc_mounts

    text = (
        "proc /proc proc rw,relatime 0 0\n"
        "mountpoint-s3 /mnt/bucket fuse rw,nosuid,nodev 0 0\n"
        "rclone /mnt/with\\040space fuse.rclone ro 0 0\n"
    )
    mounts = _parse_proc_mounts(text)
    assert [m.mountpoint for m in mounts] == ["/proc", "/mnt/bucket", "/mnt/with space"]
    assert mounts[2].fstype == "fuse.rclone"


def test_bsd_mount_output_is_parsed():
    from lamin_cli.mount._external import _parse_bsd_mount

    text = (
        "/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)\n"
        "mountpoint-s3 on /Users/me/mnt (macfuse, nodev, nosuid, mounted by me)\n"
    )
    mounts = _parse_bsd_mount(text)
    assert mounts[0].mountpoint == "/"
    assert mounts[1].mountpoint == "/Users/me/mnt"
    assert mounts[1].fstype == "macfuse"


def test_only_userspace_and_network_mounts_are_probed(monkeypatch):
    from lamin_cli.mount import _external

    monkeypatch.setattr(
        _external,
        "system_mounts",
        lambda: [
            _external.SystemMount("/", "/dev/disk1", "apfs"),
            _external.SystemMount("/mnt/bucket", "mountpoint-s3", "macfuse"),
            _external.SystemMount("/mnt/share", "server:/x", "nfs"),
        ],
    )
    assert [m.mountpoint for m in _external.candidate_mounts()] == [
        "/mnt/bucket",
        "/mnt/share",
    ]


def test_discover_finds_storage_locations_behind_mounts(tmp_path, monkeypatch):
    from lamin_cli.mount import _external

    mounted = tmp_path / "bucket"
    mounted.mkdir()
    _write_marker(mounted, "3TrLu3Ab0000")
    plain = tmp_path / "plain"
    plain.mkdir()

    monkeypatch.setattr(
        _external,
        "system_mounts",
        lambda: [
            _external.SystemMount(str(mounted), "mountpoint-s3", "macfuse"),
            _external.SystemMount(str(plain), "other", "macfuse"),
        ],
    )
    found = _external.discover()
    assert len(found) == 1
    assert found[0].storage_uid == "3TrLu3Ab0000"


def test_discover_accepts_explicit_paths(tmp_path):
    from lamin_cli.mount._external import discover

    _write_marker(tmp_path, "3TrLu3Ab0000")
    found = discover([str(tmp_path)])
    assert [f.storage_uid for f in found] == ["3TrLu3Ab0000"]


def test_external_mounts_are_never_unmounted_by_lamin(tmp_path, monkeypatch):
    from lamin_cli.mount._mount import MountError, unmount

    monkeypatch.setattr(_registry, "_registry_path", lambda: tmp_path / "mounts.json")
    mountpoint = tmp_path / "external"
    mountpoint.mkdir()
    _registry.add(
        _registry.MountRecord(
            mountpoint=str(mountpoint),
            storage_uid="3TrLu3Ab0000",
            storage_root="s3://my-bucket",
            protocol="s3",
            backend="external",
            external=True,
        )
    )
    with pytest.raises(MountError, match="outside of lamin"):
        unmount(mountpoint)
    # the record must survive a refused unmount
    assert len(_registry.load()) == 1


def test_external_mounts_are_never_remounted_by_lamin(tmp_path, monkeypatch):
    from lamin_cli.mount._mount import MountError, remount

    monkeypatch.setattr(_registry, "_registry_path", lambda: tmp_path / "mounts.json")
    record = _registry.MountRecord(
        mountpoint=str(tmp_path / "external"),
        storage_uid="3TrLu3Ab0000",
        storage_root="s3://my-bucket",
        protocol="s3",
        backend="external",
        external=True,
    )
    with pytest.raises(MountError, match="cannot remount"):
        remount(record)


def test_external_record_liveness_follows_the_mountpoint(tmp_path):
    record = _registry.MountRecord(
        mountpoint=str(tmp_path / "gone"),
        storage_uid="u",
        storage_root="s3://b",
        protocol="s3",
        backend="external",
        external=True,
    )
    assert not record.is_alive
    (tmp_path / "gone").mkdir()
    assert record.is_alive


def test_managed_storage_refreshes_even_without_a_cached_token():
    # credentials may not be loaded into fsspec yet; being hub-managed is enough
    backend = _backends.BACKENDS_BY_NAME["mount-s3"]
    target = _target("s3://my-bucket", "s3", storage_options={})
    target.managed = True
    command = build_command(backend, target, _options())
    assert command.refreshes_credentials
    assert "--profile" in command.argv


def test_anonymous_access_never_uses_a_credential_profile():
    backend = _backends.BACKENDS_BY_NAME["mount-s3"]
    target = _target("s3://my-bucket", "s3", storage_options={"anon": True})
    target.managed = True
    command = build_command(backend, target, _options())
    assert not command.refreshes_credentials
    assert "--no-sign-request" in command.argv
    assert command.env == {}


# -- bounding how long access survives revocation ----------------------------


def test_duration_parsing():
    from datetime import timedelta

    from lamin_cli.mount._credentials import parse_duration

    assert parse_duration("30s") == timedelta(seconds=30)
    assert parse_duration("15m") == timedelta(minutes=15)
    assert parse_duration("12h") == timedelta(hours=12)
    assert parse_duration("7d") == timedelta(days=7)
    with pytest.raises(ValueError, match="Could not parse duration"):
        parse_duration("soon")


def test_reauth_interval_shortens_the_reported_expiry():
    import json
    from datetime import datetime, timedelta, timezone

    from lamin_cli.mount._credentials import credential_process_payload

    far_future = datetime.now(timezone.utc) + timedelta(hours=10)
    payload = json.loads(
        credential_process_payload(
            {"key": "A", "secret": "B", "token": "C", "expiry_time": far_future},
            reauth_seconds=900,
        )
    )
    reported = datetime.strptime(payload["Expiration"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    # the SDK must come back within the re-authorization window, not in 10 hours
    assert reported <= datetime.now(timezone.utc) + timedelta(seconds=901)


def test_max_lifetime_caps_the_reported_expiry():
    import json
    from datetime import datetime, timedelta, timezone

    from lamin_cli.mount._credentials import credential_process_payload

    far_future = datetime.now(timezone.utc) + timedelta(hours=10)
    deadline = datetime.now(timezone.utc) + timedelta(hours=1)
    payload = json.loads(
        credential_process_payload(
            {"key": "A", "secret": "B", "expiry_time": far_future},
            not_after=deadline,
        )
    )
    assert payload["Expiration"] == deadline.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_lifetime_options_reach_the_profile():
    from datetime import timedelta

    backend = _backends.BACKENDS_BY_NAME["mount-s3"]
    command = build_command(
        backend,
        _target("s3://my-bucket", "s3"),
        _options(max_lifetime=timedelta(hours=12), reauth_every=timedelta(minutes=15)),
    )
    text = Path(command.env["AWS_CONFIG_FILE"]).read_text()
    assert "--not-after" in text
    assert "--reauth-seconds 900" in text
