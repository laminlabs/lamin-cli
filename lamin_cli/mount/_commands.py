"""Build the argv/env for each mounting backend.

Every builder must produce a read-only mount. A backend that cannot enforce read-only
raises, rather than silently mounting something writable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from ._credentials import (
    PROFILE_NAME,
    credential_env,
    endpoint_url,
    has_temporary_credentials,
    is_anonymous,
    write_profile_config,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ._backends import Backend
    from ._resolve import StorageTarget


class ReadOnlyNotSupported(Exception):
    """Raised when a backend cannot guarantee a read-only mount."""


def _executable(backend: Backend) -> str:
    if backend.executable is None:
        raise ReadOnlyNotSupported(
            f"Backend {backend.name!r} does not run as an external tool."
        )
    return backend.executable


@dataclass
class MountCommand:
    argv: list[str]
    env: dict[str, str]
    # backends that cannot be kept in the foreground are tracked differently
    runs_in_foreground: bool = True
    enforces_read_only: bool = True
    # in-process backends have no argv
    in_process: bool = False
    # whether the mount can obtain fresh credentials after the current ones expire
    refreshes_credentials: bool = False


@dataclass
class MountOptions:
    mountpoint: Path
    foreground: bool = True
    # 0 means "always revalidate", which keeps the mount consistent with the origin
    metadata_ttl: int = 0
    allow_other: bool = False
    # let the backend refresh expiring credentials through lamin where supported
    refresh_credentials: bool = True
    # hard deadline after which the mount stops getting credentials
    max_lifetime: timedelta | None = None
    # how often the backend must re-authorize against LaminHub
    reauth_every: timedelta | None = None
    extra_args: tuple[str, ...] = field(default_factory=tuple)


def build_command(
    backend: Backend, target: StorageTarget, options: MountOptions
) -> MountCommand:
    builder = _BUILDERS.get(backend.name)
    if builder is None:
        raise ReadOnlyNotSupported(
            f"No command builder implemented for backend {backend.name!r}."
        )
    command = builder(backend, target, options)
    command.argv.extend(options.extra_args)
    return command


def _prefix_with_slash(prefix: str) -> str:
    return f"{prefix}/" if prefix and not prefix.endswith("/") else prefix


def _build_mount_s3(
    backend: Backend, target: StorageTarget, options: MountOptions
) -> MountCommand:
    from ._resolve import split_root

    bucket, prefix, root_endpoint = split_root(target.root, target.protocol)
    argv = [_executable(backend), bucket, str(options.mountpoint), "--read-only"]
    if prefix:
        argv += ["--prefix", _prefix_with_slash(prefix)]
    endpoint = endpoint_url(target.path) or root_endpoint
    if endpoint:
        argv += ["--endpoint-url", endpoint]
    argv += [
        "--metadata-ttl",
        "minimal" if options.metadata_ttl == 0 else str(options.metadata_ttl),
    ]
    if is_anonymous(target.path):
        argv.append("--no-sign-request")
    if options.allow_other:
        argv.append("--allow-other")
    if options.foreground:
        argv.extend(backend.foreground_args)

    env = credential_env(target.path, target.protocol)
    refreshes = False
    needs_refresh = has_temporary_credentials(env) or target.managed
    if needs_refresh and options.refresh_credentials and not is_anonymous(target.path):
        # a static snapshot of federated credentials would break the mount once it
        # expires; a credential_process is rerun by the AWS SDK before that happens
        not_after = (
            datetime.now(timezone.utc) + options.max_lifetime
            if options.max_lifetime is not None
            else None
        )
        reauth_seconds = (
            int(options.reauth_every.total_seconds())
            if options.reauth_every is not None
            else None
        )
        config_path = write_profile_config(
            target.root, endpoint, not_after, reauth_seconds
        )
        argv += ["--profile", PROFILE_NAME]
        # env credentials would take precedence over the profile, so drop them
        env = {"AWS_CONFIG_FILE": str(config_path)}
        refreshes = True

    return MountCommand(
        argv=argv,
        env=env,
        runs_in_foreground=options.foreground,
        refreshes_credentials=refreshes,
    )


def _build_gcsfuse(
    backend: Backend, target: StorageTarget, options: MountOptions
) -> MountCommand:
    from ._resolve import split_root

    bucket, prefix, _ = split_root(target.root, target.protocol)
    argv = [_executable(backend), "-o", "ro", "--implicit-dirs"]
    if prefix:
        argv += ["--only-dir", prefix]
    argv += [f"--metadata-cache-ttl-secs={options.metadata_ttl}"]
    if options.allow_other:
        argv += ["-o", "allow_other"]
    if options.foreground:
        argv.extend(backend.foreground_args)
    argv += [bucket, str(options.mountpoint)]
    return MountCommand(argv=argv, env={}, runs_in_foreground=options.foreground)


def _build_rclone(
    backend: Backend, target: StorageTarget, options: MountOptions
) -> MountCommand:
    from ._resolve import split_root

    container, prefix, root_endpoint = split_root(target.root, target.protocol)
    remote_type = {"s3": "s3", "gs": "gcs", "http": "http", "https": "http"}[
        target.protocol
    ]
    remote = f":{remote_type}:{container}"
    if prefix:
        remote = f"{remote}/{prefix}"
    argv = [
        _executable(backend),
        "mount",
        remote,
        str(options.mountpoint),
        "--read-only",
        f"--dir-cache-time={options.metadata_ttl}s",
    ]
    if options.allow_other:
        argv.append("--allow-other")
    if not options.foreground:
        argv.append("--daemon")
    env = credential_env(target.path, target.protocol)
    rclone_env = {}
    if target.protocol == "s3":
        rclone_env["RCLONE_S3_PROVIDER"] = "AWS"
        if "AWS_ACCESS_KEY_ID" in env:
            rclone_env["RCLONE_S3_ACCESS_KEY_ID"] = env["AWS_ACCESS_KEY_ID"]
            rclone_env["RCLONE_S3_SECRET_ACCESS_KEY"] = env["AWS_SECRET_ACCESS_KEY"]
            if "AWS_SESSION_TOKEN" in env:
                rclone_env["RCLONE_S3_SESSION_TOKEN"] = env["AWS_SESSION_TOKEN"]
        else:
            rclone_env["RCLONE_S3_ENV_AUTH"] = "true"
        endpoint = endpoint_url(target.path) or root_endpoint
        if endpoint:
            rclone_env["RCLONE_S3_ENDPOINT"] = endpoint
    return MountCommand(
        argv=argv,
        env={**env, **rclone_env},
        runs_in_foreground=options.foreground,
    )


def _build_goofys(
    backend: Backend, target: StorageTarget, options: MountOptions
) -> MountCommand:
    from ._resolve import split_root

    bucket, prefix, root_endpoint = split_root(target.root, target.protocol)
    source = f"{bucket}:{prefix}" if prefix else bucket
    argv = [
        _executable(backend),
        "-o",
        "ro",
        f"--stat-cache-ttl={options.metadata_ttl}s",
    ]
    argv += [f"--type-cache-ttl={options.metadata_ttl}s"]
    endpoint = endpoint_url(target.path) or root_endpoint
    if endpoint:
        argv += ["--endpoint", endpoint]
    if options.allow_other:
        argv += ["-o", "allow_other"]
    if options.foreground:
        argv.extend(backend.foreground_args)
    argv += [source, str(options.mountpoint)]
    return MountCommand(
        argv=argv,
        env=credential_env(target.path, target.protocol),
        runs_in_foreground=options.foreground,
    )


def _build_s3fs(
    backend: Backend, target: StorageTarget, options: MountOptions
) -> MountCommand:
    from ._resolve import split_root

    bucket, prefix, root_endpoint = split_root(target.root, target.protocol)
    source = f"{bucket}:/{prefix}" if prefix else bucket
    argv = [_executable(backend), source, str(options.mountpoint), "-o", "ro"]
    argv += ["-o", f"stat_cache_expire={options.metadata_ttl}"]
    endpoint = endpoint_url(target.path) or root_endpoint
    if endpoint:
        argv += ["-o", f"url={endpoint}", "-o", "use_path_request_style"]
    if is_anonymous(target.path):
        argv += ["-o", "public_bucket=1"]
    if options.allow_other:
        argv += ["-o", "allow_other"]
    if options.foreground:
        argv.extend(backend.foreground_args)
    return MountCommand(
        argv=argv,
        env=credential_env(target.path, target.protocol),
        runs_in_foreground=options.foreground,
    )


def _build_bindfs(
    backend: Backend, target: StorageTarget, options: MountOptions
) -> MountCommand:
    argv = [_executable(backend), "-o", "ro", target.root, str(options.mountpoint)]
    if options.allow_other:
        argv += ["-o", "allow_other"]
    if options.foreground:
        argv.extend(backend.foreground_args)
    return MountCommand(argv=argv, env={}, runs_in_foreground=options.foreground)


def _build_symlink(
    backend: Backend, target: StorageTarget, options: MountOptions
) -> MountCommand:
    # a symlink cannot enforce read-only; the caller warns about this
    return MountCommand(
        argv=[],
        env={},
        runs_in_foreground=False,
        enforces_read_only=False,
        in_process=True,
    )


def _build_fsspec(
    backend: Backend, target: StorageTarget, options: MountOptions
) -> MountCommand:
    return MountCommand(
        argv=[],
        env={},
        runs_in_foreground=options.foreground,
        in_process=True,
    )


_BUILDERS = {
    "mount-s3": _build_mount_s3,
    "gcsfuse": _build_gcsfuse,
    "rclone": _build_rclone,
    "goofys": _build_goofys,
    "s3fs": _build_s3fs,
    "bindfs": _build_bindfs,
    "symlink": _build_symlink,
    "fsspec": _build_fsspec,
}
