from __future__ import annotations
from typing import Tuple
from lamin_utils import logger
import lamindb_setup as ln_setup


def decompose_url(url: str) -> Tuple[str, str, str]:
    assert "transform" in url or "artifact" in url
    for entity in ["transform", "artifact"]:
        if entity in url:
            break
    uid = url.split(f"{entity}/")[1]
    instance_slug = "/".join(url.replace("https://lamin.ai/", "").split("/")[:2])
    return instance_slug, entity, uid


def get(url: str):
    if url.startswith("https://lamin.ai"):
        instance_slug, entity, uid = decompose_url(url)
    else:
        raise ValueError("url has to start with https://lamin.ai")

    auto_connect = ln_setup.settings.auto_connect
    # we don't want to auto-connect when importing lamindb
    ln_setup.settings.auto_connect = False

    import lamindb as ln

    ln_setup.settings.auto_connect = auto_connect
    ln.connect(instance_slug)
    ln.settings.verbosity = "success"

    if entity == "transform":
        transform = ln.Transform.get(uid)
        filepath_cache = transform.source_code.cache()
        target_filename = transform.key
        if not target_filename.endswith(transform.source_code.suffix):
            target_filename += transform.source_code.suffix
        filepath_cache.rename(target_filename)
        logger.success(f"cached source code of transform {uid} as {target_filename}")
    elif entity == "artifact":
        artifact = ln.Artifact.get(uid)
        cache_path = artifact.cache()
        logger.success(f"cached artifact {artifact} here:\n{cache_path}")
