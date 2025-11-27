from lamindb_setup import connect
from lamindb_setup import delete as delete_instance

from .urls import decompose_url


def delete(
    entity: str,
    name: str | None = None,
    uid: str | None = None,
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
        assert uid is not None, "You have to pass a uid for deleting an artifact."
        from lamindb import Artifact

        Artifact.get(uid).delete(permanent=permanent)
    elif entity == "transform":
        assert uid is not None, "You have to pass a uid for deleting an transform."
        from lamindb import Transform

        Transform.get(uid).delete(permanent=permanent)
    elif entity == "collection":
        assert uid is not None, "You have to pass a uid for deleting an collection."
        from lamindb import Collection

        Collection.get(uid).delete(permanent=permanent)
    elif entity == "instance":
        return delete_instance(slug, force=force)
    else:  # backwards compatibility
        return delete_instance(entity, force=force)
