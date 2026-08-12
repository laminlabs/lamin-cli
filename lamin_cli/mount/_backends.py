"""Registry of mounting backends and detection of which ones are installed."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# the in-process fallback that works for every fsspec protocol
FSSPEC_BACKEND = "fsspec"


@dataclass(frozen=True)
class Backend:
    """A mounting solution for one or more storage protocols."""

    name: str
    protocols: tuple[str, ...]
    docs_url: str
    # lower sorts first, i.e. is preferred
    priority: int
    # None means the backend runs in-process rather than as an external tool
    executable: str | None = None
    version_args: tuple[str, ...] = ("--version",)
    # some tools stay in the foreground unless told otherwise, others fork
    foreground_args: tuple[str, ...] = ()
    # whether the tool can refresh expiring credentials via an AWS credential_process
    supports_credential_process: bool = False
    notes: str = ""

    @property
    def is_external(self) -> bool:
        return self.executable is not None

    def which(self) -> str | None:
        if self.executable is None:
            return None
        return shutil.which(self.executable)

    def version(self) -> str | None:
        """Best-effort version string, or None if it cannot be determined."""
        if self.executable is None:
            if self.name != FSSPEC_BACKEND:
                return "built-in"
            try:
                import fsspec

                return f"fsspec {fsspec.__version__}"
            except ImportError:
                return None
        path = self.which()
        if path is None:
            return None
        try:
            proc = subprocess.run(
                [path, *self.version_args],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        for line in output.splitlines():
            line = line.strip()
            # skip linker/library warnings that some tools print first
            if line and not line.lower().startswith("warning"):
                return line
        return None

    def is_available(self) -> bool:
        if self.executable is None:
            if self.name != FSSPEC_BACKEND:
                return True
            return _fsspec_fuse_available()
        return self.which() is not None


def _fsspec_fuse_available() -> bool:
    from importlib.util import find_spec

    try:
        return find_spec("fsspec") is not None and find_spec("fuse") is not None
    except (ImportError, ValueError):
        return False


BACKENDS: tuple[Backend, ...] = (
    Backend(
        name="mount-s3",
        executable="mount-s3",
        protocols=("s3",),
        docs_url="https://github.com/awslabs/mountpoint-s3/blob/main/doc/CONFIGURATION.md",
        priority=10,
        foreground_args=("--foreground",),
        supports_credential_process=True,
        notes="Mountpoint for Amazon S3",
    ),
    Backend(
        name="gcsfuse",
        executable="gcsfuse",
        protocols=("gs",),
        docs_url="https://cloud.google.com/storage/docs/gcsfuse-cli",
        priority=10,
        foreground_args=("--foreground",),
    ),
    Backend(
        name="rclone",
        executable="rclone",
        protocols=("s3", "gs", "http", "https"),
        docs_url="https://rclone.org/commands/rclone_mount/",
        priority=20,
    ),
    Backend(
        name="goofys",
        executable="goofys",
        protocols=("s3",),
        docs_url="https://github.com/kahing/goofys#readme",
        priority=30,
        version_args=("-v",),
        foreground_args=("-f",),
    ),
    Backend(
        name="s3fs",
        executable="s3fs",
        protocols=("s3",),
        docs_url="https://github.com/s3fs-fuse/s3fs-fuse/wiki/Fuse-Over-Amazon",
        priority=40,
        foreground_args=("-f",),
    ),
    Backend(
        name="bindfs",
        executable="bindfs",
        protocols=("local",),
        docs_url="https://bindfs.org/docs/bindfs.1.html",
        priority=10,
        foreground_args=("-f",),
    ),
    Backend(
        name="symlink",
        executable=None,
        protocols=("local",),
        docs_url="https://docs.lamin.ai/cli",
        priority=90,
        notes="symlink only, read-only is NOT enforced",
    ),
    Backend(
        name=FSSPEC_BACKEND,
        executable=None,
        protocols=("s3", "gs", "hf", "http", "https"),
        docs_url="https://filesystem-spec.readthedocs.io/en/latest/features.html#mount-anything-with-fuse",
        priority=80,
        notes="in-process fallback, reuses LaminDB credentials",
    ),
)

BACKENDS_BY_NAME: dict[str, Backend] = {b.name: b for b in BACKENDS}


def _symlink_backend() -> Backend:
    return BACKENDS_BY_NAME["symlink"]


def backends_for_protocol(protocol: str) -> list[Backend]:
    """All backends that can serve a protocol, most preferred first."""
    matching = [b for b in BACKENDS if protocol in b.protocols]
    return sorted(matching, key=lambda b: (b.priority, b.name))


def available_backends(protocol: str) -> list[Backend]:
    """Installed backends that can serve a protocol, most preferred first."""
    candidates = backends_for_protocol(protocol)
    # the symlink backend is always "installed" but never enforces read-only,
    # so it is only offered when nothing better exists
    available = [b for b in candidates if b.name != "symlink" and b.is_available()]
    if protocol == "local" and not available:
        available.append(_symlink_backend())
    return available


def fuse_provider_available() -> bool:
    """Whether a FUSE provider (macFUSE / libfuse) seems to be present."""
    from pathlib import Path

    if sys.platform == "darwin":
        return Path("/Library/Filesystems/macfuse.fs").exists()
    if sys.platform.startswith("linux"):
        return (
            Path("/dev/fuse").exists()
            or shutil.which("fusermount") is not None
            or shutil.which("fusermount3") is not None
        )
    return False


def fuse_provider_hint() -> str:
    if sys.platform == "darwin":
        return "install macFUSE: https://macfuse.github.io"
    if sys.platform.startswith("linux"):
        return "install libfuse, e.g. 'apt install fuse3' or 'yum install fuse3'"
    return f"FUSE is not supported on platform {sys.platform!r}"


@dataclass
class BackendReport:
    """Detection result for one backend."""

    backend: Backend
    available: bool
    location: str | None
    version: str | None


def detect(protocol: str | None = None) -> list[BackendReport]:
    """Probe all backends, optionally restricted to one protocol."""
    backends = BACKENDS if protocol is None else tuple(backends_for_protocol(protocol))
    reports = []
    for backend in sorted(backends, key=lambda b: (b.protocols, b.priority, b.name)):
        available = backend.is_available()
        reports.append(
            BackendReport(
                backend=backend,
                available=available,
                location=backend.which(),
                version=backend.version() if available else None,
            )
        )
    return reports
