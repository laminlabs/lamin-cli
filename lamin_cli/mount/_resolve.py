"""Resolve a CLI target (storage, artifact, space) to storage locations to mount."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from upath import UPath


@dataclass
class StorageTarget:
    """A storage location resolved for mounting."""

    uid: str
    root: str
    protocol: str
    path: UPath
    # set when the target was resolved via an artifact
    artifact_uid: str | None = None
    artifact_key: str | None = None
    # path of the artifact relative to the storage root
    artifact_storage_key: str | None = None
    # a managed storage location gets its credentials from LaminHub
    managed: bool = False

    @property
    def slug(self) -> str:
        """Directory name to use when several storages are mounted together."""
        tail = self.root.rstrip("/").rsplit("/", 1)[-1] or self.uid
        safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in tail)
        return f"{safe}-{self.uid}" if safe else self.uid


def _connect() -> None:
    import lamindb_setup as ln_setup

    if not ln_setup.settings.is_configured:
        raise SystemExit(
            "Not connected to an instance. Please run: lamin connect account/name"
        )


def _to_target(storage) -> StorageTarget:
    path = storage.path
    return StorageTarget(
        uid=storage.uid,
        root=storage.root,
        protocol=storage.type,
        path=path,
        managed=storage.instance_uid is not None,
    )


def resolve_storage(
    uid: str | None = None, root: str | None = None
) -> list[StorageTarget]:
    """Resolve a single storage location, defaulting to the instance's storage."""
    _connect()
    import lamindb as ln
    import lamindb_setup as ln_setup

    if uid is not None and root is not None:
        raise SystemExit("Pass only one of --uid or --root.")
    if uid is not None:
        storage = ln.Storage.filter(uid=uid).one_or_none()
        if storage is None:
            raise SystemExit(f"No storage location found with uid {uid!r}.")
    elif root is not None:
        storage = ln.Storage.filter(root=root.rstrip("/")).one_or_none()
        if storage is None:
            raise SystemExit(f"No storage location found with root {root!r}.")
    else:
        default_root = ln_setup.settings.storage.root_as_str
        storage = ln.Storage.filter(root=default_root).one_or_none()
        if storage is None:
            raise SystemExit(
                f"Could not find the default storage location {default_root!r} in the"
                " registry. Pass --uid or --root explicitly."
            )
    return [_to_target(storage)]


def resolve_artifact(
    uid: str | None = None, key: str | None = None
) -> list[StorageTarget]:
    """Resolve the storage location underlying an artifact."""
    _connect()
    import lamindb as ln

    if uid is None and key is None:
        raise SystemExit("Pass one of --uid or --key.")
    if uid is not None and key is not None:
        raise SystemExit("Pass only one of --uid or --key.")
    if uid is not None:
        artifact = (
            ln.Artifact.filter(uid__startswith=uid).order_by("-created_at").first()
        )
        if artifact is None:
            raise SystemExit(f"No artifact found with uid {uid!r}.")
    else:
        artifact = (
            ln.Artifact.filter(key=key, is_latest=True).order_by("-created_at").first()
        )
        if artifact is None:
            raise SystemExit(f"No artifact found with key {key!r}.")

    from lamindb.core.storage.paths import auto_storage_key_from_artifact

    target = _to_target(artifact.storage)
    target.artifact_uid = artifact.uid
    target.artifact_key = artifact.key
    target.artifact_storage_key = auto_storage_key_from_artifact(artifact)
    return [target]


def resolve_space(
    name: str | None = None, uid: str | None = None
) -> list[StorageTarget]:
    """Resolve all storage locations managed by a space."""
    _connect()
    import lamindb as ln

    if name is None and uid is None:
        raise SystemExit("Pass one of --name or --uid.")
    if name is not None and uid is not None:
        raise SystemExit("Pass only one of --name or --uid.")
    if name is not None:
        space = ln.Space.filter(name=name).one_or_none()
        if space is None:
            raise SystemExit(f"No space found with name {name!r}.")
    else:
        space = ln.Space.filter(uid=uid).one_or_none()
        if space is None:
            raise SystemExit(f"No space found with uid {uid!r}.")

    storages = ln.Storage.filter(space=space).all()
    targets = [_to_target(storage) for storage in storages]
    if not targets:
        raise SystemExit(f"Space {space.name!r} does not manage any storage location.")
    return targets


def split_root(root: str, protocol: str) -> tuple[str, str, str | None]:
    """Split a storage root into (container, prefix, endpoint_url).

    For object stores the container is the bucket. For local storage the container is
    the absolute path and the prefix is empty.
    """
    if protocol == "local":
        return root, "", None
    parts = urlsplit(root)
    endpoint_url = None
    query = parts.query
    if query:
        from urllib.parse import parse_qs

        parsed = parse_qs(query)
        if "endpoint_url" in parsed:
            endpoint_url = parsed["endpoint_url"][0]
    container = parts.netloc
    prefix = parts.path.strip("/")
    if not container:
        # e.g. hf://datasets/org/name has no netloc in some spellings
        stripped = urlunsplit((parts.scheme, "", parts.path, "", "")).split("://", 1)[
            -1
        ]
        container, _, prefix = stripped.partition("/")
    return container, prefix, endpoint_url
