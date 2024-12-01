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
        track_pattern = re.compile(
            r'ln\.track\(\s*(?:transform\s*=\s*)?(["\'])([a-zA-Z0-9]{16})\1'
        )
        # backward compat
        uid_pattern = re.compile(r'\.context\.uid\s*=\s*["\']([^"\']+)["\']')
        stem_uid_pattern = re.compile(
            r'\.transform\.stem_uid\s*=\s*["\']([^"\']+)["\']'
        )
        version_pattern = re.compile(r'\.transform\.version\s*=\s*["\']([^"\']+)["\']')
    elif suffix == ".ipynb":
        track_pattern = re.compile(
            r'ln\.track\(\s*(?:transform\s*=\s*)?(?:\\"|\')([a-zA-Z0-9]{16})(?:\\"|\')'
        )
        # backward compat
        uid_pattern = re.compile(r'\.context\.uid\s*=\s*\\["\']([^"\']+)\\["\']')
        stem_uid_pattern = re.compile(
            r'\.transform\.stem_uid\s*=\s*\\["\']([^"\']+)\\["\']'
        )
        version_pattern = re.compile(
            r'\.transform\.version\s*=\s*\\["\']([^"\']+)\\["\']'
        )
    elif suffix in {".R", ".qmd", ".Rmd"}:
        track_pattern = re.compile(
            r'track\(\s*(?:transform\s*=\s*)?([\'"])([a-zA-Z0-9]{16})\1'
        )
        uid_pattern = None
        stem_uid_pattern = None
        version_pattern = None
    else:
        raise SystemExit(
            "Only .py, .ipynb, .R, .qmd, .Rmd files are supported for saving"
            " transforms."
        )

    # Search for matches in the entire file content
    uid_match = track_pattern.search(content)
    group_index = 1 if suffix == ".ipynb" else 2
    uid = uid_match.group(group_index) if uid_match else None
    stem_uid = None
    version = None

    if uid_pattern is not None and uid is None:
        uid_match = uid_pattern.search(content)
        uid = uid_match.group(1) if uid_match else None
    if stem_uid_pattern is not None:
        stem_uid_match = stem_uid_pattern.search(content)
        stem_uid = stem_uid_match.group(1) if stem_uid_match else None
    if version_pattern is not None:
        version_match = version_pattern.search(content)
        version = version_match.group(1) if version_match else None

    if uid is None and (stem_uid is None or version is None):
        target = "script" if suffix in {".py", ".R"} else "notebook"
        raise SystemExit(
            f"Cannot infer transform uid. Did you run `ln.track()` in your {target}?"
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

    suffixes_transform = {
        "py": set([".py", ".ipynb"]),
        "R": set([".R", ".qmd", ".Rmd"]),
    }

    if filepath.suffix in {".qmd", ".Rmd"}:
        if not (
            filepath.with_suffix(".html").exists()
            or filepath.with_suffix(".nb.html").exists()
        ):
            raise SystemExit(
                f"Please export your {filepath.suffix} file as an html file here"
                f" {filepath.with_suffix('.html')}"
            )
        if (
            filepath.with_suffix(".html").exists()
            and filepath.with_suffix(".nb.html").exists()
        ):
            raise SystemExit(
                f'Please delete one of\n - {filepath.with_suffix(".html")}\n -'
                f' {filepath.with_suffix(".nb.html")}'
            )

    if registry is None:
        registry = (
            "transform"
            if filepath.suffix
            in suffixes_transform["py"].union(suffixes_transform["R"])
            else "artifact"
        )

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
                "You are trying to save a transform created by another user: Source"
                " and report files will be tagged with *your* user id. Proceed?"
                " (y/n)"
            )
            if response != "y":
                return "aborted-save-notebook-created-by-different-user"
        return_code = save_context_core(
            run=run,
            transform=transform,
            filepath=filepath,
            from_cli=True,
        )
        return return_code
    else:
        raise SystemExit("Allowed values for '--registry' are: 'artifact', 'transform'")
