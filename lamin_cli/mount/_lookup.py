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
    from collections.abc import Iterator

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

    target = resolve_artifact(uid=uid, key=key)[0]
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
