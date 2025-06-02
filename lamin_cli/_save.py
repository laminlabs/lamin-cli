from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

import click
from lamin_utils import logger

if TYPE_CHECKING:
    from pathlib import Path


def parse_uid_from_code(content: str, suffix: str) -> str | None:
    if suffix == ".py":
        track_pattern = re.compile(
            r'ln\.track\(\s*(?:transform\s*=\s*)?(["\'])([a-zA-Z0-9]{12,16})\1'
        )
        uid_pattern = re.compile(r'\.context\.uid\s*=\s*["\']([^"\']+)["\']')
    elif suffix == ".ipynb":
        track_pattern = re.compile(
            r'ln\.track\(\s*(?:transform\s*=\s*)?(?:\\"|\')([a-zA-Z0-9]{12,16})(?:\\"|\')'
        )
        # backward compat
        uid_pattern = re.compile(r'\.context\.uid\s*=\s*\\["\']([^"\']+)\\["\']')
    elif suffix in {".R", ".qmd", ".Rmd"}:
        track_pattern = re.compile(
            r'track\(\s*(?:transform\s*=\s*)?([\'"])([a-zA-Z0-9]{12,16})\1'
        )
        uid_pattern = None
    else:
        raise SystemExit(
            "Only .py, .ipynb, .R, .qmd, .Rmd files are supported for saving"
            " transforms."
        )

    # Search for matches in the entire file content
    uid_match = track_pattern.search(content)
    group_index = 1 if suffix == ".ipynb" else 2
    uid = uid_match.group(group_index) if uid_match else None

    if uid_pattern is not None and uid is None:
        uid_match = uid_pattern.search(content)
        uid = uid_match.group(1) if uid_match else None

    return uid


def save_from_path_cli(
    path: Path | str,
    key: str | None,
    description: str | None,
    stem_uid: str | None,
    project: str | None,
    space: str | None,
    branch: str | None,
    registry: str | None,
) -> str | None:
    import lamindb_setup as ln_setup
    from lamindb_setup.core.upath import LocalPathClasses, UPath, create_path

    # this will be gone once we get rid of lamin load or enable loading multiple
    # instances sequentially
    auto_connect_state = ln_setup.settings.auto_connect
    ln_setup.settings.auto_connect = True

    import lamindb as ln

    if not ln.setup.core.django.IS_SETUP:
        sys.exit(-1)
    from lamindb._finish import save_context_core

    ln_setup.settings.auto_connect = auto_connect_state

    # this allows to have the correct treatment of credentials in case of cloud paths
    path = create_path(path)
    # isinstance is needed to cast the type of path to UPath
    # to avoid mypy erors
    assert isinstance(path, UPath)
    if not path.exists():
        raise click.BadParameter(f"Path {path} does not exist", param_hint="path")

    if registry is None:
        suffixes_transform = {
            "py": {".py", ".ipynb"},
            "R": {".R", ".qmd", ".Rmd"},
        }
        registry = (
            "transform"
            if path.suffix in suffixes_transform["py"].union(suffixes_transform["R"])
            else "artifact"
        )

    if project is not None:
        project_record = ln.Project.filter(
            ln.Q(name=project) | ln.Q(uid=project)
        ).one_or_none()
        if project_record is None:
            raise ln.errors.InvalidArgument(
                f"Project '{project}' not found, either create it with `ln.Project(name='...').save()` or fix typos."
            )
    if space is not None:
        space_record = ln.Space.filter(ln.Q(name=space) | ln.Q(uid=space)).one_or_none()
        if space_record is None:
            raise ln.errors.InvalidArgument(
                f"Space '{space}' not found, either create it on LaminHub or fix typos."
            )
    if branch is not None:
        branch_record = ln.Branch.filter(
            ln.Q(name=branch) | ln.Q(uid=branch)
        ).one_or_none()
        if branch_record is None:
            raise ln.errors.InvalidArgument(
                f"Branch '{branch}' not found, either create it with `ln.Branch(name='...').save()` or fix typos."
            )

    is_cloud_path = not isinstance(path, LocalPathClasses)

    if registry == "artifact":
        ln.settings.creation.artifact_silence_missing_run_warning = True
        revises = None
        if stem_uid is not None:
            revises = (
                ln.Artifact.filter(uid__startswith=stem_uid)
                .order_by("-created_at")
                .first()
            )
            if revises is None:
                raise ln.errors.InvalidArgument("The stem uid is not found.")

        if is_cloud_path:
            if key is not None:
                logger.error("Do not pass --key for cloud paths")
                return "key-with-cloud-path"
        elif key is None and description is None:
            logger.error("Please pass a key or description via --key or --description")
            return "missing-key-or-description"

        artifact = ln.Artifact(path, key=key, description=description, revises=revises)
        if space is not None:
            artifact.space = space_record
        if branch is not None:
            artifact.branch = branch_record
        artifact.save()
        logger.important(f"saved: {artifact}")
        logger.important(f"storage path: {artifact.path}")
        if ln_setup.settings.storage.type == "s3":
            logger.important(f"storage url: {artifact.path.to_url()}")
        if project is not None:
            artifact.projects.add(project_record)
            logger.important(f"labeled with project: {project_record.name}")
        if ln_setup.settings.instance.is_remote:
            slug = ln_setup.settings.instance.slug
            logger.important(f"go to: https://lamin.ai/{slug}/artifact/{artifact.uid}")
        return None

    if registry == "transform":
        if is_cloud_path:
            logger.error("Can not register a transform from a cloud path")
            return "transform-with-cloud-path"

        if path.suffix in {".qmd", ".Rmd"}:
            html_file_exists = path.with_suffix(".html").exists()
            nb_html_file_exists = path.with_suffix(".nb.html").exists()

            if not html_file_exists and not nb_html_file_exists:
                logger.error(
                    f"Please export your {path.suffix} file as an html file here"
                    f" {path.with_suffix('.html')}"
                )
                return "export-qmd-Rmd-as-html"
            elif html_file_exists and nb_html_file_exists:
                logger.error(
                    f"Please delete one of\n - {path.with_suffix('.html')}\n -"
                    f" {path.with_suffix('.nb.html')}"
                )
                return "delete-html-or-nb-html"

        with path.open() as file:
            content = file.read()
        uid = parse_uid_from_code(content, path.suffix)

        if uid is not None:
            logger.important(f"mapped '{path.name}' on uid '{uid}'")
            if len(uid) == 16:
                # is full uid
                transform = ln.Transform.filter(uid=uid).one_or_none()
            else:
                # is stem uid
                if stem_uid is not None:
                    assert stem_uid == uid, (
                        "passed stem uid and parsed stem uid do not match"
                    )
                else:
                    stem_uid = uid
                transform = (
                    ln.Transform.filter(uid__startswith=uid)
                    .order_by("-created_at")
                    .first()
                )
                if transform is None:
                    uid = f"{stem_uid}0000"
        else:
            # TODO: account for folders and hash equivalence as we do in ln.track()
            transform = ln.Transform.filter(key=path.name, is_latest=True).one_or_none()
        revises = None
        if stem_uid is not None:
            revises = (
                ln.Transform.filter(uid__startswith=stem_uid)
                .order_by("-created_at")
                .first()
            )
            if revises is None:
                raise ln.errors.InvalidArgument("The stem uid is not found.")
        if transform is None:
            if path.suffix == ".ipynb":
                from nbproject.dev import read_notebook
                from nbproject.dev._meta_live import get_title

                nb = read_notebook(path)
                description = get_title(nb)
            else:
                description = None
            transform = ln.Transform(
                uid=uid,
                description=description,
                key=path.name,
                type="script" if path.suffix in {".R", ".py"} else "notebook",
                revises=revises,
            )
            if space is not None:
                transform.space = space_record
            if branch is not None:
                transform.branch = branch_record
            transform.save()
            logger.important(f"created Transform('{transform.uid}')")
        if project is not None:
            transform.projects.add(project_record)
            logger.important(f"labeled with project: {project_record.name}")
        # latest run of this transform by user
        run = ln.Run.filter(transform=transform).order_by("-started_at").first()
        if run is not None and run.created_by.id != ln_setup.settings.user.id:
            response = input(
                "You are trying to save a transform created by another user: Source"
                " and report files will be tagged with *your* user id. Proceed?"
                " (y/n)"
            )
            if response != "y":
                return "aborted-save-notebook-created-by-different-user"
        if run is None and transform.key.endswith(".ipynb"):
            run = ln.Run(transform=transform).save()
            logger.important(
                f"found no run, creating Run('{run.uid}') to display the html"
            )
        return_code = save_context_core(
            run=run,
            transform=transform,
            filepath=path,
            from_cli=True,
        )
        return return_code
    else:
        raise SystemExit("Allowed values for '--registry' are: 'artifact', 'transform'")
