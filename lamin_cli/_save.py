from __future__ import annotations
from pathlib import Path
from typing import Optional, Union
import lamindb_setup as ln_setup
from lamin_utils import logger
import re


def get_stem_uid_and_version_from_file(file_path: Path) -> tuple[str, str]:
    # line-by-line matching might be faster, but let's go with this for now
    with open(file_path) as file:
        content = file.read()

    if file_path.suffix == ".py":
        stem_uid_pattern = re.compile(
            r'\.transform\.stem_uid\s*=\s*["\']([^"\']+)["\']'
        )
        version_pattern = re.compile(r'\.transform\.version\s*=\s*["\']([^"\']+)["\']')
    elif file_path.suffix == ".ipynb":
        stem_uid_pattern = re.compile(
            r'\.transform\.stem_uid\s*=\s*\\["\']([^"\']+)\\["\']'
        )
        version_pattern = re.compile(
            r'\.transform\.version\s*=\s*\\["\']([^"\']+)\\["\']'
        )
    else:
        raise ValueError("Only .py and .ipynb files are supported.")

    # Search for matches in the entire file content
    stem_uid_match = stem_uid_pattern.search(content)
    version_match = version_pattern.search(content)

    # Extract values if matches are found
    stem_uid = stem_uid_match.group(1) if stem_uid_match else None
    version = version_match.group(1) if version_match else None

    if stem_uid is None or version is None:
        raise SystemExit(
            "ln.settings.transform.stem_uid and ln.settings.transform.version aren't"
            f" set in {file_path}\nCall ln.track() and copy/paste the output into the"
            " notebook"
        )
    return stem_uid, version


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
            from_cli=True,
        )
