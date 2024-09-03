import os
import subprocess
from pathlib import Path
import nbproject_test
import pytest
from nbproject.dev import read_notebook, write_notebook
from nbclient.exceptions import CellExecutionError
import json
import lamindb as ln

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
        "Call ln.context.track() and copy/paste the output into the notebook"
        in result.stderr.decode()
    )


def test_save_non_consecutive():
    env = os.environ
    env["LAMIN_TESTING"] = "true"
    notebook_path = Path(
        f"{notebook_dir}with-title-and-initialized-non-consecutive.ipynb"
    ).resolve()

    # here, we're mimicking a non-consecutive run
    transform = ln.Transform(
        uid="HDMGkxN9rgFA",
        version="1",
        name="My test notebook (non-consecutive)",
        type="notebook",
    )
    transform.save()
    run = ln.Run(transform=transform)
    run.save()
    result = subprocess.run(
        f"lamin save {notebook_path}",  # noqa
        shell=True,
        capture_output=True,
        env=env,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert result.returncode == 1
    assert "were not run consecutively" in result.stdout.decode()


def test_save_consecutive():
    notebook_path = Path(
        f"{notebook_dir}with-title-and-initialized-consecutive.ipynb"
    ).resolve()
    env = os.environ
    env["LAMIN_TESTING"] = "true"

    assert not Path("./with-title-and-initialized-consecutive.ipynb").exists()

    transform = ln.Transform.filter(uid="hlsFXswrJjtt0000").one_or_none()
    assert transform is None

    # let's try to save a notebook for which `ln.context.track()` was never run
    result = subprocess.run(
        f"lamin save {notebook_path}",
        shell=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 1
    assert "Did not find uid 'hlsFXswrJjtt0000'" in result.stdout.decode()

    # now, let's re-run this notebook so that ln.context.track() is actually run
    nbproject_test.execute_notebooks(notebook_path, print_outputs=True)

    # now, there is a transform record, but we're missing all artifacts
    transform = ln.Transform.filter(uid="hlsFXswrJjtt0000").one_or_none()
    assert transform is not None
    assert transform.latest_run.report is None
    assert transform._source_code_artifact is None
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
# # transform.name

# %%
import lamindb as ln

# %%
ln.context.uid = "hlsFXswrJjtt0000"
ln.context.track()

# %%
print("my consecutive cell")
"""
    )
    assert transform.hash == "fHpHnC_pScmOl3ZR8x5cTQ"
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
    assert transform._source_code_artifact is None

    # now, assume the user modifies the notebook
    nb = read_notebook(notebook_path)
    # simulate editing the notebook (here, duplicate last cell)
    new_cell = nb.cells[-1].copy()
    new_cell["execution_count"] += 1
    nb.cells.append(new_cell)
    write_notebook(nb, notebook_path)

    # try re-running - it fails
    with pytest.raises(CellExecutionError) as error:
        nbproject_test.execute_notebooks(notebook_path, print_outputs=True)
    # print(error.exconly())
    assert "UpdateContext" in error.exconly()

    # try re-saving - it works but will issue an interactive warning dialogue
    # that clarifies that the user is about to re-save the notebook
    result = subprocess.run(
        f"lamin save {notebook_path}",
        shell=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0
    # the source code is overwritten with the edits, reflected in a new hash
    transform = ln.Transform.get("hlsFXswrJjtt0000")
    assert transform.latest_run.report.path.exists()
    assert transform.latest_run.report.path == transform.latest_run.report.path
    assert transform.hash == "b63gPnGqNWBE0G4G3pOghw"
    assert transform.latest_run.environment.path.exists()
    assert transform._source_code_artifact is None

    # get the the source code via command line
    result = subprocess.run(
        "lamin get"
        f" https://lamin.ai/{ln.setup.settings.user.handle}/laminci-unit-tests/transform/hlsFXswrJjtt0000",  # noqa
        shell=True,
        capture_output=True,
    )
    # print(result.stderr.decode())
    assert Path("./with-title-and-initialized-consecutive.ipynb").exists()
    with open("./with-title-and-initialized-consecutive.ipynb", "r") as f:
        json_notebook = json.load(f)
    print(json_notebook["cells"][0])
    assert json_notebook["cells"][0]["source"] == ["# My test notebook (consecutive)"]
    assert result.returncode == 0

    # now, assume the user renames the notebook
    new_path = notebook_path.with_name("new_name.ipynb")
    os.system(f"cp {notebook_path} {new_path}")

    # upon re-running it, the user is asked to create a new stem uid
    with pytest.raises(CellExecutionError) as error:
        nbproject_test.execute_notebooks(new_path, print_outputs=True)

    print(error.exconly())
    assert "Notebook filename changed." in error.exconly()
