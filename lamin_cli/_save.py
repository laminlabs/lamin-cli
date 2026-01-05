from __future__ import annotations

import os
import re
from pathlib import Path

import click
import lamindb_setup as ln_setup
from lamin_utils import logger
from lamindb_setup.core.hashing import hash_file

from lamin_cli._context import get_current_run_file


def infer_registry_from_path(path: Path | str) -> str:
    suffixes_transform = {
        "py": {".py", ".ipynb"},
        "R": {".R", ".qmd", ".Rmd"},
        "sh": {".sh"},
    }
    if isinstance(path, str):
        path = Path(path)
    registry = (
        "transform"
        if path.suffix
        in suffixes_transform["py"]
        .union(suffixes_transform["R"])
        .union(suffixes_transform["sh"])
        else "artifact"
    )
    return registry


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
    elif suffix == ".sh":
        return None
    else:
        raise SystemExit(
            "Only .py, .ipynb, .R, .qmd, .Rmd, .sh files are supported for saving"
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


def parse_title_r_notebook(content: str) -> str | None:
    # Pattern to match title only within YAML header section
    title_pattern = r'^---\n.*?title:\s*"([^"]*)".*?---'
    title_match = re.search(title_pattern, content, flags=re.DOTALL | re.MULTILINE)
    if title_match:
        return title_match.group(1)
    else:
        return None


def save(
    path: Path | str,
    key: str | None = None,
    description: str | None = None,
    stem_uid: str | None = None,
    project: str | None = None,
    space: str | None = None,
    branch: str | None = None,
    registry: str | None = None,
) -> str | None:
    import lamindb as ln
    from lamindb._finish import save_context_core
    from lamindb_setup.core._settings_store import settings_dir
    from lamindb_setup.core.upath import LocalPathClasses, UPath, create_path

    current_run = None
    if get_current_run_file().exists():
        current_run = ln.Run.get(uid=get_current_run_file().read_text().strip())

    # this allows to have the correct treatment of credentials in case of cloud paths
    ppath = create_path(path)
    # isinstance is needed to cast the type of path to UPath
    # to avoid mypy erors
    assert isinstance(ppath, UPath)
    if not ppath.exists():
        raise click.BadParameter(f"Path {ppath} does not exist", param_hint="path")

    if registry is None:
        registry = infer_registry_from_path(ppath)

    if project is not None:
        project_record = ln.Project.filter(
            ln.Q(name=project) | ln.Q(uid=project)
        ).one_or_none()
        if project_record is None:
            raise ln.errors.InvalidArgument(
                f"Project '{project}' not found, either create it with `ln.Project(name='...').save()` or fix typos."
            )
    space_record = None
    if space is not None:
        space_record = ln.Space.filter(ln.Q(name=space) | ln.Q(uid=space)).one_or_none()
        if space_record is None:
            raise ln.errors.InvalidArgument(
                f"Space '{space}' not found, either create it on LaminHub or fix typos."
            )
    branch_record = None
    if branch is not None:
        branch_record = ln.Branch.filter(
            ln.Q(name=branch) | ln.Q(uid=branch)
        ).one_or_none()
        if branch_record is None:
            raise ln.errors.InvalidArgument(
                f"Branch '{branch}' not found, either create it with `ln.Branch(name='...').save()` or fix typos."
            )

    is_cloud_path = not isinstance(ppath, LocalPathClasses)

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

        artifact = ln.Artifact(
            ppath,
            key=key,
            description=description,
            revises=revises,
            branch=branch_record,
            space=space_record,
            run=current_run,
        ).save()
        logger.important(f"saved: {artifact}")
        logger.important(f"storage path: {artifact.path}")
        if artifact.storage.type == "s3":
            logger.important(f"storage url: {artifact.path.to_url()}")
        if project is not None:
            artifact.projects.add(project_record)
            logger.important(f"labeled with project: {project_record.name}")
        if ln.setup.settings.instance.is_remote:
            slug = ln.setup.settings.instance.slug
            ui_url = ln.setup.settings.instance.ui_url
            logger.important(f"go to: {ui_url}/{slug}/artifact/{artifact.uid}")
        return None

    if registry == "transform":
        if key is not None:
            logger.warning(
                "key is ignored for transforms, the transform key is determined by the filename and the development directory (dev-dir)"
            )
        if is_cloud_path:
            logger.error("Can not register a transform from a cloud path")
            return "transform-with-cloud-path"

        if ppath.suffix in {".qmd", ".Rmd"}:
            html_file_exists = ppath.with_suffix(".html").exists()
            nb_html_file_exists = ppath.with_suffix(".nb.html").exists()

            if not html_file_exists and not nb_html_file_exists:
                logger.error(
                    f"Please export your {ppath.suffix} file as an html file here"
                    f" {ppath.with_suffix('.html')}"
                )
                return "export-qmd-Rmd-as-html"
            elif html_file_exists and nb_html_file_exists:
                logger.error(
                    f"Please delete one of\n - {ppath.with_suffix('.html')}\n -"
                    f" {ppath.with_suffix('.nb.html')}"
                )
                return "delete-html-or-nb-html"

        content = ppath.read_text()
        uid = parse_uid_from_code(content, ppath.suffix)

        ppath = ppath.resolve().expanduser()
        if ln_setup.settings.dev_dir is not None:
            key = ppath.relative_to(ln_setup.settings.dev_dir).as_posix()
        else:
            key = ppath.name

        if uid is not None:
            logger.important(f"mapped '{ppath.name}' on uid '{uid}'")
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
            _, transform_hash, _ = hash_file(ppath)
            transform = ln.Transform.filter(hash=transform_hash).first()
            if transform is not None and transform.hash is not None:
                if transform.hash == transform_hash:
                    if transform.type != "notebook":
                        logger.important(f"transform already saved: {transform}")
                        if transform.key != key:
                            transform.key = key
                            logger.important(f"updated key to '{key}'")
                            transform.save()
                        return None
                    if os.getenv("LAMIN_TESTING") == "true":
                        response = "y"
                    else:
                        response = input(
                            f"Found an existing Transform('{transform.uid}') "
                            "with matching source code hash.\n"
                            "Do you want to update it? (y/n) "
                        )
                    if response != "y":
                        return None
                else:
                    # we need to create a new version
                    stem_uid = transform.uid[:12]
                    transform = None
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
            if ppath.suffix == ".ipynb":
                from nbproject.dev import read_notebook
                from nbproject.dev._meta_live import get_title

                nb = read_notebook(ppath)
                description = get_title(nb)
            elif ppath.suffix in {".qmd", ".Rmd"}:
                description = parse_title_r_notebook(content)
            else:
                description = None
            transform = ln.Transform(
                uid=uid,
                description=description,
                key=key,
                type="script" if ppath.suffix in {".R", ".py", ".sh"} else "notebook",
                revises=revises,
            )
            if space is not None:
                transform.space = space_record
            if branch is not None:
                transform.branch = branch_record
            transform.save()
            logger.important(
                f"created Transform('{transform.uid}', key='{transform.key}')"
            )
        if project is not None:
            transform.projects.add(project_record)
            logger.important(f"labeled with project: {project_record.name}")
        # latest run of this transform by user
        run = ln.Run.filter(transform=transform).order_by("-started_at").first()
        if run is not None and run.created_by.id != ln.setup.settings.user.id:
            if os.getenv("LAMIN_TESTING") == "true":
                response = "y"
            else:
                response = input(
                    "You are trying to save a transform created by another user: Source"
                    " and report files will be tagged with *your* user id. Proceed?"
                    " (y/n) "
                )
            if response != "y":
                return "aborted-save-notebook-created-by-different-user"
        if run is None and transform.type == "notebook":
            run = ln.Run(transform=transform).save()
            logger.important(
                f"found no run, creating Run('{run.uid}') to display the html"
            )
        return_code = save_context_core(
            run=run,
            transform=transform,
            filepath=ppath,
            from_cli=True,
        )
        return return_code
    else:
        raise SystemExit("Allowed values for '--registry' are: 'artifact', 'transform'")
