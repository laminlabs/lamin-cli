import click
from lamindb_setup import connect
from lamindb_setup import delete as delete_instance
from lamindb_setup.errors import StorageNotEmpty

from .urls import decompose_url


def delete(
    entity: str,
    name: str | None = None,
    uid: str | None = None,
    key: str | None = None,
    slug: str | None = None,
    permanent: bool | None = None,
    force: bool = False,
):
    # TODO: refactor to abstract getting and deleting across entities
    if entity.startswith("https://") and "lamin" in entity:
        url = entity
        instance, entity, uid = decompose_url(url)
        connect(instance)

    if entity == "branch":
        assert name is not None, "You have to pass a name for deleting a branch."
        from lamindb import Branch

        Branch.get(name=name).delete(permanent=permanent)
    elif entity == "artifact":
        assert uid is not None or key is not None, (
            "You have to pass a uid or key for deleting an artifact."
        )
        from lamindb import Artifact

        if key is not None:
            record = Artifact.objects.filter(key=key).order_by("-created_at").first()
            if record is None:
                raise SystemExit(f"Artifact with key={key} does not exist.")
        else:
            record = Artifact.get(uid)
        record.delete(permanent=permanent)
    elif entity == "transform":
        assert uid is not None or key is not None, (
            "You have to pass a uid or key for deleting a transform."
        )
        from lamindb import Transform

        if key is not None:
            record = Transform.objects.filter(key=key).order_by("-created_at").first()
            if record is None:
                raise SystemExit(f"Transform with key={key} does not exist.")
        else:
            record = Transform.get(uid)
        record.delete(permanent=permanent)
    elif entity == "collection":
        assert uid is not None or key is not None, (
            "You have to pass a uid or key for deleting a collection."
        )
        from lamindb import Collection

        if key is not None:
            record = Collection.objects.filter(key=key).order_by("-created_at").first()
            if record is None:
                raise SystemExit(f"Collection with key={key} does not exist.")
        else:
            record = Collection.get(uid)
        record.delete(permanent=permanent)
    elif entity == "instance":
        try:
            return delete_instance(slug, force=force)
        except StorageNotEmpty as e:
            raise click.ClickException(str(e)) from e
    else:  # backwards compatibility
        try:
            return delete_instance(entity, force=force)
        except StorageNotEmpty as e:
            raise click.ClickException(str(e)) from e
