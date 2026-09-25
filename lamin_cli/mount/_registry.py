"""Track active mounts so that they can be listed and unmounted later."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class MountRecord:
    mountpoint: str
    storage_uid: str
    storage_root: str
    protocol: str
    backend: str
    pid: int | None = None
    in_process: bool = False
    # mounts established outside of lamin are never torn down or remounted by lamin
    external: bool = False

    @property
    def is_alive(self) -> bool:
        if self.external:
            # lamin does not own the process, so the mountpoint itself is the evidence
            return Path(self.mountpoint).exists()
        if self.pid is None:
            return Path(self.mountpoint).exists()
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def _registry_path() -> Path:
    from lamindb_setup.core._settings_store import settings_dir

    return Path(settings_dir) / "mounts.json"


def _read_raw() -> list[dict]:
    path = _registry_path()
    if not path.exists():
        return []
    try:
        content = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return content if isinstance(content, list) else []


def load() -> list[MountRecord]:
    records = []
    for entry in _read_raw():
        try:
            records.append(MountRecord(**entry))
        except TypeError:
            continue
    return records


def _write(records: list[MountRecord]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(r) for r in records], indent=2))


def add(record: MountRecord) -> None:
    records = [r for r in load() if r.mountpoint != record.mountpoint]
    records.append(record)
    _write(records)


def remove(mountpoint: str) -> MountRecord | None:
    records = load()
    kept = [r for r in records if r.mountpoint != mountpoint]
    removed = next((r for r in records if r.mountpoint == mountpoint), None)
    if len(kept) != len(records):
        _write(kept)
    return removed


def prune() -> list[MountRecord]:
    """Drop records whose process is gone. Returns the surviving records."""
    records = load()
    alive = [r for r in records if r.is_alive]
    if len(alive) != len(records):
        _write(alive)
    return alive
