from typing import Tuple
from lamin_utils import logger
import lamindb_setup as ln_setup


def decompose_url(url: str) -> Tuple[str, str, str]:
    assert "transform" in url
    uid = url.split("transform/")[1]
    instance_slug = "/".join(url.replace("https://lamin.ai/", "").split("/")[:2])
    return instance_slug, "transform", uid


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
        transform = ln.Transform.filter(uid=uid).one()
        filepath_cache = transform.source_code.stage()
        target_filename = f"{transform.key}.ipynb"
        filepath_cache.rename(target_filename)
        logger.success(f"staged source code of transform {uid} as {target_filename}")
    else:
        raise NotImplementedError
