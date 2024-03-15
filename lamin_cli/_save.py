import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Union
import lamindb_setup as ln_setup
from lamin_utils import logger


def save(filepath: Union[str, Path]) -> Optional[str]:
    if not isinstance(filepath, Path):
        filepath = Path(filepath)
    # this will be gone once we get rid of lamin load or enable loading multiple
    # instances sequentially
    auto_connect_state = ln_setup.settings.auto_connect
    ln_setup.settings.auto_connect = True
    import lamindb as ln
    from lamindb.core._run_context import get_stem_uid_and_version_from_file

    ln_setup.settings.auto_connect = auto_connect_state
    # nothing here should be tracked as an output artifact!
    run_context_run = ln.core.run_context.run
    ln.core.run_context.run = None
    is_notebook = False
    stem_uid, transform_version = get_stem_uid_and_version_from_file(filepath)

    if filepath.suffix == ".ipynb":
        is_notebook = True
        try:
            import nbstripout  # noqa
            from nbproject.dev import (
                check_consecutiveness,
                read_notebook,
            )
        except ImportError:
            logger.error(
                "install nbproject & nbstripout: pip install nbproject nbstripout"
            )
            return None
        nb = read_notebook(filepath)  # type: ignore
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

    ln.settings.verbosity = "success"

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
    if is_notebook:
        # convert the notebook file to html
        # log_level is set to 40 to silence the nbconvert logging
        result = subprocess.run(
            "jupyter nbconvert --to html"
            f" {filepath.as_posix()} --Application.log_level=40",
            shell=True,
        )
        # move the temporary file into the cache dir in case it's accidentally
        # in an existing storage location -> we want to move associated
        # artifacts into default storage and not register them in an existing
        # location
        filepath_html = filepath.with_suffix(".html")  # current location
        shutil.move(
            filepath_html, ln_setup.settings.storage.cache_dir / filepath_html.name
        )  # move; don't use Path.rename here because of cross-device link error
        # see https://laminlabs.slack.com/archives/C04A0RMA0SC/p1710259102686969
        filepath_html = (
            ln_setup.settings.storage.cache_dir / filepath_html.name
        )  # adjust location
        assert result.returncode == 0
        # copy the notebook file to a temporary file
        source_code_path = ln_setup.settings.storage.cache_dir / filepath.name
        shutil.copy2(filepath, source_code_path)  # copy
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
    filepath_env = ln_setup.settings.storage.cache_dir / f"run_env_pip_{run.uid}.txt"
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
                description=f"Report of run {run.uid}",
                is_new_version_of=initial_report,
                visibility=0,  # hidden file
            )
            report_file.save()
            run.report = report_file
        run.is_consecutive = is_consecutive
        run.save()
        transform.latest_report = run.report
    transform.save()
    logger.success(f"saved transform.source_code: {transform.source_code}")
    if is_notebook:
        logger.success(f"saved transform.latest_report: {transform.latest_report}")
    identifier = ln_setup.settings.instance.slug
    logger.success(f"Go to: https://lamin.ai/{identifier}/transform/{transform.uid}")
    ln.core.run_context.run = run_context_run
    return None
