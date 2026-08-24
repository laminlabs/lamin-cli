"""The `lamin settings mount` command group."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from lamin_utils import logger

if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click

from ._backends import (
    BACKENDS_BY_NAME,
    Backend,
    available_backends,
    backends_for_protocol,
    detect,
    fuse_provider_available,
    fuse_provider_hint,
)
from ._commands import MountOptions
from ._mount import MountError, prepare_mountpoint, run_mount, unmount
from ._mount import remount as remount_storage

if TYPE_CHECKING:
    from ._resolve import StorageTarget


def _mount_options(func):
    """Options shared by all mount targets."""
    func = click.argument(
        "mountpoint", type=click.Path(file_okay=False, path_type=Path)
    )(func)
    func = click.option(
        "--backend",
        type=str,
        default=None,
        help="Mounting backend to use. By default, lamin detects installed backends and asks if several are available.",
    )(func)
    func = click.option(
        "--foreground/--daemon",
        default=True,
        help="Keep the mount in the foreground and unmount on Ctrl-C, or detach it.",
    )(func)
    func = click.option(
        "--metadata-ttl",
        type=int,
        default=0,
        show_default=True,
        help="Seconds to cache metadata. 0 always revalidates against the origin.",
    )(func)
    func = click.option(
        "--allow-other",
        is_flag=True,
        default=False,
        help="Allow other users on this machine to read the mount. By default only you can.",
    )(func)
    func = click.option(
        "--refresh-credentials/--static-credentials",
        default=True,
        help="Let the backend renew expiring credentials through lamin, where supported.",
    )(func)
    func = click.option(
        "--max-lifetime",
        type=str,
        default=None,
        help="Stop serving credentials after this duration, e.g. '12h'. The mount then fails closed.",
    )(func)
    func = click.option(
        "--reauth-every",
        type=str,
        default=None,
        help="Re-authorize against LaminHub at least this often, e.g. '15m'. Bounds how long revoked access keeps working.",
    )(func)
    func = click.option(
        "--allow-non-empty",
        is_flag=True,
        default=False,
        help="Mount even if the mountpoint already contains files.",
    )(func)
    func = click.option(
        "--dry-run",
        is_flag=True,
        default=False,
        help="Print what would be mounted without mounting.",
    )(func)
    return func


@click.group(invoke_without_command=False)
def mount():
    """Mount storage locations read-only via an installed FUSE backend.

    Mounts are always read-only: writing to a storage location behind LaminDB's back
    would create files without a corresponding record. Upload via `lamin save` or
    `ln.Artifact` instead.

    Examples:

    ```
    lamin settings mount backends                     # what can be used here
    lamin settings mount storage ./mnt                # the instance's default storage
    lamin settings mount storage --uid 3TrLu3Ab ./mnt
    lamin settings mount artifact --key my_file.parquet ./mnt
    lamin settings mount space --name my-space ./mnt
    lamin settings mount list
    ```
    """


@mount.command("backends")
@click.option(
    "--protocol",
    type=str,
    default=None,
    help="Only report backends for this protocol (local, s3, gs, hf, http, https).",
)
def backends_command(protocol: str | None):
    """List mounting backends and whether they are installed."""
    reports = detect(protocol)
    if not reports:
        raise click.ClickException(f"No backends known for protocol {protocol!r}.")

    logger.print(f"{'backend':<10} {'protocols':<22} {'installed':<10} version / docs")
    for report in reports:
        backend = report.backend
        protocols = ",".join(backend.protocols)
        status = "yes" if report.available else "no"
        detail = report.version or backend.docs_url
        logger.print(f"{backend.name:<10} {protocols:<22} {status:<10} {detail}")

    if not fuse_provider_available():
        logger.warning(f"no FUSE provider detected: {fuse_provider_hint()}")


@mount.command("list")
def list_command():
    """List active mounts."""
    from . import _registry

    records = _registry.prune()
    if not records:
        logger.print("no active mounts")
        return
    logger.print(f"{'mountpoint':<40} {'backend':<10} storage")
    for record in records:
        backend = "external" if record.external else record.backend
        logger.print(f"{record.mountpoint:<40} {backend:<10} {record.storage_root}")


def _select_backend(protocol: str, requested: str | None) -> Backend:
    """Pick a backend, asking the user when several are installed."""
    if requested is not None:
        backend = BACKENDS_BY_NAME.get(requested)
        if backend is None:
            known = ", ".join(sorted(BACKENDS_BY_NAME))
            raise click.ClickException(
                f"Unknown backend {requested!r}. Known: {known}."
            )
        if protocol not in backend.protocols:
            raise click.ClickException(
                f"Backend {requested!r} does not support protocol {protocol!r}."
            )
        if not backend.is_available():
            raise click.ClickException(
                f"Backend {requested!r} is not installed. See {backend.docs_url}"
            )
        return backend

    candidates = available_backends(protocol)
    if not candidates:
        installable = backends_for_protocol(protocol)
        hint = ", ".join(f"{b.name} ({b.docs_url})" for b in installable)
        message = f"No mounting backend installed for protocol {protocol!r}."
        if hint:
            message += f" Install one of: {hint}"
        if not fuse_provider_available():
            message += f". Also {fuse_provider_hint()}"
        raise click.ClickException(message)

    if len(candidates) == 1:
        backend = candidates[0]
        logger.important(f"using the only installed backend: {backend.name}")
        logger.print(f"docs: {backend.docs_url}")
        return backend

    names = [b.name for b in candidates]
    if not sys.stdin.isatty():
        backend = candidates[0]
        logger.important(
            f"several backends installed ({', '.join(names)}); using '{backend.name}'."
            " Pass --backend to choose."
        )
        logger.print(f"docs: {backend.docs_url}")
        return backend

    logger.print(f"several backends can mount {protocol!r}:")
    for backend in candidates:
        version = backend.version() or "installed"
        suffix = f" — {backend.notes}" if backend.notes else ""
        logger.print(f"  {backend.name:<10} {version}{suffix}")
        logger.print(f"  {'':<10} docs: {backend.docs_url}")
    choice = click.prompt(
        "which backend should be used?",
        type=click.Choice(names),
        default=names[0],
        show_choices=False,
    )
    return BACKENDS_BY_NAME[choice]


def _mount_targets(
    targets: list[StorageTarget],
    mountpoint: Path,
    backend_name: str | None,
    foreground: bool,
    metadata_ttl: int,
    allow_other: bool,
    allow_non_empty: bool,
    dry_run: bool,
    refresh_credentials: bool = True,
    max_lifetime: str | None = None,
    reauth_every: str | None = None,
) -> None:
    from ._credentials import parse_duration

    try:
        max_lifetime_delta = parse_duration(max_lifetime) if max_lifetime else None
        reauth_every_delta = parse_duration(reauth_every) if reauth_every else None
    except ValueError as error:
        raise click.ClickException(str(error)) from None
    if allow_other:
        logger.warning(
            "--allow-other makes the mount readable by every user on this machine;"
            " by default only you can read it"
        )
    multiple = len(targets) > 1
    if multiple and foreground and not dry_run:
        logger.important(
            f"mounting {len(targets)} storage locations, detaching them so that they"
            " can run side by side; unmount with 'lamin settings unmount --all'"
        )
        foreground = False

    mounted = 0
    for target in targets:
        child = mountpoint / target.slug if multiple else mountpoint
        try:
            backend = _select_backend(target.protocol, backend_name)
        except click.ClickException as error:
            if not multiple:
                raise
            logger.warning(f"skipping {target.root}: {error.message}")
            continue

        options = MountOptions(
            mountpoint=child.resolve() if child.exists() else child.absolute(),
            foreground=foreground,
            metadata_ttl=metadata_ttl,
            allow_other=allow_other,
            refresh_credentials=refresh_credentials,
            max_lifetime=max_lifetime_delta,
            reauth_every=reauth_every_delta,
        )
        try:
            if not dry_run and backend.name != "symlink":
                prepare_mountpoint(options.mountpoint, allow_non_empty=allow_non_empty)
            elif not dry_run:
                options.mountpoint.parent.mkdir(parents=True, exist_ok=True)
            run_mount(backend, target, options, dry_run=dry_run)
        except MountError as error:
            if not multiple:
                raise click.ClickException(str(error)) from None
            logger.warning(f"skipping {target.root}: {error}")
            continue
        mounted += 1

        if target.artifact_storage_key is not None:
            logger.important(
                f"artifact is at {options.mountpoint / target.artifact_storage_key}"
            )
            if (
                target.artifact_key
                and target.artifact_key != target.artifact_storage_key
            ):
                logger.print(
                    f"note: the artifact key is '{target.artifact_key}' but it is stored"
                    f" at '{target.artifact_storage_key}' because the key is virtual"
                )

    if multiple and mounted == 0:
        raise click.ClickException("Could not mount any storage location.")


# fmt: off
@mount.command("storage")
@click.option("--uid", type=str, default=None, help="The uid of the storage location.")
@click.option("--root", type=str, default=None, help="The root of the storage location, e.g. 's3://my-bucket'.")
@_mount_options
# fmt: on
def mount_storage(uid, root, mountpoint, backend, foreground, metadata_ttl, allow_other, refresh_credentials, max_lifetime, reauth_every, allow_non_empty, dry_run):
    """Mount a storage location, by default the one of the current instance."""
    from ._resolve import resolve_storage

    targets = resolve_storage(uid=uid, root=root)
    _mount_targets(targets, mountpoint, backend, foreground, metadata_ttl, allow_other, allow_non_empty, dry_run, refresh_credentials, max_lifetime, reauth_every)


# fmt: off
@mount.command("artifact")
@click.option("--uid", type=str, default=None, help="The uid of the artifact.")
@click.option("--key", type=str, default=None, help="The key of the artifact.")
@_mount_options
# fmt: on
def mount_artifact(uid, key, mountpoint, backend, foreground, metadata_ttl, allow_other, refresh_credentials, max_lifetime, reauth_every, allow_non_empty, dry_run):
    """Mount the storage location underlying an artifact."""
    from ._resolve import resolve_artifact

    targets = resolve_artifact(uid=uid, key=key)
    _mount_targets(targets, mountpoint, backend, foreground, metadata_ttl, allow_other, allow_non_empty, dry_run, refresh_credentials, max_lifetime, reauth_every)


# fmt: off
@mount.command("space")
@click.option("--name", type=str, default=None, help="The name of the space.")
@click.option("--uid", type=str, default=None, help="The uid of the space.")
@_mount_options
# fmt: on
def mount_space(name, uid, mountpoint, backend, foreground, metadata_ttl, allow_other, refresh_credentials, max_lifetime, reauth_every, allow_non_empty, dry_run):
    """Mount all storage locations managed by a space, each in a subdirectory."""
    from ._resolve import resolve_space

    targets = resolve_space(name=name, uid=uid)
    _mount_targets(targets, mountpoint, backend, foreground, metadata_ttl, allow_other, allow_non_empty, dry_run, refresh_credentials, max_lifetime, reauth_every)


# fmt: off
@mount.command("credentials", hidden=True)
@click.option("--root", type=str, required=True, help="The root of the storage location.")
@click.option("--not-after", type=str, default=None, help="Refuse to issue credentials after this UTC timestamp.")
@click.option("--reauth-seconds", type=int, default=None, help="Report an expiry this many seconds ahead to force re-authorization.")
# fmt: on
def credentials_command(root: str, not_after: str | None, reauth_seconds: int | None):
    """Print fresh AWS credentials in the `credential_process` format.

    This is invoked by the mounting backend, not by users: the AWS SDK reruns it
    before the current credentials expire, which keeps long-lived mounts working.
    Every run re-authorizes against LaminHub, so revoked access stops the mount.
    """
    from datetime import datetime, timezone

    from ._credentials import credential_process_payload, fetch_aws_credentials
    from ._lookup import stdout_to_stderr

    deadline = None
    if not_after is not None:
        deadline = datetime.strptime(not_after, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        if datetime.now(timezone.utc) >= deadline:
            raise click.ClickException(
                f"This mount reached its maximum lifetime at {not_after}. Remount to"
                " continue."
            )

    with stdout_to_stderr():
        credentials = fetch_aws_credentials(root)
    if credentials is None:
        raise click.ClickException(
            f"Could not obtain credentials for {root}. Access may have been revoked,"
            " or you may need to run: lamin login"
        )
    click.echo(credential_process_payload(credentials, deadline, reauth_seconds))


# fmt: off
@mount.command("register")
@click.argument("mountpoint", type=click.Path(file_okay=False, exists=True, path_type=Path))
@click.option("--uid", type=str, default=None, help="The uid of the storage location exposed by this mount. Auto-detected from the storage marker file if omitted.")
@click.option("--root", type=str, default=None, help="The root of the storage location exposed by this mount.")
# fmt: on
def register_command(mountpoint: Path, uid: str | None, root: str | None):
    """Register a mount that was established outside of lamin.

    Afterwards `lamin settings mount path` finds artifacts through it. Lamin never
    unmounts or remounts a registered external mount, because it does not own the
    process.

    The storage location is auto-detected by reading `.lamindb/storage_uid.txt` at the
    mountpoint, so usually no options are needed:

    ```
    lamin settings mount register /mnt/my-bucket
    ```
    """
    import lamindb_setup as ln_setup

    from . import _registry
    from ._external import read_storage_marker
    from ._resolve import resolve_storage

    if uid is not None and root is not None:
        raise click.ClickException("Pass only one of --uid or --root.")

    mountpoint = mountpoint.absolute()
    marker_uid = read_storage_marker(mountpoint)

    if uid is None and root is None:
        if marker_uid is None:
            raise click.ClickException(
                f"Could not detect a storage location at {mountpoint}: no readable"
                " '.lamindb/storage_uid.txt'. Pass --uid or --root explicitly."
            )
        uid = marker_uid
    elif marker_uid is not None and uid is not None and marker_uid != uid:
        raise click.ClickException(
            f"{mountpoint} exposes storage location {marker_uid}, not {uid}."
        )

    try:
        targets = resolve_storage(uid=uid, root=root)
    except SystemExit as error:
        if marker_uid is not None and uid == marker_uid:
            raise click.ClickException(
                f"{mountpoint} exposes storage location {marker_uid}, which is not"
                f" registered in the connected instance"
                f" ({ln_setup.settings.instance.slug}). Connect to the instance that"
                " manages it, or register it there first."
            ) from None
        raise click.ClickException(str(error)) from None
    target = targets[0]
    if marker_uid is not None and marker_uid != target.uid:
        raise click.ClickException(
            f"{mountpoint} exposes storage location {marker_uid}, but"
            f" {target.root} has uid {target.uid}."
        )

    _registry.add(
        _registry.MountRecord(
            mountpoint=str(mountpoint),
            storage_uid=target.uid,
            storage_root=target.root,
            protocol=target.protocol,
            backend="external",
            external=True,
        )
    )
    logger.important(f"registered external mount {mountpoint} -> {target.root}")


# fmt: off
@mount.command("unregister")
@click.argument("mountpoint", type=click.Path(file_okay=False, path_type=Path))
# fmt: on
def unregister_command(mountpoint: Path):
    """Forget a mount without unmounting it."""
    from . import _registry

    record = _registry.remove(str(mountpoint.absolute()))
    if record is None:
        raise click.ClickException(f"{mountpoint} is not registered.")
    logger.important(f"unregistered {mountpoint}")


# fmt: off
@mount.command("discover")
@click.argument("paths", type=str, nargs=-1)
@click.option("--register", "register_", is_flag=True, default=False, help="Register every discovered mount.")
# fmt: on
def discover_command(paths: tuple[str, ...], register_: bool):
    """Find mounts that expose a LaminDB storage location.

    Scans the operating system's mount table for userspace and network filesystems and
    reads the storage marker file at each candidate. Pass paths to probe them directly.
    """
    from . import _registry
    from ._external import discover
    from ._resolve import resolve_storage

    found = discover(list(paths) if paths else None)
    if not found:
        logger.print("no mounted storage locations found")
        return

    known = {record.mountpoint for record in _registry.load()}
    for item in found:
        state = "registered" if item.mountpoint in known else "not registered"
        logger.print(f"{item.mountpoint}  storage={item.storage_uid}  [{state}]")
        if not register_ or item.mountpoint in known:
            continue
        try:
            target = resolve_storage(uid=item.storage_uid)[0]
        except SystemExit as error:
            logger.warning(f"skipping {item.mountpoint}: {error}")
            continue
        _registry.add(
            _registry.MountRecord(
                mountpoint=item.mountpoint,
                storage_uid=target.uid,
                storage_root=target.root,
                protocol=target.protocol,
                backend="external",
                external=True,
            )
        )
        logger.important(f"registered {item.mountpoint} -> {target.root}")


# fmt: off
@mount.command("path")
@click.option("--uid", type=str, default=None, help="The uid of the artifact.")
@click.option("--key", type=str, default=None, help="The key of the artifact.")
@click.option("--mountpoint", type=click.Path(file_okay=False, path_type=Path), default=None, help="Use this mountpoint instead of looking one up in the registry.")
@click.option("--no-check", is_flag=True, default=False, help="Print the path without checking that it is readable.")
@click.option("--remount", is_flag=True, default=False, help="Remount the storage location if it serves stale metadata.")
# fmt: on
def path_command(uid, key, mountpoint, no_check, remount):
    """Print the local path of an artifact inside a mounted storage location.

    Only the path goes to stdout, so it can be used directly:

    ```
    head -c 100 "$(lamin settings mount path --key my_file.parquet)"
    ```

    If the artifact is not visible through the mount but does exist in the storage
    location, the mount is refreshed once before giving up.
    """
    from ._lookup import (
        Visibility,
        check_visibility,
        find_mount,
        local_path_for,
        resolve_artifact_location,
        stdout_to_stderr,
    )

    def note(message: str) -> None:
        # diagnostics go to stderr so that stdout stays a bare path
        click.echo(message, err=True)

    with stdout_to_stderr():
        location = resolve_artifact_location(uid=uid, key=key)

    if mountpoint is not None:
        mount_root = Path(mountpoint)
        local_path = local_path_for(mount_root, location.storage_key)
    else:
        record = find_mount(location.storage_uid, location.storage_root)
        if record is not None:
            location.mount = record
            mount_root = Path(record.mountpoint)
            local_path = local_path_for(mount_root, location.storage_key)
        elif location.protocol == "local":
            # a local storage location is readable without any mount
            mount_root = Path(location.storage_root)
            local_path = Path(location.origin)
        else:
            raise click.ClickException(
                f"Storage location {location.storage_root} is not mounted. Mount it"
                f" with: lamin settings mount storage --uid {location.storage_uid}"
                " <mountpoint>"
            )

    location.local_path = local_path

    if location.key_is_virtual:
        note(
            f"note: key '{location.key}' is virtual, the artifact is stored at"
            f" '{location.storage_key}'"
        )

    if no_check:
        click.echo(str(local_path))
        return

    visibility = check_visibility(local_path, location.origin)

    if visibility is Visibility.FOUND_AFTER_REFRESH:
        note("note: the mount served stale metadata, it was refreshed")
    elif visibility is Visibility.MISSING_IN_ORIGIN:
        raise click.ClickException(
            f"Artifact {location.artifact_uid} is recorded at {location.origin} but"
            " that path does not exist in the storage location."
        )
    elif visibility is Visibility.STALE:
        if not remount:
            remedy = (
                "Refresh it with the tool that created it."
                if location.mount is not None and location.mount.external
                else f"Retry with --remount, or: lamin settings mount refresh {mount_root}"
            )
            raise click.ClickException(
                f"{local_path} is not visible through the mount although"
                f" {location.origin} exists. The mount is stale. {remedy}"
            )
        if location.mount is None:
            raise click.ClickException(
                "Cannot remount a mountpoint that is not in the registry."
            )
        if location.mount.pid is not None:
            note(
                "warning: this mount runs in the foreground in another process, which"
                " remounting will terminate"
            )
        note(f"remounting {location.storage_root} ...")
        try:
            with stdout_to_stderr():
                record = remount_storage(location.mount)
        except MountError as error:
            raise click.ClickException(str(error)) from None
        local_path = local_path_for(record.mountpoint, location.storage_key)
        if not local_path.exists():
            raise click.ClickException(
                f"{local_path} is still not readable after remounting."
            )

    click.echo(str(local_path))


# fmt: off
@mount.command("refresh")
@click.argument("mountpoint", type=click.Path(file_okay=False, path_type=Path), required=False)
@click.option("--all", "all_", is_flag=True, default=False, help="Refresh all mounts created by lamin.")
# fmt: on
def refresh_command(mountpoint: Path | None, all_: bool):
    """Remount a storage location so that it picks up changes in the origin."""
    from . import _registry

    if all_ and mountpoint is not None:
        raise click.ClickException("Pass either a mountpoint or --all, not both.")
    if not all_ and mountpoint is None:
        raise click.ClickException("Pass a mountpoint or --all.")

    records = _registry.prune()
    if mountpoint is not None:
        wanted = str(mountpoint.absolute())
        records = [r for r in records if r.mountpoint == wanted]
        if not records:
            raise click.ClickException(f"{mountpoint} is not a mount created by lamin.")
    else:
        external = [r for r in records if r.external]
        if external:
            logger.print(
                f"skipping {len(external)} mount(s) established outside of lamin"
            )
        records = [r for r in records if not r.external]

    if not records:
        logger.print("no active mounts")
        return

    for record in records:
        try:
            remount_storage(record)
        except MountError as error:
            raise click.ClickException(str(error)) from None
        logger.important(f"refreshed {record.mountpoint}")


# fmt: off
@click.command("unmount")
@click.argument("mountpoint", type=click.Path(file_okay=False, path_type=Path), required=False)
@click.option("--all", "all_", is_flag=True, default=False, help="Unmount all mounts created by lamin.")
# fmt: on
def unmount_command(mountpoint: Path | None, all_: bool):
    """Unmount a storage location mounted by `lamin settings mount`."""
    from . import _registry

    if all_ and mountpoint is not None:
        raise click.ClickException("Pass either a mountpoint or --all, not both.")
    if not all_ and mountpoint is None:
        raise click.ClickException("Pass a mountpoint or --all.")

    if all_:
        records = [r for r in _registry.load() if not r.external]
        skipped = [r for r in _registry.load() if r.external]
        if skipped:
            logger.print(
                f"skipping {len(skipped)} mount(s) established outside of lamin"
            )
        if not records:
            logger.print("no active mounts created by lamin")
            return
        failed = []
        for record in records:
            try:
                unmount(Path(record.mountpoint))
                logger.important(f"unmounted {record.mountpoint}")
            except MountError as error:
                failed.append(str(error))
        if failed:
            raise click.ClickException("; ".join(failed))
        return

    if mountpoint is None:
        raise click.ClickException("Pass a mountpoint or --all.")
    try:
        unmount(mountpoint.absolute())
    except MountError as error:
        raise click.ClickException(str(error)) from None
    logger.important(f"unmounted {mountpoint}")
