from typing import Tuple
from lamin_utils import logger
import lamindb_setup as ln_setup


def decompose_url(url: str) -> Tuple[str, str, str]:
    assert "transform" in url
    uid = url.split("transform/")[1]
    instance_slug = "/".join(url.replace("https://lamin.ai/", "").split("/")[:2])
    return instance_slug, "transform", uid


def stage(instance_identifier: str):
    # this will be gone once we get rid of lamin load or enable loading multiple
    # instances sequentially
    auto_connect_state = ln_setup.settings.auto_connect
    ln_setup.settings.auto_connect = True
    import lamindb as ln

    ln_setup.settings.auto_connect = auto_connect_state

    ln.settings.verbosity = "success"
    if instance_identifier.startswith("https://lamin.ai"):
        instance_slug, entity, uid = decompose_url(instance_identifier)
    else:
        entity, uid = instance_identifier.split()
        instance_slug = ln.setup.settings.instance.slug

    if entity == "transform":
        transform = ln.Transform.using(instance_slug).filter(uid=uid).one()
        filepath_cache = transform.source_code.stage()
        target_filename = f"{transform.key}.ipynb"
        filepath_cache.rename(target_filename)
        logger.success(f"staged source code of transform {uid} as {target_filename}")
    else:
        raise NotImplementedError
