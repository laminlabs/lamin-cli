"""Execute and tear down mounts."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from lamin_utils import logger

from . import _registry
from ._commands import MountCommand, MountOptions, build_command

if TYPE_CHECKING:
    from ._backends import Backend
    from ._resolve import StorageTarget


class MountError(Exception):
    """Raised when a mount cannot be established or torn down."""


def prepare_mountpoint(mountpoint: Path, allow_non_empty: bool = False) -> None:
    """Create the mountpoint and refuse to shadow existing data."""
    if mountpoint.is_symlink():
        raise MountError(
            f"{mountpoint} is already a symlink. Run"
            f" 'lamin settings unmount {mountpoint}' first."
        )
    if mountpoint.exists():
        if not mountpoint.is_dir():
            raise MountError(f"{mountpoint} exists and is not a directory.")
        if any(mountpoint.iterdir()) and not allow_non_empty:
            raise MountError(
                f"{mountpoint} is not empty. Mounting would hide its contents. Pass"
                " --allow-non-empty to override."
            )
    else:
        mountpoint.mkdir(parents=True)


def is_mounted(mountpoint: Path) -> bool:
    try:
        return mountpoint.is_mount()
    except OSError:
        # a stale FUSE mount can make is_mount() raise
        return True


def run_mount(
    backend: Backend,
    target: StorageTarget,
    options: MountOptions,
    dry_run: bool = False,
) -> _registry.MountRecord | None:
    """Mount a storage target with a backend and register the result."""
    command = build_command(backend, target, options)

    if dry_run:
        _echo_dry_run(backend, command, options)
        return None

    if not command.enforces_read_only:
        logger.warning(
            f"backend '{backend.name}' cannot enforce read-only access; the mount"
            " exposes the storage location as-is"
        )

    if backend.name == "symlink":
        return _mount_symlink(backend, target, options)
    if command.in_process:
        return _mount_fsspec(backend, target, options)
    return _mount_external(backend, target, options, command)


def _echo_dry_run(
    backend: Backend, command: MountCommand, options: MountOptions
) -> None:
    if command.in_process:
        logger.print(f"would mount in-process with backend '{backend.name}'")
    else:
        logger.print(" ".join(command.argv))
    if command.env:
        redacted = ", ".join(sorted(command.env))
        logger.print(f"with credentials passed via environment: {redacted}")


def _mount_symlink(
    backend: Backend, target: StorageTarget, options: MountOptions
) -> _registry.MountRecord:
    mountpoint = options.mountpoint
    if mountpoint.exists() and mountpoint.is_dir() and not any(mountpoint.iterdir()):
        mountpoint.rmdir()
    mountpoint.symlink_to(Path(target.root).resolve(), target_is_directory=True)
    record = _registry.MountRecord(
        mountpoint=str(mountpoint),
        storage_uid=target.uid,
        storage_root=target.root,
        protocol=target.protocol,
        backend=backend.name,
        in_process=True,
    )
    _registry.add(record)
    return record


def _mount_fsspec(
    backend: Backend, target: StorageTarget, options: MountOptions
) -> _registry.MountRecord:
    try:
        from fsspec.fuse import FUSEr
        from fuse import FUSE
    except ImportError as error:
        raise MountError(
            "The fsspec fallback needs 'fusepy'. Install it with: pip install fusepy"
        ) from error

    path = target.path
    fs = path.fs
    root = path.path

    record = _registry.MountRecord(
        mountpoint=str(options.mountpoint),
        storage_uid=target.uid,
        storage_root=target.root,
        protocol=target.protocol,
        backend=backend.name,
        pid=os.getpid(),
        in_process=True,
    )
    _registry.add(record)
    logger.important(f"mounted {target.root} at {options.mountpoint} (read-only)")
    logger.print(f"backend '{backend.name}' docs: {backend.docs_url}")
    logger.print("press Ctrl-C to unmount")
    try:
        # ro=True is forwarded by fusepy as the '-o ro' mount option
        FUSE(
            FUSEr(fs, root),
            str(options.mountpoint),
            nothreads=True,
            foreground=True,
            ro=True,
        )
    finally:
        _registry.remove(str(options.mountpoint))
    return record


def _mount_external(
    backend: Backend,
    target: StorageTarget,
    options: MountOptions,
    command: MountCommand,
) -> _registry.MountRecord:
    from ._credentials import has_temporary_credentials

    env = {**os.environ, **command.env}
    if command.refreshes_credentials:
        logger.important(
            "credentials are refreshed on demand through lamin, so this mount keeps"
            " working when the current token expires"
        )
    elif has_temporary_credentials(command.env):
        logger.warning(
            f"backend '{backend.name}' cannot refresh credentials, so this mount will"
            " stop working once the current token expires; remount to renew it"
        )

    if options.foreground:
        process = subprocess.Popen(command.argv, env=env)
        record = _registry.MountRecord(
            mountpoint=str(options.mountpoint),
            storage_uid=target.uid,
            storage_root=target.root,
            protocol=target.protocol,
            backend=backend.name,
            pid=process.pid,
        )
        _registry.add(record)
        logger.important(f"mounted {target.root} at {options.mountpoint} (read-only)")
        logger.print(f"backend '{backend.name}' docs: {backend.docs_url}")
        logger.print("press Ctrl-C to unmount")
        _wait_and_cleanup(process, options.mountpoint)
        return record

    result = subprocess.run(command.argv, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise MountError(
            f"backend '{backend.name}' failed to mount {target.root}:\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    record = _registry.MountRecord(
        mountpoint=str(options.mountpoint),
        storage_uid=target.uid,
        storage_root=target.root,
        protocol=target.protocol,
        backend=backend.name,
    )
    _registry.add(record)
    logger.important(f"mounted {target.root} at {options.mountpoint} (read-only)")
    logger.print(f"backend '{backend.name}' docs: {backend.docs_url}")
    return record


def _wait_and_cleanup(process: subprocess.Popen, mountpoint: Path) -> None:
    interrupted = False
    try:
        process.wait()
    except KeyboardInterrupt:
        interrupted = True
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
    finally:
        _registry.remove(str(mountpoint))
        if interrupted or is_mounted(mountpoint):
            try:
                unmount(mountpoint)
            except MountError:
                pass


def remount(record: _registry.MountRecord) -> _registry.MountRecord:
    """Unmount and mount a storage location again with the same backend.

    This is the last resort when a mount serves stale metadata; it is the only way to
    drop caches that a backend does not expose an invalidation mechanism for.
    """
    from ._backends import BACKENDS_BY_NAME
    from ._resolve import resolve_storage

    if record.external:
        raise MountError(
            f"{record.mountpoint} was mounted outside of lamin, so lamin cannot remount"
            " it. Refresh it with the tool that created it."
        )

    backend = BACKENDS_BY_NAME.get(record.backend)
    if backend is None:
        raise MountError(f"Unknown backend {record.backend!r} in the mount registry.")

    targets = resolve_storage(uid=record.storage_uid)
    mountpoint = Path(record.mountpoint)

    try:
        unmount(mountpoint)
    except MountError as error:
        raise MountError(f"could not unmount before remounting: {error}") from None

    options = MountOptions(mountpoint=mountpoint, foreground=False)
    if backend.name != "symlink":
        prepare_mountpoint(mountpoint)
    new_record = run_mount(backend, targets[0], options)
    if new_record is None:
        raise MountError(f"could not remount {record.storage_root}.")
    return new_record


def unmount(mountpoint: Path) -> None:
    """Unmount a mountpoint, handling symlinks and both FUSE flavours."""
    existing = next(
        (r for r in _registry.load() if r.mountpoint == str(mountpoint)), None
    )
    if existing is not None and existing.external:
        raise MountError(
            f"{mountpoint} was mounted outside of lamin. Unmount it with the tool that"
            " created it, or run 'lamin settings mount unregister' to forget it."
        )

    if mountpoint.is_symlink():
        _registry.remove(str(mountpoint))
        mountpoint.unlink()
        return

    record = _registry.remove(str(mountpoint))

    if record is None and not mountpoint.exists():
        raise MountError(f"{mountpoint} is not mounted.")

    if record is not None and record.in_process and record.pid is not None:
        try:
            os.kill(record.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    errors = []
    for argv in _unmount_commands(mountpoint):
        result = subprocess.run(argv, capture_output=True, text=True)
        if result.returncode == 0:
            return
        errors.append(result.stderr.strip() or result.stdout.strip())

    if not is_mounted(mountpoint):
        return
    raise MountError(
        f"could not unmount {mountpoint}: {'; '.join(e for e in errors if e)}"
    )


def _unmount_commands(mountpoint: Path) -> list[list[str]]:
    import shutil

    candidates: list[list[str]] = []

    def add(tool: str, *args: str) -> None:
        executable = shutil.which(tool)
        if executable is not None:
            candidates.append([executable, *args])

    if sys.platform == "darwin":
        add("umount", str(mountpoint))
        add("diskutil", "unmount", str(mountpoint))
    else:
        add("fusermount3", "-u", str(mountpoint))
        add("fusermount", "-u", str(mountpoint))
        add("umount", str(mountpoint))
    return candidates
