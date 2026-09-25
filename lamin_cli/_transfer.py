from __future__ import annotations

import click

from .urls import decompose_url

ENTITIES_KEY = {"artifact", "transform", "collection"}
ENTITIES_NAME = {
    "record",
    "project",
    "ulabel",
    "branch",
    "run",
    "feature",
    "schema",
    "space",
    "reference",
}
ENTITIES = ENTITIES_KEY | ENTITIES_NAME


def transfer(
    entity: str | None = None,
    uid: str | None = None,
    key: str | None = None,
    name: str | None = None,
    source: str | None = None,
    depth: int | None = None,
    transfer: str | None = None,
):
    """Transfer one object from a source database into the current default database."""
    if entity is not None and entity.startswith("https://") and "lamin" in entity:
        source, entity, uid = decompose_url(entity)
    if entity is None:
        raise click.ClickException(
            "Pass a LaminDB URL or an entity such as `record` or `artifact`."
        )
    if entity not in ENTITIES:
        raise click.ClickException(
            "Entity must be a LaminDB URL or one of: " + ", ".join(sorted(ENTITIES))
        )
    if source is None:
        raise click.ClickException(
            "Pass a LaminDB URL, or pass --from with the source instance slug."
        )
    if uid is None and key is None and name is None:
        raise click.ClickException(
            "Pass a LaminDB URL, or pass --uid, --key, or --name."
        )
    if uid is None:
        uid = _lookup_uid(entity, source, key=key, name=name)

    from lamindb.models import sync_objects_from_database

    return sync_objects_from_database(
        entity,
        uid,
        source=source,
        depth=depth,
        transfer=transfer,
    )


def _lookup_uid(entity: str, source: str, *, key: str | None, name: str | None) -> str:
    from lamindb.models._transfer import _registry_class_name
    from lamindb.models.db import DB

    queryset = getattr(DB(source), _registry_class_name(entity))
    if entity in ENTITIES_KEY:
        if key is None:
            raise click.ClickException(f"For {entity} pass --uid or --key.")
        record = queryset.get(key=key)
    else:
        if name is None:
            raise click.ClickException(f"For {entity} pass --uid or --name.")
        record = queryset.get(name=name)
    return record.uid
