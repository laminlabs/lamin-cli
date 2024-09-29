from __future__ import annotations
from typing import Tuple
from lamin_utils import logger
import lamindb_setup as ln_setup
from pathlib import Path


def decompose_url(url: str) -> Tuple[str, str, str]:
    assert "transform" in url or "artifact" in url
    for entity in ["transform", "artifact"]:
        if entity in url:
            break
    uid = url.split(f"{entity}/")[1]
    instance_slug = "/".join(url.split("/")[3:5])
    return instance_slug, entity, uid


def load(entity: str, uid: str = None, key: str = None, with_env: bool = False):
    if entity.startswith("https://") and "lamin" in entity:
        url = entity
        instance_slug, entity, uid = decompose_url(url)
    elif entity not in {"artifact", "transform"}:
        raise SystemExit("Entity has to be a laminhub URL or 'artifact' or 'transform'")
    else:
        instance_slug = None

    if instance_slug is not None:
        auto_connect = ln_setup.settings.auto_connect
        # we don't want to auto-connect when importing lamindb
        ln_setup.settings.auto_connect = False

        import lamindb as ln
        from lamindb._finish import script_to_notebook

        ln_setup.settings.auto_connect = auto_connect
        ln.connect(instance_slug)
    else:
        import lamindb as ln
        from lamindb._finish import script_to_notebook

    # below is to silence warnings about missing run inputs
    ln.settings.track_run_inputs = False

    if entity == "transform":
        transform = (
            ln.Transform.get(uid)
            if uid is not None
            # if below, we take is_latest=True as the criterion, we might get draft notebooks
            # hence, we use source_code__isnull=False and order by created_at instead
            else ln.Transform.filter(key=key, source_code__isnull=False)
            .order_by("-created_at")
            .first()
        )
        target_filename = transform.key
        if Path(target_filename).exists():
            response = input(f"! {target_filename} exists: replace? (y/n)")
            if response != "y":
                raise SystemExit("Aborted.")
        if transform._source_code_artifact_id is not None:
            # backward compat
            filepath_cache = transform._source_code_artifact.cache()
            if not target_filename.endswith(transform._source_code_artifact.suffix):
                target_filename += transform._source_code_artifact.suffix
            filepath_cache.rename(target_filename)
        elif transform.source_code is not None:
            if transform.key.endswith(".ipynb"):
                script_to_notebook(transform, target_filename, bump_revision=True)
            else:
                Path(target_filename).write_text(transform.source_code)
        else:
            raise SystemExit("No source code available for this transform.")
        logger.important(f"{transform.type} is here: {target_filename}")
        if with_env:
            if (
                transform.latest_run is not None
                and transform.latest_run.environment is not None
            ):
                filepath_env_cache = transform.latest_run.environment.cache()
                target_env_filename = (
                    ".".join(target_filename.split(".")[:-1]) + "__requirements.txt"
                )
                filepath_env_cache.rename(target_env_filename)
                logger.important(target_env_filename)
            else:
                logger.warning("latest transform run with environment doesn't exist")
        return target_filename
    elif entity == "artifact":
        artifact = ln.Artifact.get(uid) if uid is not None else ln.Artifact.get(key=key)
        cache_path = artifact.cache()
        logger.important(f"artifact is here: {cache_path}")
        return cache_path
