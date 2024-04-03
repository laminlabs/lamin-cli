from pathlib import Path
from typing import Optional, Union
import lamindb_setup as ln_setup
from lamin_utils import logger


def save_from_filepath_cli(
    filepath: Union[str, Path], key: Optional[str], description: Optional[str]
) -> Optional[str]:
    if not isinstance(filepath, Path):
        filepath = Path(filepath)

    # this will be gone once we get rid of lamin load or enable loading multiple
    # instances sequentially
    auto_connect_state = ln_setup.settings.auto_connect
    ln_setup.settings.auto_connect = True

    import lamindb as ln
    from lamindb.core._run_context import get_stem_uid_and_version_from_file
    from lamindb._finish import save_run_context_core

    ln_setup.settings.auto_connect = auto_connect_state

    if filepath.suffix not in {".py", ".ipynb"}:
        if key is None and description is None:
            logger.error("Please pass a key or description via --key or --description")
            return "missing-key-or-description"
        artifact = ln.Artifact(filepath, key=key, description=description)
        artifact.save()
        slug = ln_setup.settings.instance.slug
        logger.important(f"saved: {artifact}")
        logger.important(f"storage path: {artifact.path}")
        if ln_setup.settings.instance.is_remote:
            logger.important(f"go to: https://lamin.ai/{slug}/artifact/{artifact.uid}")
        if ln_setup.settings.storage.type == "s3":
            logger.important(f"storage url: {artifact.path.to_url()}")
        return None
    else:
        # consider notebooks & scripts a transform
        stem_uid, transform_version = get_stem_uid_and_version_from_file(filepath)
        # the corresponding transform family in the transform table
        transform_family = ln.Transform.filter(uid__startswith=stem_uid).all()
        if len(transform_family) == 0:
            logger.error(
                f"Did not find stem uid '{stem_uid}'"
                " in Transform registry. Did you run ln.track()?"
            )
            return "not-tracked-in-transform-registry"
        # the specific version
        transform = transform_family.filter(version=transform_version).one()
        # latest run of this transform by user
        run = ln.Run.filter(transform=transform).order_by("-started_at").first()
        if run.created_by.id != ln_setup.settings.user.id:
            response = input(
                "You are trying to save a transform created by another user: Source and"
                " report files will be tagged with *your* user id. Proceed? (y/n)"
            )
            if response != "y":
                return "aborted-save-notebook-created-by-different-user"
        return save_run_context_core(
            run=run,
            transform=transform,
            filepath=filepath,
            transform_family=transform_family,
        )
