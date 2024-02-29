from typing import Tuple
from lamin_utils import logger


def decompose_url(url: str) -> Tuple[str, str, str]:
    assert "transform" in url
    uid = url.split("transform/")[1]
    instance_identifier = "/".join(url.replace("https://lamin.ai/", "").split("/")[:2])
    return instance_identifier, "transform", uid


def stage(identifier: str):
    import lamindb as ln

    ln.settings.verbosity = "success"
    if identifier.startswith("https://lamin.ai"):
        instance_identifier, entity, uid = decompose_url(identifier)
    else:
        entity, uid = identifier.split()
        instance_identifier = ln.setup.settings.instance.identifier

    if entity == "transform":
        transform = ln.Transform.using(instance_identifier).filter(uid=uid).one()
        filepath_cache = transform.source_code.stage()
        target_filename = f"{transform.short_name}.ipynb"
        filepath_cache.rename(target_filename)
        logger.success(f"staged source code of transform {uid} as {target_filename}")
    else:
        raise NotImplementedError
