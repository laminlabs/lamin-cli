import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

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


# also see lamindb.dev._run_context.reinitialize_notebook for related code
def update_metadata(content, filepath):
    # here, content is either a Mapping representing a Notebook
    # or the content of a source file
    if filepath.endswith(".ipynb"):
        is_notebook = True

        from nbproject.dev import write_notebook
        from nbproject.dev._initialize import nbproject_id

        uid_prefix = content.metadata["nbproject"]["id"]
        version = content.metadata["nbproject"]["version"]        
    else:
        is_notebook = False

        import importlib.util
        spec = importlib.util.spec_from_file_location("script", filepath)
        script_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script_module)

        uid_prefix = script_module.__lamindb_uid_prefix__
        version = script_module.__version__ 

    logger.info(
        f"the transform {filepath} is already tracked (uid_prefix='{uid_prefix}',"
        f" version: '{version}')"
    )
    updated = False
    # ask for generating new id
    if os.getenv("LAMIN_TESTING") is None:
        response = input("Do you want to generate a new uid prefix? (y/n) ")
    else:
        response = "y"
    if response == "y":
        new_uid_prefix = nbproject_id()
        updated = True
    else:
        new_uid_prefix = uid_prefix
        response = input(
            f"The current version is '{version}' - do you want to set a new"
            " version? (y/n) "
        )
        if response == "y":
            new_version = input("Please type the version: ")
            updated = True
    if updated:
        if is_notebook:
            logger.save("updated notebook")
            content.metadata["nbproject"]["id"] = new_uid_prefix
            content.metadata["nbproject"]["version"] = new_version
            write_notebook(content, filepath)
        else:
            logger.save("updated script")
            old_metadata = f'__lamindb_uid_prefix__ = "{uid_prefix}"\n__version__ = "{version}"\n'
            new_metadata = f'__lamindb_uid_prefix__ = "{new_uid_prefix}"\n__version__ = "{new_version}"\n'
            if old_metadata not in content:
                raise ValueError(f"Cannot find {old_metadata} block in script, please re-format as block to update")
            with open(filepath, "w") as f:
                f.write(content.replace(old_metadata, new_metadata))


def track(filepath: str, pypackage: Optional[str] = None) -> None:
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
            update_metadata(nb, filepath)
    elif filepath.endswith(".py"):
        with open(filepath) as f:
            content = f.read()
        if "__lamindb_uid_prefix__" not in content:
            init_script_metadata(filepath)
        else:
            update_metadata(content, filepath)
    else:
        raise ValueError("Only .py and .ipynb files can be tracked as transforms")
    return None


def save(notebook_path: str) -> Optional[str]:
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
        logger.error("install nbproject & nbstripout: pip install nbproject nbstripout")
        return None
    nb = read_notebook(notebook_path)  # type: ignore
    nb_meta = nb.metadata
    is_consecutive = check_consecutiveness(nb)
    if not is_consecutive:
        if os.getenv("LAMIN_TESTING") is None:
            decide = input("   Do you still want to proceed with publishing? (y/n) ")
        else:
            decide = "n"
        if decide != "y":
            logger.error("Aborted (non-consecutive)!")
            return "aborted-non-consecutive"
    if get_title(nb) is None:
        logger.error(
            f"No title! Update & {colors.bold('save')} your notebook with a title '# My"
            " title' in the first cell."
        )
        return "no-title"
    if nb_meta is not None and "nbproject" in nb_meta:
        meta_container = MetaContainer(**nb_meta["nbproject"])
    else:
        logger.error("notebook isn't initialized, run lamin track <notebook_path>")
        return "not-initialized"

    meta_store = MetaStore(meta_container, notebook_path)
    import lamindb as ln

    ln.settings.verbosity = "success"
    transform_version = meta_store.version
    # the corresponding transform family in the transform table
    transform_family = ln.Transform.filter(uid__startswith=meta_store.id).all()
    if len(transform_family) == 0:
        logger.error(
            f"Did not find notebook with uid prefix {meta_store.id} (12 initial characters)"
            " in transform registry. Did you run ln.track()?"
        )
        return "not-tracked-in-transform-registry"
    # the specific version
    transform = transform_family.filter(version=transform_version).one()
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
    notebook_path_html = notebook_path.replace(".ipynb", ".html")
    # log_level is set to 40 to silence the nbconvert logging
    result = subprocess.run(
        f"jupyter nbconvert --to html {notebook_path} --Application.log_level=40",
        shell=True,
    )
    assert result.returncode == 0
    # copy the notebook file to a temporary file
    notebook_path_stripped = notebook_path.replace(".ipynb", "_stripped.ipynb")
    shutil.copy2(notebook_path, notebook_path_stripped)
    result = subprocess.run(f"nbstripout {notebook_path_stripped}", shell=True)
    assert result.returncode == 0
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
        if ln.File(notebook_path_stripped, key="dummy")._state.adding:
            if os.getenv("LAMIN_TESTING") is None:
                # in test, auto-confirm overwrite
                response = input(
                    f"You try to save a new notebook source file with the same"
                    f" version {transform.version}; do you want to replace the content of the existing source file ({transform.source_file})? (y/n)"
                )
            else:
                response = "y"
            if response == "y":
                transform.source_file.replace(notebook_path_stripped)
            else:
                logger.warning(
                    "Please create a new version of the notebook via `lamin track"
                    " <notebook_path>` and re-run the notebook"
                )
                return "rerun-the-notebook"
    else:
        source_file = ln.File(
            notebook_path_stripped,
            description=f"Source of transform {transform.uid}",
            version=transform_version,
            is_new_version_of=initial_source,
            visibility=1,
        )
        source_file.save()
        transform.source_file = source_file
    # save report file
    if run.report_id is not None:
        logger.warning(
            "there is already an existing report for this run, replacing it"
        )
        run.report.replace(notebook_path_html)
    else:
        report_file = ln.File(
            notebook_path_html,
            description=f"Report of transform {transform.uid}",
            is_new_version_of=initial_report,
            visibility=1,
        )
        report_file.save()
        run.report = report_file
    run.is_consecutive = is_consecutive
    run.save()
    # annotate transform
    transform.latest_report = run.report
    transform.save()
    # clean up
    Path(notebook_path_stripped).unlink()
    Path(notebook_path_html).unlink()
    msg = "saved notebook and wrote source file and html report"
    msg += (
        f"\n\n{transform}\n\n.source_file: {transform.source_file}\n.latest_report:"
        f" {transform.latest_report}"
    )
    logger.success(msg)
    return None
