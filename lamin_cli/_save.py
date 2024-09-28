from __future__ import annotations
from pathlib import Path
from typing import Union
import lamindb_setup as ln_setup
from lamin_utils import logger
import re


def parse_uid_from_code(
    content: str, suffix: str
) -> tuple[str | None, str | None, str | None]:
    if suffix == ".py":
        track_pattern = re.compile(r'ln\.track\(\s*(?:uid\s*=\s*)?["\']([^"\']+)["\']')
        uid_pattern = re.compile(r'\.context\.uid\s*=\s*["\']([^"\']+)["\']')
        stem_uid_pattern = re.compile(
            r'\.transform\.stem_uid\s*=\s*["\']([^"\']+)["\']'
        )
        version_pattern = re.compile(r'\.transform\.version\s*=\s*["\']([^"\']+)["\']')
    elif suffix == ".ipynb":
        track_pattern = re.compile(
            r'ln\.track\(\s*(?:uid\s*=\s*)?\\["\']([^"\']+)\\["\']'
        )
        uid_pattern = re.compile(r'\.context\.uid\s*=\s*\\["\']([^"\']+)\\["\']')
        stem_uid_pattern = re.compile(
            r'\.transform\.stem_uid\s*=\s*\\["\']([^"\']+)\\["\']'
        )
        version_pattern = re.compile(
            r'\.transform\.version\s*=\s*\\["\']([^"\']+)\\["\']'
        )
    else:
        raise ValueError("Only .py and .ipynb files are supported.")

    # Search for matches in the entire file content
    uid_match = track_pattern.search(content)
    uid = uid_match.group(1) if uid_match else None
    if uid is None:
        uid_match = uid_pattern.search(content)
    stem_uid_match = stem_uid_pattern.search(content)
    version_match = version_pattern.search(content)

    # Extract values if matches are found
    uid = uid_match.group(1) if uid_match else None
    stem_uid = stem_uid_match.group(1) if stem_uid_match else None
    version = version_match.group(1) if version_match else None

    if uid is None and (stem_uid is None or version is None):
        raise SystemExit(
            "Cannot infer transform uid."
            "\nCall `ln.track()` and copy/paste the output"
            " into the notebook"
        )
    return uid, stem_uid, version


def save_from_filepath_cli(
    filepath: Union[str, Path],
    key: str | None,
    description: str | None,
    registry: str | None,
) -> str | None:
    if not isinstance(filepath, Path):
        filepath = Path(filepath)

    # this will be gone once we get rid of lamin load or enable loading multiple
    # instances sequentially
    auto_connect_state = ln_setup.settings.auto_connect
    ln_setup.settings.auto_connect = True

    import lamindb as ln
    from lamindb._finish import save_context_core

    ln_setup.settings.auto_connect = auto_connect_state

    if registry is None:
        registry = "transform" if filepath.suffix in {".py", ".ipynb"} else "artifact"

    if registry == "artifact":
        ln.settings.creation.artifact_silence_missing_run_warning = True
        if key is None and description is None:
            logger.error("Please pass a key or description via --key or --description")
            return "missing-key-or-description"
        artifact = ln.Artifact(filepath, key=key, description=description).save()
        logger.important(f"saved: {artifact}")
        logger.important(f"storage path: {artifact.path}")
        if ln_setup.settings.storage.type == "s3":
            logger.important(f"storage url: {artifact.path.to_url()}")
        if ln_setup.settings.instance.is_remote:
            slug = ln_setup.settings.instance.slug
            logger.important(f"go to: https://lamin.ai/{slug}/artifact/{artifact.uid}")
        return None
    elif registry == "transform":
        with open(filepath) as file:
            content = file.read()
        uid, stem_uid, version = parse_uid_from_code(content, filepath.suffix)
        logger.important(f"mapped '{filepath}' on uid '{uid}'")
        if uid is not None:
            transform = ln.Transform.filter(uid=uid).one_or_none()
            if transform is None:
                logger.error(
                    f"Did not find uid '{uid}'"
                    " in Transform registry. Did you run `ln.track()`?"
                )
                return "not-tracked-in-transform-registry"
        else:
            transform = ln.Transform.get(uid__startswith=stem_uid, version=version)
        # latest run of this transform by user
        run = ln.Run.filter(transform=transform).order_by("-started_at").first()
        if run.created_by.id != ln_setup.settings.user.id:
            response = input(
                "You are trying to save a transform created by another user: Source and"
                " report files will be tagged with *your* user id. Proceed? (y/n)"
            )
            if response != "y":
                return "aborted-save-notebook-created-by-different-user"
        return save_context_core(
            run=run,
            transform=transform,
            filepath=filepath,
            from_cli=True,
        )
    else:
        raise SystemExit("Allowed values for '--registry' are: 'artifact', 'transform'")
