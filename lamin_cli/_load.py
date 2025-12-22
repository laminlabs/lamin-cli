from __future__ import annotations

import re
import shutil
from pathlib import Path

from lamin_utils import logger

from ._context import get_current_run_file
from ._save import infer_registry_from_path, parse_title_r_notebook
from .urls import decompose_url


def load(
    entity: str | None = None,
    uid: str | None = None,
    key: str | None = None,
    with_env: bool = False,
):
    """Load artifact, collection, or transform from LaminDB.

    Args:
        entity: URL containing 'lamin', or 'artifact', 'collection', or 'transform'
        uid: Unique identifier (prefix matching supported)
        key: Key identifier
        with_env: If True, also load environment requirements file for transforms

    Returns:
        Path to loaded transform, or None for artifacts/collections
    """
    import lamindb_setup as ln_setup

    if entity is None:
        if key is None:
            raise SystemExit("Either entity or key has to be provided.")
        else:
            entity = infer_registry_from_path(key)

    if entity.startswith("https://") and "lamin" in entity:
        url = entity
        instance, entity, uid = decompose_url(url)
    elif entity not in {"artifact", "transform", "collection"}:
        raise SystemExit(
            "Entity has to be a laminhub URL or 'artifact', 'collection', or 'transform'"
        )
    else:
        instance = ln_setup.settings.instance.slug

    ln_setup.connect(instance)
    import lamindb as ln

    current_run = None
    if get_current_run_file().exists():
        current_run = ln.Run.get(uid=get_current_run_file().read_text().strip())

    def script_to_notebook(
        transform: ln.Transform, notebook_path: Path, bump_revision: bool = False
    ) -> None:
        import jupytext
        from lamin_utils._base62 import increment_base62

        if notebook_path.suffix == ".ipynb":
            # below is backward compat
            if "# # transform.name" in transform.source_code:
                new_content = transform.source_code.replace(
                    "# # transform.name", f"# # {transform.description}"
                )
            elif transform.source_code.startswith("# %% [markdown]"):
                source_code_split = transform.source_code.split("\n")
                if source_code_split[1] == "#":
                    source_code_split[1] = f"# # {transform.description}"
                new_content = "\n".join(source_code_split)
            else:
                new_content = transform.source_code
        else:  # R notebook
            new_content = transform.source_code
            current_title = parse_title_r_notebook(new_content)
            if current_title is not None and current_title != transform.description:
                pattern = r'^(---\n.*?title:\s*)"([^"]*)"(.*?---)'
                replacement = f'\\1"{transform.description}"\\3'
                new_content = re.sub(
                    pattern,
                    replacement,
                    new_content,
                    flags=re.DOTALL | re.MULTILINE,
                )
                logger.important(
                    f"updated title to match description: {current_title} →"
                    f" {transform.description}"
                )
        if bump_revision:
            uid = transform.uid
            if (
                uid in new_content
            ):  # this only hits if it has the full uid, not for the stem uid
                new_uid = f"{uid[:-4]}{increment_base62(uid[-4:])}"
                new_content = new_content.replace(uid, new_uid)
                logger.important(f"updated uid: {uid} → {new_uid}")
        if notebook_path.suffix == ".ipynb":
            notebook = jupytext.reads(new_content, fmt="py:percent")
            jupytext.write(notebook, notebook_path)
        else:
            notebook_path.write_text(new_content)

    query_by_uid = uid is not None

    match entity:
        case "transform":
            if query_by_uid:
                # we don't use .get here because DoesNotExist is hard to catch
                # due to private django API
                # here full uid is not expected anymore as before
                # via ln.Transform.objects.get(uid=uid)
                transforms = ln.Transform.objects.filter(uid__startswith=uid)
            else:
                # if below, we take is_latest=True as the criterion, we might get draft notebooks
                # hence, we use source_code__isnull=False and order by created_at instead
                transforms = ln.Transform.objects.filter(
                    key=key, source_code__isnull=False
                )

            if (n_transforms := len(transforms)) == 0:
                err_msg = f"uid {uid}" if query_by_uid else f"key={key} and source_code"
                raise SystemExit(f"Transform with {err_msg} does not exist.")

            if n_transforms > 1:
                transforms = transforms.order_by("-created_at")
            transform = transforms.first()

            target_path = Path(transform.key)
            if ln_setup.settings.dev_dir is not None:
                target_path = ln_setup.settings.dev_dir / target_path
            if len(target_path.parents) > 1:
                target_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.exists():
                response = input(f"! {target_path} exists: replace? (y/n)")
                if response != "y":
                    raise SystemExit("Aborted.")

            if transform.source_code is not None:
                if target_path.suffix in (".ipynb", ".Rmd", ".qmd"):
                    script_to_notebook(transform, target_path, bump_revision=True)
                else:
                    target_path.write_text(transform.source_code)
            else:
                raise SystemExit("No source code available for this transform.")

            logger.important(f"{transform.type} is here: {target_path}")

            if with_env:
                ln.settings.track_run_inputs = False
                if (
                    transform.latest_run is not None
                    and transform.latest_run.environment is not None
                ):
                    filepath_env_cache = transform.latest_run.environment.cache()
                    target_env_filename = (
                        target_path.parent / f"{target_path.stem}__requirements.txt"
                    )
                    shutil.move(filepath_env_cache, target_env_filename)
                    logger.important(f"environment is here: {target_env_filename}")
                else:
                    logger.warning(
                        "latest transform run with environment doesn't exist"
                    )

            return target_path
        case "artifact" | "collection":
            ln.settings.track_run_inputs = False

            EntityClass = ln.Artifact if entity == "artifact" else ln.Collection

            # we don't use .get here because DoesNotExist is hard to catch due to private django API
            # we use `.objects` here because we don't want to exclude kind = __lamindb_run__ artifacts
            if query_by_uid:
                entities = EntityClass.objects.filter(uid__startswith=uid)
            else:
                entities = EntityClass.objects.filter(key=key)

            if (n_entities := len(entities)) == 0:
                err_msg = f"uid={uid}" if query_by_uid else f"key={key}"
                raise SystemExit(
                    f"{entity.capitalize()} with {err_msg} does not exist."
                )

            if n_entities > 1:
                entities = entities.order_by("-created_at")

            entity_obj = entities.first()
            cache_path = entity_obj.cache(is_run_input=current_run)

            # collection gives us a list of paths
            if isinstance(cache_path, list):
                logger.important(f"{entity} paths ({len(cache_path)} files):")
                for i, path in enumerate(cache_path):
                    if i < 5 or i >= len(cache_path) - 5:
                        logger.important(f"  [{i + 1}/{len(cache_path)}] {path}")
                    elif i == 5:
                        logger.important(f"  ... {len(cache_path) - 10} more files ...")
            else:
                logger.important(f"{entity} is here: {cache_path}")
        case _:
            raise AssertionError(f"unknown entity {entity}")
