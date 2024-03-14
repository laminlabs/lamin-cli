import os
import subprocess
from pathlib import Path
import nbproject_test
import pytest
from nbclient.exceptions import CellExecutionError

notebook_dir = "./sub/lamin-cli/tests/notebooks/"


def test_save_not_initialized():
    env = os.environ
    env["LAMIN_TESTING"] = "true"
    result = subprocess.run(
        f"lamin save {notebook_dir}not-initialized.ipynb",
        shell=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 1
    assert (
        "Call ln.track() and copy/paste the output into the notebook"
        in result.stderr.decode()
    )


def test_save_non_consecutive():
    env = os.environ
    env["LAMIN_TESTING"] = "true"
    result = subprocess.run(
        f"lamin save {notebook_dir}with-title-and-initialized-non-consecutive.ipynb",  # noqa
        shell=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 1
    assert "Aborted (non-consecutive)!" in result.stdout.decode()


def test_save_consecutive():
    notebook_path = Path(
        f"{notebook_dir}with-title-and-initialized-consecutive.ipynb"
    ).resolve()
    env = os.environ
    env["LAMIN_TESTING"] = "true"

    # let's inspect what got written to the database
    import lamindb as ln

    ln.connect("lamindb-unit-tests")

    transform = ln.Transform.filter(uid="hlsFXswrJjtt5zKv").one_or_none()
    assert transform is None

    # let's try to save a notebook for which `ln.track()` was never run
    result = subprocess.run(
        f"lamin save {notebook_path}",
        shell=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 1
    assert "Did not find stem uid 'hlsFXswrJjtt'" in result.stdout.decode()

    # now, let's re-run this notebook so that ln.track() is actually run
    nbproject_test.execute_notebooks(notebook_path, print_outputs=True)

    # now, there is a transform record, but we're missing all artifacts
    transform = ln.Transform.filter(uid="hlsFXswrJjtt5zKv").one_or_none()
    assert transform is not None
    assert transform.latest_report is None
    assert transform.source_code is None
    assert transform.latest_run.environment is None

    # and save again
    result = subprocess.run(
        f"lamin save {notebook_path}",
        shell=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0
    assert "saved transform" in result.stdout.decode()

    # now, we have the associated artifacts
    transform = ln.Transform.filter(uid="hlsFXswrJjtt5zKv").one_or_none()
    assert transform is not None
    assert transform.latest_report.path.exists()
    assert transform.latest_run.report.path == transform.latest_report.path
    assert transform.source_code.hash == "bH9mTpWerQcoI0UcDIkKSw"
    assert transform.latest_run.environment.path.exists()
    assert transform.source_code.path.exists()

    # now, assume the user modifies the notebook and saves
    # it without changing stem uid or version
    # outside of tests, this triggers a dialogue
    # within tests, it automatically overwrites the source
    from nbproject.dev import read_notebook, write_notebook

    nb = read_notebook(notebook_path)
    # simulate editing the notebook (here, duplicate last cell)
    new_cell = nb.cells[-1].copy()
    new_cell["execution_count"] += 1
    nb.cells.append(new_cell)
    write_notebook(nb, notebook_path)
    result = subprocess.run(
        f"lamin save {notebook_path}",
        shell=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0
    assert "saved transform" in result.stdout.decode()

    # now, the source code should be overwritten
    transform = ln.Transform.filter(uid="hlsFXswrJjtt5zKv").one_or_none()
    assert transform is not None
    assert transform.latest_report.path.exists()
    assert transform.latest_run.report.path == transform.latest_report.path
    assert transform.source_code.hash == "WYIScjWm7_jfrcVqrK4WFw"
    assert transform.latest_run.environment.path.exists()
    assert transform.source_code.path.exists()

    # now, assume the user renames the notebook
    new_path = notebook_path.with_name("new_name.ipynb")
    os.system(f"cp {notebook_path} {new_path}")

    # upon re-running it, the user is asked whether it's still the same notebook
    with pytest.raises(CellExecutionError) as error:
        nbproject_test.execute_notebooks(new_path, print_outputs=True)

    assert "Please update your transform settings as follows" in error.exconly()
