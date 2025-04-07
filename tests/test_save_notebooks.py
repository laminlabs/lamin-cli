import json
import os
import subprocess
from pathlib import Path

import lamindb as ln
import nbproject_test
import pytest
from nbclient.exceptions import CellExecutionError
from nbproject.dev import read_notebook, write_notebook

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
    assert result.returncode == 0


def test_save_non_consecutive():
    env = os.environ
    env["LAMIN_TESTING"] = "true"
    notebook_path = Path(
        f"{notebook_dir}with-title-and-initialized-non-consecutive.ipynb"
    ).resolve()

    # here, we're mimicking a non-consecutive run
    transform = ln.Transform(
        uid="HDMGkxN9rgFA0000",
        version="1",
        name="My test notebook (non-consecutive)",
        type="notebook",
    ).save()
    ln.Run(transform=transform).save()

    process = subprocess.Popen(
        f"lamin save {notebook_path}",
        shell=True,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate("n")
    assert "were not run consecutively" in stdout
    assert process.returncode == 0


def test_save_consecutive():
    notebook_path = Path(
        f"{notebook_dir}with-title-and-initialized-consecutive.ipynb"
    ).resolve()
    env = os.environ
    env["LAMIN_TESTING"] = "true"

    assert not Path("./with-title-and-initialized-consecutive.ipynb").exists()

    transform = ln.Transform.filter(uid="hlsFXswrJjtt0000").one_or_none()
    assert transform is None

    # let's try to save a notebook for which `ln.track()` was never run
    result = subprocess.run(
        f"lamin save {notebook_path}",
        shell=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 1
    assert "Did not find uid 'hlsFXswrJjtt0000'" in result.stdout.decode()

    # now, let's re-run this notebook so that `ln.track()` is actually run
    nbproject_test.execute_notebooks(notebook_path, print_outputs=True)

    # now, there is a transform record, but we're missing all artifacts
    transform = ln.Transform.filter(uid="hlsFXswrJjtt0000").one_or_none()
    assert transform is not None
    assert transform.latest_run.report is None
    assert transform.source_code is None
    assert transform.latest_run.environment is None

    # and save again
    result = subprocess.run(
        f"lamin save {notebook_path}",
        shell=True,
        capture_output=True,
        env=env,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert result.returncode == 0

    # now, we have the associated artifacts
    transform = ln.Transform.filter(uid="hlsFXswrJjtt0000").one_or_none()
    assert transform is not None
    assert (
        transform.source_code
        == """# %% [markdown]
#

# %%
import lamindb as ln

# %%
ln.track("hlsFXswrJjtt0000")

# %%
print("my consecutive cell")
"""
    )
    assert transform.hash == "ik5Dilxs2RmwOGydohFolQ"
    # below is the test that we can use if store the run repot as `.ipynb`
    # and not as html as we do right now
    assert transform.latest_run.report.suffix == ".html"
    # with open(transform.latest_run.report.path, "r") as f:
    #     json_notebook = json.load(f)
    # # test that title is stripped from notebook
    # assert json_notebook["cells"][0] == {
    #     "cell_type": "markdown",
    #     "metadata": {},
    #     "source": [],
    # }
    # testing for the hash of the report makes no sense because it contains timestamps
    assert transform.latest_run.environment.path.exists()

    # edit the notebook
    nb = read_notebook(notebook_path)
    new_cell = nb.cells[-1].copy()
    new_cell["execution_count"] += 1
    nb.cells.append(new_cell)  # duplicate last cell
    write_notebook(nb, notebook_path)

    # attempt re-running - it fails
    with pytest.raises(CellExecutionError) as error:
        nbproject_test.execute_notebooks(notebook_path, print_outputs=True)
    # print(error.exconly())
    assert "UpdateContext" in error.exconly()

    # attempt re-saving - it works but the user needs to confirm overwriting
    # source code and run report
    process = subprocess.Popen(
        f"lamin save {notebook_path}",
        shell=True,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate("y\ny")
    assert "You are about to overwrite existing source code" in stdout
    assert "You are about to overwrite an existing report" in stdout
    assert process.returncode == 0
    # the source code is overwritten with the edits, reflected in a new hash
    transform = ln.Transform.get("hlsFXswrJjtt0000")
    assert transform.latest_run.report.path.exists()
    assert transform.latest_run.report.path == transform.latest_run.report.path
    assert transform.hash == "Jv0_TrZfzM-0erbp1FGdrQ"
    assert transform.latest_run.environment.path.exists()

    # get the the source code via command line
    result = subprocess.run(
        "yes | lamin load"
        f" https://lamin.ai/{ln.setup.settings.user.handle}/laminci-unit-tests/transform/hlsFXswrJjtt0000",
        shell=True,
        capture_output=True,
    )
    # print(result.stderr.decode())
    assert Path("./with-title-and-initialized-consecutive.ipynb").exists()
    with open("./with-title-and-initialized-consecutive.ipynb") as f:
        json_notebook = json.load(f)
    print(json_notebook["cells"][0])
    assert json_notebook["cells"][0]["source"] == ["# My test notebook (consecutive)"]
    assert result.returncode == 0

    # now, assume the user renames the notebook
    new_path = notebook_path.with_name("new_name.ipynb")
    os.system(f"cp {notebook_path} {new_path}")

    # upon re-running it, the notebook name is updated
    nbproject_test.execute_notebooks(new_path, print_outputs=True)
    transform = ln.Transform.get("hlsFXswrJjtt0001")
    assert "new_name.ipynb" in transform.key
