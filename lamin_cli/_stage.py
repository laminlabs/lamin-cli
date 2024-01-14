from typing import Tuple
from lamin_utils import logger


def decompose_url(url: str) -> Tuple[str, str]:
    assert "Transform" in url
    uid = url.split("uid=")[1]
    return "transform", uid


def stage(identifier: str):
    if identifier.startswith("https://lamin.ai"):
        entity, uid = decompose_url(identifier)
    else:
        entity, uid = identifier.split()
    import lamindb as ln

    if entity == "transform":
        transform = ln.Transform.filter(uid=uid).one()
        filepath_cache = transform.source_code.stage()
        target_filename = f"{transform.short_name}.ipynb"
        filepath_cache.rename(target_filename)
        logger.success(f"staged source code of transform {uid}")
    else:
        raise NotImplementedError
