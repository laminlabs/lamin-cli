import click
from lamindb_setup import connect
from lamindb_setup import delete as delete_instance
from lamindb_setup.errors import StorageNotEmpty

from ._annotate import (
    ANNOTATE_ENTITIES_KEY,
    ANNOTATE_ENTITIES_NAME,
)
from .urls import decompose_url

SUPPORTED_ENTITIES = ANNOTATE_ENTITIES_KEY | ANNOTATE_ENTITIES_NAME


def delete(
    entity: str,
    name: str | None = None,
    uid: str | None = None,
    key: str | None = None,
    permanent: bool | None = None,
    force: bool = False,
):
    # TODO: refactor to abstract getting and deleting across entities
    if entity.startswith("https://") and "lamin" in entity:
        url = entity
        instance, entity, uid = decompose_url(url)
        connect(instance)

    if entity in SUPPORTED_ENTITIES:
        import lamindb as ln

        if entity in ANNOTATE_ENTITIES_KEY:
            if uid is None and key is None:
                raise SystemExit(f"For entity '{entity}' you must pass --uid or --key")
            model = {
                "artifact": ln.Artifact,
                "transform": ln.Transform,
                "collection": ln.Collection,
            }[entity]
            if key is not None:
                record = model.objects.filter(key=key).order_by("-created_at").first()
                if record is None:
                    raise SystemExit(f"{model.__name__} with key={key} does not exist.")
            else:
                record = model.get(uid)
        elif entity in ANNOTATE_ENTITIES_NAME:
            if uid is None and name is None:
                raise SystemExit(f"For entity '{entity}' you must pass --uid or --name")
            model = {
                "record": ln.Record,
                "project": ln.Project,
                "ulabel": ln.ULabel,
                "branch": ln.Branch,
                "run": ln.Run,
                "feature": ln.Feature,
                "schema": ln.Schema,
                "space": ln.Space,
            }[entity]
            record = model.get(uid) if uid is not None else model.get(name=name)
        record.delete(permanent=permanent)
    else:
        try:
            return delete_instance(entity, force=force)
        except StorageNotEmpty as e:
            raise click.ClickException(str(e)) from e
