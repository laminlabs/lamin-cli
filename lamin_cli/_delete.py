import click
from lamindb_setup import connect
from lamindb_setup import delete as delete_instance
from lamindb_setup.errors import StorageNotEmpty

from .urls import decompose_url

ENTITIES_KEY: set[str] = {"artifact", "transform", "collection"}
ENTITIES_NAME: set[str] = {
    "record",
    "project",
    "ulabel",
    "branch",
    "run",
    "feature",
    "schema",
    "space",
}


def get_registry(entity: str):
    """Registry class for a CLI entity name, for example `artifact` to `ln.Artifact`."""
    import lamindb as ln

    return {
        "artifact": ln.Artifact,
        "transform": ln.Transform,
        "collection": ln.Collection,
        "record": ln.Record,
        "project": ln.Project,
        "ulabel": ln.ULabel,
        "branch": ln.Branch,
        "run": ln.Run,
        "feature": ln.Feature,
        "schema": ln.Schema,
        "space": ln.Space,
        "reference": ln.Reference,
    }[entity]


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

    if entity in ENTITIES_KEY | ENTITIES_NAME:
        model = get_registry(entity)
        if entity in ENTITIES_KEY:
            if uid is None and key is None:
                raise click.ClickException(
                    f"For entity '{entity}' you must pass --uid or --key"
                )
            if key is not None:
                record = model.objects.filter(key=key).order_by("-created_at").first()
                if record is None:
                    raise click.ClickException(
                        f"{model.__name__} with key={key} does not exist."
                    )
            else:
                record = model.get(uid)
        elif entity in ENTITIES_NAME:
            if uid is None and name is None:
                raise click.ClickException(
                    f"For entity '{entity}' you must pass --uid or --name"
                )
            record = model.get(uid) if uid is not None else model.get(name=name)
        record.delete(permanent=permanent)
    else:
        try:
            return delete_instance(entity, force=force)
        except StorageNotEmpty as e:
            raise click.ClickException(str(e)) from e
