"""Locate an artifact inside a mounted storage location.

The mountpoint always corresponds to the *storage root*: every backend is invoked so
that a bucket prefix is mapped onto the mountpoint (``--prefix``, ``--only-dir``, ...).
The local path of an artifact is therefore ``mountpoint / <storage key>``, where the
storage key is the physical key, which differs from ``artifact.key`` when keys are
virtual (the default).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from . import _registry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ._registry import MountRecord


class Visibility(Enum):
    """Why an artifact is or is not visible through a mount."""

    FOUND = "found"
    FOUND_AFTER_REFRESH = "found-after-refresh"
    MISSING_IN_ORIGIN = "missing-in-origin"
    STALE = "stale"


@dataclass
class ArtifactLocation:
    """Where an artifact lives, in its storage location and on this machine."""

    artifact_uid: str
    key: str | None
    storage_key: str
    origin: str
    storage_uid: str
    storage_root: str
    protocol: str
    mount: MountRecord | None = None
    local_path: Path | None = None

    @property
    def key_is_virtual(self) -> bool:
        return self.key is not None and self.key != self.storage_key


def resolve_artifact_location(
    uid: str | None = None, key: str | None = None
) -> ArtifactLocation:
    """Resolve an artifact to its storage location and physical storage key."""
    from ._resolve import resolve_artifact

    return _location_from_target(resolve_artifact(uid=uid, key=key)[0])


def location_from_artifact(artifact) -> ArtifactLocation:
    """Resolve an already fetched artifact, which may live in another instance."""
    from ._resolve import target_from_artifact

    return _location_from_target(target_from_artifact(artifact))


def _location_from_target(target) -> ArtifactLocation:
    assert target.artifact_storage_key is not None
    assert target.artifact_uid is not None
    return ArtifactLocation(
        artifact_uid=target.artifact_uid,
        key=target.artifact_key,
        storage_key=target.artifact_storage_key,
        origin=str(target.path / target.artifact_storage_key),
        storage_uid=target.uid,
        storage_root=target.root,
        protocol=target.protocol,
    )


def find_mount(
    storage_uid: str | None = None, storage_root: str | None = None
) -> MountRecord | None:
    """Find a live mount for a storage location, by uid and then by root."""
    records = _registry.prune()
    for record in records:
        if storage_uid is not None and record.storage_uid == storage_uid:
            return record
    for record in records:
        if storage_root is not None and record.storage_root == storage_root:
            return record
    return None


def local_path_for(mountpoint: str | Path, storage_key: str) -> Path:
    return Path(mountpoint) / storage_key


@contextmanager
def stdout_to_stderr() -> Iterator[None]:
    """Route library chatter to stderr so that stdout stays a bare path.

    ``lamindb`` logs to stdout through a handler that captured ``sys.stdout`` at
    import time, so swapping ``sys.stdout`` alone is not enough.
    """
    import logging

    from lamin_utils import logger

    swapped: list[tuple[logging.StreamHandler, object]] = []
    for source in (logger, logging.getLogger()):
        for handler in getattr(source, "handlers", []):
            if (
                isinstance(handler, logging.StreamHandler)
                and handler.stream is sys.stdout
            ):
                swapped.append((handler, handler.stream))
                handler.stream = sys.stderr
    original = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = original
        for handler, stream in swapped:
            handler.stream = stream


def invalidate(path: Path) -> None:
    """Ask the FUSE layer to revalidate a path against the origin.

    Listing the parent directory is the documented way to refresh stale metadata for
    both Mountpoint for Amazon S3 and gcsfuse, and it also drops cached negative
    lookups for the entries it returns.
    """
    parent = path.parent
    for directory in (parent, parent.parent):
        try:
            if directory.is_dir():
                for _ in directory.iterdir():
                    pass
        except OSError:
            # a stale or disconnected mount raises here; the caller reports it
            continue


def origin_exists(origin: str) -> bool:
    """Check the origin directly, bypassing the mount and any fsspec listing cache."""
    from lamindb_setup.core.upath import create_path

    path = create_path(origin)
    fs = getattr(path, "fs", None)
    if fs is not None:
        try:
            fs.invalidate_cache()
        except (AttributeError, TypeError, OSError):
            pass
    try:
        return path.exists()
    except OSError:
        return False


def check_visibility(local_path: Path, origin: str) -> Visibility:
    """Determine whether an artifact is visible through the mount, refreshing once."""
    if local_path.exists():
        return Visibility.FOUND
    # the origin is authoritative: it is read through fsspec, not through FUSE
    if not origin_exists(origin):
        return Visibility.MISSING_IN_ORIGIN
    invalidate(local_path)
    if local_path.exists():
        return Visibility.FOUND_AFTER_REFRESH
    return Visibility.STALE


class LocalPathError(Exception):
    """Raised when an artifact cannot be read through a local path."""


class NotMounted(LocalPathError):
    """Raised when the artifact's storage location is not mounted on this machine."""


def resolve_local_path(
    location: ArtifactLocation,
    *,
    mountpoint: str | Path | None = None,
    check: bool = True,
    remount: bool = False,
    note: Callable[[str], None] | None = None,
) -> Path:
    """Resolve the local path of an artifact inside a mounted storage location.

    This is the single implementation behind `lamin settings mount path` and the
    argument translation of `lamin run`. A local storage location is readable without
    any mount. Otherwise the registry is consulted, and when ``check`` is set a "not
    found" is verified against the origin and the mount refreshed before giving up.
    """
    notify = note or (lambda message: None)

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
            mount_root = Path(location.storage_root)
            local_path = Path(location.origin)
        else:
            raise NotMounted(
                f"Storage location {location.storage_root} is not mounted. Mount it"
                f" with: lamin settings mount storage --uid {location.storage_uid}"
                " <mountpoint>"
            )

    location.local_path = local_path
    if location.key_is_virtual:
        notify(
            f"note: key '{location.key}' is virtual, the artifact is stored at"
            f" '{location.storage_key}'"
        )
    if not check:
        return local_path

    visibility = check_visibility(local_path, location.origin)
    if visibility is Visibility.FOUND_AFTER_REFRESH:
        notify("note: the mount served stale metadata, it was refreshed")
    elif visibility is Visibility.MISSING_IN_ORIGIN:
        raise LocalPathError(
            f"Artifact {location.artifact_uid} is recorded at {location.origin} but"
            " that path does not exist in the storage location."
        )
    elif visibility is Visibility.STALE:
        local_path = _recover_stale(location, local_path, mount_root, remount, notify)
    return local_path


def _recover_stale(
    location: ArtifactLocation,
    local_path: Path,
    mount_root: Path,
    remount: bool,
    notify: Callable[[str], None],
) -> Path:
    from ._mount import MountError
    from ._mount import remount as remount_storage

    if not remount:
        remedy = (
            "Refresh it with the tool that created it."
            if location.mount is not None and location.mount.external
            else f"Retry with --remount, or: lamin settings mount refresh {mount_root}"
        )
        raise LocalPathError(
            f"{local_path} is not visible through the mount although"
            f" {location.origin} exists. The mount is stale. {remedy}"
        )
    if location.mount is None:
        raise LocalPathError("Cannot remount a mountpoint that is not in the registry.")
    if location.mount.pid is not None:
        notify(
            "warning: this mount runs in the foreground in another process, which"
            " remounting will terminate"
        )
    notify(f"remounting {location.storage_root} ...")
    try:
        with stdout_to_stderr():
            record = remount_storage(location.mount)
    except MountError as error:
        raise LocalPathError(str(error)) from None
    local_path = local_path_for(record.mountpoint, location.storage_key)
    if not local_path.exists():
        raise LocalPathError(f"{local_path} is still not readable after remounting.")
    return local_path
