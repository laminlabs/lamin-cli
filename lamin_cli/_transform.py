import os
import shutil
from typing import Tuple
import subprocess
from pathlib import Path
from typing import Optional, Any
import re
import lamindb_setup
from lamin_utils import colors, logger


def init_script_metadata(script_path: str):
    from lnschema_core.ids import base62_12

    stem_uid = base62_12()
    version = "0"

    with open(script_path) as f:
        content = f.read()
    prepend = f'__transform_stem_uid__ = "{stem_uid}"\n__version__ = "{version}"\n'
    with open(script_path, "w") as f:
        f.write(prepend + content)
    logger.success("added __transform_stem_uid__ & __version__ to .py file")


def get_script_metadata(file_path: str) -> Tuple[str, str]:
    with open(file_path, "r") as file:
        content = file.read()

    # Define patterns for __transform_stem_uid__ and __version__ variables
    stem_uid_pattern = re.compile(r'__transform_stem_uid__\s*=\s*["\']([^"\']+)["\']')
    version_pattern = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')

    # Search for matches in the entire file content
    stem_uid_match = stem_uid_pattern.search(content)
    version_match = version_pattern.search(content)

    # Extract values if matches are found
    stem_uid = stem_uid_match.group(1) if stem_uid_match else None
    version = version_match.group(1) if version_match else None

    if stem_uid is None or version is None:
        raise ValueError(
            f"Did not find __transform_stem_uid__ and __version__ in script {file_path}"
        )
    return stem_uid, version


# also see lamindb.dev._run_context.reinitialize_notebook for related code
def update_transform_source_metadata(
    content: Any,
    filepath: str,
    run_from_cli: bool = True,
    bump_version: bool = False,
) -> (bool, str, str):  # type:ignore
    # here, content is either a Mapping representing the Notebook metadata
    # or the content of a source code
    if filepath.endswith(".ipynb"):
        is_notebook = True

        from nbproject.dev import write_notebook
        from nbproject.dev._initialize import nbproject_id

        stem_uid = content.metadata["nbproject"]["id"]
        version = content.metadata["nbproject"]["version"]
    else:
        is_notebook = False
        stem_uid, version = get_script_metadata(filepath)
    from lamin_utils._base62 import encodebytes
    import hashlib

    # the following line is duplicated with get_transform_kwargs_from_stem_uid
    # in lamindb - we should move it, e.g., to lamin-utils
    # it also occurs a few lines below
    uid_ext = encodebytes(hashlib.md5(version.encode()).digest())[:4]
    # it simply looks better here to not use the logger because we won't have an
    # emoji also for the subsequent input question
    print(
        f"Transform is tracked with stem_uid='{stem_uid}' & version='{version}'"
        f" (uid='{stem_uid}{uid_ext}')"
    )
    updated = False
    # ask for generating a new stem uid
    response = "bump"
    if not bump_version:
        if os.getenv("LAMIN_TESTING") is None:
            response = input(
                "To create a new stem uid, type 'new'. To bump the version, type 'bump'"
                " or a custom version: "
            )
        else:
            response = "new"
        if response == "new":
            new_stem_uid = nbproject_id()
            updated = True
        else:
            bump_version = True
    new_version = version
    if bump_version:
        new_stem_uid = stem_uid
        if response == "bump":
            try:
                new_version = str(int(version) + 1)
            except ValueError:
                new_version = input(
                    f"The current version is '{version}' - please type the new"
                    " version: "
                )
        else:
            new_version = response
        updated = new_version != version
    if updated and run_from_cli:
        display_info = (
            f"version='{new_version}'" if bump_version else f"stem_uid='{new_stem_uid}'"
        )
        new_uid_ext = encodebytes(hashlib.md5(new_version.encode()).digest())[:4]
        display_info += f" (uid='{new_stem_uid}{new_uid_ext}')"
        if is_notebook:
            logger.save(f"updated notebook: {display_info}")
            content.metadata["nbproject"]["id"] = new_stem_uid
            content.metadata["nbproject"]["version"] = new_version
            write_notebook(content, filepath)
        else:
            logger.save(f"updated script: {display_info}")
            old_metadata = (
                f'__transform_stem_uid__ = "{stem_uid}"\n__version__ = "{version}"\n'
            )
            new_metadata = (
                f'__transform_stem_uid__ = "{new_stem_uid}"\n__version__ ='
                f' "{new_version}"\n'
            )
            if old_metadata not in content:
                raise ValueError(
                    f"Cannot find {old_metadata} block in script, please re-format as"
                    " block to update"
                )
            with open(filepath, "w") as f:
                f.write(content.replace(old_metadata, new_metadata))
    return updated, new_stem_uid, new_version


def track(
    filepath: str, pypackage: Optional[str] = None, bump_version: bool = False
) -> None:
    try:
        from nbproject.dev import initialize_metadata, read_notebook, write_notebook
    except ImportError:
        logger.error("install nbproject: pip install nbproject")
        return None

    if filepath.endswith(".ipynb"):
        nb = read_notebook(filepath)
        if "nbproject" not in nb.metadata:
            if pypackage is not None:
                pypackage = [pp for pp in pypackage.split(",") if len(pp) > 0]  # type: ignore # noqa
            metadata = initialize_metadata(nb, pypackage=pypackage).dict()
            nb.metadata["nbproject"] = metadata
            write_notebook(nb, filepath)
            logger.success("added stem_uid & version to ipynb file metadata")
        else:
            update_transform_source_metadata(nb, filepath, bump_version=bump_version)
    elif filepath.endswith(".py"):
        with open(filepath) as f:
            content = f.read()
        if "__transform_stem_uid__" not in content:
            init_script_metadata(filepath)
        else:
            update_transform_source_metadata(
                content, filepath, bump_version=bump_version
            )
    else:
        raise ValueError("Only .py and .ipynb files can be tracked as transforms")
    return None


def save(filepath: str) -> Optional[str]:
    if filepath.endswith(".ipynb"):
        is_notebook = True
        try:
            import nbstripout  # noqa
            from nbproject.dev import (
                MetaContainer,
                MetaStore,
                check_consecutiveness,
                read_notebook,
            )
            from nbproject.dev._meta_live import get_title
        except ImportError:
            logger.error(
                "install nbproject & nbstripout: pip install nbproject nbstripout"
            )
            return None
        nb = read_notebook(filepath)  # type: ignore
        nb_meta = nb.metadata
        is_consecutive = check_consecutiveness(nb)
        if not is_consecutive:
            if os.getenv("LAMIN_TESTING") is None:
                decide = input(
                    "   Do you still want to proceed with publishing? (y/n) "
                )
            else:
                decide = "n"
            if decide != "y":
                logger.error("Aborted (non-consecutive)!")
                return "aborted-non-consecutive"
        if get_title(nb) is None:
            logger.error(
                f"No title! Update & {colors.bold('save')} your notebook with a title"
                " '# My title' in the first cell."
            )
            return "no-title"
        if nb_meta is not None and "nbproject" in nb_meta:
            meta_container = MetaContainer(**nb_meta["nbproject"])
        else:
            logger.error("notebook isn't initialized, run lamin track <filepath>")
            return "not-initialized"

        meta_store = MetaStore(meta_container, filepath)
        stem_uid, transform_version = meta_store.id, meta_store.version
    else:
        is_notebook = False
        stem_uid, transform_version = get_script_metadata(filepath)

    import lamindb as ln

    ln.settings.verbosity = "success"

    # the corresponding transform family in the transform table
    transform_family = ln.Transform.filter(uid__startswith=stem_uid).all()
    if len(transform_family) == 0:
        logger.error(
            f"Did not find notebook with uid prefix {stem_uid}"
            " in transform registry. Did you run ln.track()?"
        )
        return "not-tracked-in-transform-registry"
    # the specific version
    transform = transform_family.filter(version=transform_version).one()
    # latest run of this transform by user
    run = ln.Run.filter(transform=transform).order_by("-run_at").first()
    if run.created_by.id != lamindb_setup.settings.user.id:
        response = input(
            "You are trying to save a transform created by another user: Source and"
            " report files will be tagged with *your* user id. Proceed? (y/n)"
        )
        if response != "y":
            return "aborted-save-notebook-created-by-different-user"
    if is_notebook:
        # convert the notebook file to html
        filepath_html = filepath.replace(".ipynb", ".html")
        # log_level is set to 40 to silence the nbconvert logging
        result = subprocess.run(
            f"jupyter nbconvert --to html {filepath} --Application.log_level=40",
            shell=True,
        )
        assert result.returncode == 0
        # copy the notebook file to a temporary file
        source_code_path = filepath.replace(".ipynb", "_stripped.ipynb")
        shutil.copy2(filepath, source_code_path)
        result = subprocess.run(f"nbstripout {source_code_path}", shell=True)
        assert result.returncode == 0
    else:
        source_code_path = filepath
    # find initial versions of source codes and html reports
    initial_report = None
    initial_source = None
    if len(transform_family) > 0:
        for prev_transform in transform_family.order_by("-created_at"):
            # check for id to avoid query
            if prev_transform.latest_report_id is not None:
                # any previous latest report of this transform is OK!
                initial_report = prev_transform.latest_report
            if prev_transform.source_code_id is not None:
                # any previous source code id is OK!
                initial_source = prev_transform.source_code
    ln.settings.silence_file_run_transform_warning = True
    # register the source code
    if transform.source_code is not None:
        # check if the hash of the notebook source code matches
        check_source_code = ln.Artifact(source_code_path, key="dummy")
        if check_source_code._state.adding:
            if os.getenv("LAMIN_TESTING") is None:
                # in test, auto-confirm overwrite
                response = input(
                    "You try to save a new notebook source code with the same version"
                    f" '{transform.version}'; do you want to replace the content of the"
                    f" existing source code {transform.source_code}? (y/n)"
                )
            else:
                response = "y"
            if response == "y":
                transform.source_code.replace(source_code_path)
                transform.source_code.save()
            else:
                logger.warning(
                    "Please create a new version of the notebook via `lamin track"
                    " <filepath>` and re-run the notebook"
                )
                return "rerun-the-notebook"
    else:
        source_code = ln.Artifact(
            source_code_path,
            description=f"Source of transform {transform.uid}",
            version=transform_version,
            is_new_version_of=initial_source,
            visibility=0,  # hidden file
        )
        source_code.save()
        transform.source_code = source_code
    # track environment
    filepath_env = (
        lamindb_setup.settings.storage.cache_dir / f"run_env_pip_{run.uid}.txt"
    )
    if filepath_env.exists():
        artifact = ln.Artifact(
            filepath_env, description="requirements.txt", visibility=0
        )
        if artifact._state.adding:
            artifact.save()
        run.environment = artifact
        logger.success(f"saved run.environment: {run.environment}")
    # save report file
    if not is_notebook:
        run.save()
    else:
        if run.report_id is not None:
            logger.warning(
                "there is already an existing report for this run, replacing it"
            )
            run.report.replace(filepath_html)
            run.report.save()
        else:
            report_file = ln.Artifact(
                filepath_html,
                description=f"Report of transform {transform.uid}",
                is_new_version_of=initial_report,
                visibility=0,  # hidden file
            )
            report_file.save()
            run.report = report_file
        run.is_consecutive = is_consecutive
        run.save()
        transform.latest_report = run.report
    transform.save()
    if is_notebook:
        # clean up
        Path(source_code_path).unlink()
        Path(filepath_html).unlink()
    logger.success(f"saved transform.source_code: {transform.source_code}")
    if is_notebook:
        logger.success(f"saved transform.latest_report: {transform.latest_report}")
    return None
