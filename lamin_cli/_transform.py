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

    uid_prefix = base62_12()
    version = "0"

    with open(script_path) as f:
        content = f.read()
    prepend = f'__lamindb_uid_prefix__ = "{uid_prefix}"\n__version__ = "{version}"\n'
    with open(script_path, "w") as f:
        f.write(prepend + content)
    logger.success("added __lamindb_uid_prefix__ & __version__ to .py file")


def get_script_metadata(file_path: str) -> Tuple[str, str]:
    with open(file_path, "r") as file:
        content = file.read()

    # Define patterns for __lamindb_uid_prefix__ and __version__ variables
    uid_prefix_pattern = re.compile(r'__lamindb_uid_prefix__\s*=\s*["\']([^"\']+)["\']')
    version_pattern = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')

    # Search for matches in the entire file content
    uid_prefix_match = uid_prefix_pattern.search(content)
    version_match = version_pattern.search(content)

    # Extract values if matches are found
    uid_prefix = uid_prefix_match.group(1) if uid_prefix_match else None
    version = version_match.group(1) if version_match else None

    if uid_prefix is None or version is None:
        raise ValueError(
            f"Did not find __lamindb_uid_prefix__ and __version__ in script {file_path}"
        )
    return uid_prefix, version


# also see lamindb.dev._run_context.reinitialize_notebook for related code
def update_transform_source_metadata(
    content: Any,
    filepath: str,
    run_from_cli: bool = True,
    bump_version: bool = False,
) -> (bool, str, str):  # type:ignore
    # here, content is either a Mapping representing the Notebook metadata
    # or the content of a source file
    if filepath.endswith(".ipynb"):
        is_notebook = True

        from nbproject.dev import write_notebook
        from nbproject.dev._initialize import nbproject_id

        uid_prefix = content.metadata["nbproject"]["id"]
        version = content.metadata["nbproject"]["version"]
    else:
        is_notebook = False
        uid_prefix, version = get_script_metadata(filepath)
    logger.important(
        f"transform is tracked with uid_prefix='{uid_prefix}', version: '{version}'"
    )
    updated = False
    # ask for generating a new uid prefix
    if not bump_version:
        if os.getenv("LAMIN_TESTING") is None:
            response = input("Do you want to generate a new uid prefix? (y/n) ")
        else:
            response = "y"
        if response == "y":
            new_uid_prefix = nbproject_id()
            updated = True
        else:
            bump_version = True
    new_version = version
    if bump_version:
        new_uid_prefix = uid_prefix
        if os.getenv("LAMIN_TESTING") is None:
            new_version = input(
                f"The current version is '{version}' - please type the new version: "
            )
        else:
            new_version = str(int(version) + 1)
        updated = new_version != version
    if updated and run_from_cli:
        if is_notebook:
            logger.save("updated notebook")
            content.metadata["nbproject"]["id"] = new_uid_prefix
            content.metadata["nbproject"]["version"] = new_version
            write_notebook(content, filepath)
        else:
            logger.save("updated script")
            old_metadata = (
                f'__lamindb_uid_prefix__ = "{uid_prefix}"\n__version__ = "{version}"\n'
            )
            new_metadata = (
                f'__lamindb_uid_prefix__ = "{new_uid_prefix}"\n__version__ ='
                f' "{new_version}"\n'
            )
            if old_metadata not in content:
                raise ValueError(
                    f"Cannot find {old_metadata} block in script, please re-format as"
                    " block to update"
                )
            with open(filepath, "w") as f:
                f.write(content.replace(old_metadata, new_metadata))
    return updated, new_uid_prefix, new_version


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
            logger.success("added uid_prefix & version to ipynb file metadata")
        else:
            update_transform_source_metadata(nb, filepath, bump_version=bump_version)
    elif filepath.endswith(".py"):
        with open(filepath) as f:
            content = f.read()
        if "__lamindb_uid_prefix__" not in content:
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
        uid_prefix, transform_version = meta_store.id, meta_store.version
    else:
        is_notebook = False
        uid_prefix, transform_version = get_script_metadata(filepath)

    import lamindb as ln

    ln.settings.verbosity = "success"

    # the corresponding transform family in the transform table
    transform_family = ln.Transform.filter(uid__startswith=uid_prefix).all()
    if len(transform_family) == 0:
        logger.error(
            f"Did not find notebook with uid prefix {uid_prefix}"
            " in transform registry. Did you run ln.track()?"
        )
        return "not-tracked-in-transform-registry"
    # the specific version
    transform = transform_family.filter(version=transform_version).one()
    if is_notebook:
        # latest run of this transform by user
        run = ln.Run.filter(transform=transform).order_by("-run_at").first()
        if run.created_by.id != lamindb_setup.settings.user.id:
            response = input(
                "You are trying to save a notebook created by another user: Source and"
                " report files will be tagged with *your* user id. Proceed? (y/n)"
            )
            if response != "y":
                return "aborted-save-notebook-created-by-different-user"
        # convert the notebook file to html
        filepath_html = filepath.replace(".ipynb", ".html")
        # log_level is set to 40 to silence the nbconvert logging
        result = subprocess.run(
            f"jupyter nbconvert --to html {filepath} --Application.log_level=40",
            shell=True,
        )
        assert result.returncode == 0
        # copy the notebook file to a temporary file
        source_file_path = filepath.replace(".ipynb", "_stripped.ipynb")
        shutil.copy2(filepath, source_file_path)
        result = subprocess.run(f"nbstripout {source_file_path}", shell=True)
        assert result.returncode == 0
    else:
        source_file_path = filepath
    # find initial versions of source files and html reports
    initial_report = None
    initial_source = None
    if len(transform_family) > 0:
        for prev_transform in transform_family.order_by("-created_at"):
            # check for id to avoid query
            if prev_transform.latest_report_id is not None:
                # any previous latest report of this transform is OK!
                initial_report = prev_transform.latest_report
            if prev_transform.source_file_id is not None:
                # any previous source file id is OK!
                initial_source = prev_transform.source_file
    ln.settings.silence_file_run_transform_warning = True
    # register the source code
    if transform.source_file is not None:
        # check if the hash of the notebook source file matches
        check_source_file = ln.File(source_file_path, key="dummy")
        if check_source_file._state.adding:
            if os.getenv("LAMIN_TESTING") is None:
                # in test, auto-confirm overwrite
                response = input(
                    "You try to save a new notebook source file with the same version"
                    f" '{transform.version}'; do you want to replace the content of the"
                    f" existing source file {transform.source_file}? (y/n)"
                )
            else:
                response = "y"
            if response == "y":
                transform.source_file.replace(source_file_path)
                transform.source_file.save()
            else:
                logger.warning(
                    "Please create a new version of the notebook via `lamin track"
                    " <filepath>` and re-run the notebook"
                )
                return "rerun-the-notebook"
    else:
        source_file = ln.File(
            source_file_path,
            description=f"Source of transform {transform.uid}",
            version=transform_version,
            is_new_version_of=initial_source,
            visibility=0,  # hidden file
        )
        source_file.save()
        transform.source_file = source_file
    # save report file
    if is_notebook:
        if run.report_id is not None:
            logger.warning(
                "there is already an existing report for this run, replacing it"
            )
            run.report.replace(filepath_html)
            run.report.save()
        else:
            report_file = ln.File(
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
        Path(source_file_path).unlink()
        Path(filepath_html).unlink()
    logger.success(f"saved transform.source_file: {transform.source_file}")
    if is_notebook:
        logger.success(f"saved transform.latest_report: {transform.latest_report}")
    return None
