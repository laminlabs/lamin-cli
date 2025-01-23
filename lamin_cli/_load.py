from __future__ import annotations
from typing import Tuple
from lamin_utils import logger
import shutil
import re
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
    import lamindb_setup as ln_setup

    if entity.startswith("https://") and "lamin" in entity:
        url = entity
        instance, entity, uid = decompose_url(url)
    elif entity not in {"artifact", "transform"}:
        raise SystemExit("Entity has to be a laminhub URL or 'artifact' or 'transform'")
    else:
        instance = ln_setup.settings.instance.slug

    ln_setup.connect(instance)
    import lamindb as ln

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
            elif transform.source_code.startswith("# %% [markdown]\n#\n"):
                new_content = transform.source_code.replace(
                    "# %% [markdown]\n#\n",
                    f"# %% [markdown]\n# # {transform.description}\n",
                )
        else:  # R notebook
            # Pattern to match title only within YAML header section
            title_pattern = r'^---\n.*?title:\s*"([^"]*)".*?---'
            title_match = re.search(
                title_pattern, transform.source_code, flags=re.DOTALL | re.MULTILINE
            )
            new_content = transform.source_code
            if title_match:
                current_title = title_match.group(1)
                if current_title != transform.description:
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
            if uid in new_content:
                new_uid = f"{uid[:-4]}{increment_base62(uid[-4:])}"
                new_content = new_content.replace(uid, new_uid)
                logger.important(f"updated uid: {uid} → {new_uid}")
        if notebook_path.suffix == ".ipynb":
            notebook = jupytext.reads(new_content, fmt="py:percent")
            jupytext.write(notebook, notebook_path)
        else:
            notebook_path.write_text(new_content)

    query_by_uid = uid is not None

    if entity == "transform":
        if query_by_uid:
            # we don't use .get here because DoesNotExist is hard to catch
            # due to private django API
            # here full uid is not expected anymore as before
            # via ln.Transform.objects.get(uid=uid)
            transforms = ln.Transform.objects.filter(uid__startswith=uid)
        else:
            # if below, we take is_latest=True as the criterion, we might get draft notebooks
            # hence, we use source_code__isnull=False and order by created_at instead
            transforms = ln.Transform.objects.filter(key=key, source_code__isnull=False)

        if (n_transforms := len(transforms)) == 0:
            err_msg = f"uid {uid}" if query_by_uid else f"key={key} and source_code"
            raise SystemExit(f"Transform with {err_msg} does not exist.")

        if n_transforms > 1:
            transforms = transforms.order_by("-created_at")
        transform = transforms.first()

        target_relpath = Path(transform.key)
        if len(target_relpath.parents) > 1:
            logger.important(
                "preserve the folder structure for versioning:"
                f" {target_relpath.parent}/"
            )
            target_relpath.parent.mkdir(parents=True, exist_ok=True)
        if target_relpath.exists():
            response = input(f"! {target_relpath} exists: replace? (y/n)")
            if response != "y":
                raise SystemExit("Aborted.")

        if transform.source_code is not None:
            if target_relpath.suffix in (".ipynb", ".Rmd", ".qmd"):
                script_to_notebook(transform, target_relpath, bump_revision=True)
            else:
                target_relpath.write_text(transform.source_code)
        else:
            raise SystemExit("No source code available for this transform.")

        logger.important(f"{transform.type} is here: {target_relpath}")

        if with_env:
            ln.settings.track_run_inputs = False
            if (
                transform.latest_run is not None
                and transform.latest_run.environment is not None
            ):
                filepath_env_cache = transform.latest_run.environment.cache()
                target_env_filename = (
                    target_relpath.parent / f"{target_relpath.stem}__requirements.txt"
                )
                shutil.move(filepath_env_cache, target_env_filename)
                logger.important(f"environment is here: {target_env_filename}")
            else:
                logger.warning("latest transform run with environment doesn't exist")

        return target_relpath
    elif entity == "artifact":
        ln.settings.track_run_inputs = False

        if query_by_uid:
            # we don't use .get here because DoesNotExist is hard to catch
            # due to private django API
            artifacts = ln.Artifact.filter(uid__startswith=uid)
        else:
            artifacts = ln.Artifact.filter(key=key)

        if (n_artifacts := len(artifacts)) == 0:
            err_msg = f"uid={uid}" if query_by_uid else f"key={key}"
            raise SystemExit(f"Artifact with {err_msg} does not exist.")

        if n_artifacts > 1:
            artifacts = artifacts.order_by("-created_at")
        artifact = artifacts.first()

        cache_path = artifact.cache()
        logger.important(f"artifact is here: {cache_path}")
        return cache_path
