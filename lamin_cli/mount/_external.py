"""Identify storage locations that were mounted outside of lamin.

Every LaminDB storage root carries a marker file, ``.lamindb/storage_uid.txt``, whose
first line is the uid of the storage location. Reading it through a mountpoint is a
protocol- and backend-agnostic way to tell which storage location a mount exposes,
without having to parse the mount source of every FUSE implementation.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

STORAGE_UID_FILE_KEY = ".lamindb/storage_uid.txt"
LEGACY_STORAGE_UID_FILE_KEY = ".lamindb/_is_initialized"

# fstypes that indicate a userspace mount worth probing for a marker
FUSE_FSTYPES = frozenset(
    {
        "fuse",
        "fuse.rclone",
        "fuseblk",
        "macfuse",
        "osxfuse",
        "nfs",
        "nfs4",
        "cifs",
        "smbfs",
    }
)


@dataclass
class SystemMount:
    """An entry of the operating system's mount table."""

    mountpoint: str
    source: str
    fstype: str


def read_storage_marker(mountpoint: str | Path) -> str | None:
    """Read the storage uid that a mounted storage root advertises."""
    root = Path(mountpoint)
    for key in (STORAGE_UID_FILE_KEY, LEGACY_STORAGE_UID_FILE_KEY):
        marker = root / key
        try:
            if not marker.is_file():
                continue
            lines = marker.read_text().splitlines()
        except OSError:
            # unreadable, disconnected or permission-denied mounts are simply skipped
            continue
        if lines and lines[0].strip():
            return lines[0].strip()
    return None


def _parse_proc_mounts(text: str) -> list[SystemMount]:
    mounts = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        source, mountpoint, fstype = fields[0], fields[1], fields[2]
        # /proc/mounts octal-escapes spaces and friends
        mountpoint = mountpoint.encode().decode("unicode_escape")
        mounts.append(SystemMount(mountpoint=mountpoint, source=source, fstype=fstype))
    return mounts


def _parse_bsd_mount(text: str) -> list[SystemMount]:
    mounts = []
    for line in text.splitlines():
        # "<source> on <mountpoint> (<fstype>, <options>)"
        if " on " not in line or "(" not in line:
            continue
        source, _, rest = line.partition(" on ")
        mountpoint, _, options = rest.rpartition(" (")
        fstype = options.strip(")").split(",")[0].strip()
        mounts.append(
            SystemMount(
                mountpoint=mountpoint.strip(), source=source.strip(), fstype=fstype
            )
        )
    return mounts


def system_mounts() -> list[SystemMount]:
    """Read the operating system's mount table."""
    if sys.platform.startswith("linux"):
        try:
            return _parse_proc_mounts(Path("/proc/mounts").read_text())
        except OSError:
            return []
    try:
        result = subprocess.run(
            ["/sbin/mount"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return _parse_bsd_mount(result.stdout)


def candidate_mounts() -> list[SystemMount]:
    """Mounts that could plausibly expose a remote storage location."""
    return [
        mount
        for mount in system_mounts()
        if any(mount.fstype.startswith(fstype) for fstype in FUSE_FSTYPES)
    ]


@dataclass
class Discovered:
    """A system mount that exposes a LaminDB storage location."""

    mountpoint: str
    storage_uid: str
    fstype: str
    source: str


def discover(paths: list[str] | None = None) -> list[Discovered]:
    """Find mounts that expose a LaminDB storage root.

    Without ``paths`` the operating system's mount table is scanned, restricted to
    userspace and network filesystems.
    """
    if paths:
        candidates = [
            SystemMount(mountpoint=path, source="", fstype="given") for path in paths
        ]
    else:
        candidates = candidate_mounts()

    found = []
    for candidate in candidates:
        uid = read_storage_marker(candidate.mountpoint)
        if uid is not None:
            found.append(
                Discovered(
                    mountpoint=str(Path(candidate.mountpoint).absolute()),
                    storage_uid=uid,
                    fstype=candidate.fstype,
                    source=candidate.source,
                )
            )
    return found
