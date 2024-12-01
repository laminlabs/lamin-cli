from pathlib import Path
import subprocess
import os
import lamindb as ln
from lamindb_setup import settings


scripts_dir = Path(__file__).parent.resolve() / "scripts"


def test_run_save_cache():
    env = os.environ
    env["LAMIN_TESTING"] = "true"
    filepath = scripts_dir / "run-track-and-finish-sync-git.py"

    # attempt to save the script without it yet being run
    result = subprocess.run(
        f"lamin save {filepath}",
        shell=True,
        capture_output=True,
    )
    # print(result.stdout.decode())
    assert result.returncode == 1
    assert "Did you run `ln.track()`?" in result.stdout.decode()

    # run the script
    result = subprocess.run(
        f"python {filepath}",
        shell=True,
        capture_output=True,
    )
    # print(result.stdout.decode())
    # print(result.stderr.decode())
    assert result.returncode == 0
    assert "created Transform" in result.stdout.decode()
    assert "m5uCHTTp" in result.stdout.decode()
    assert "started new Run" in result.stdout.decode()

    transform = ln.Transform.get("m5uCHTTpJnjQ")
    assert transform.hash == "MoIciBQ0lpVPCKQGofPX6g"
    assert transform.latest_run.environment.path.exists()
    assert transform._source_code_artifact is None

    # you can rerun the same script
    result = subprocess.run(
        f"python {filepath}",
        shell=True,
        capture_output=True,
        env=env,
    )
    # print(result.stdout.decode())
    # print(result.stderr.decode())
    assert result.returncode == 0
    assert "loaded Transform" in result.stdout.decode()
    assert "m5uCHTTp" in result.stdout.decode()
    assert "started Run" in result.stdout.decode()

    # you can re-save the script
    result = subprocess.run(
        f"lamin save {filepath}",
        shell=True,
        capture_output=True,
        env=env,
    )
    # print(result.stdout.decode())
    # print(result.stderr.decode())
    assert result.returncode == 0
    assert "run.environment is already saved" in result.stdout.decode()

    # edit the script
    content = filepath.read_text() + "\n # edited"
    filepath.write_text(content)

    # re-run the script without committing
    result = subprocess.run(
        f"python {filepath}",
        shell=True,
        capture_output=True,
        env=env,
    )
    # print(result.stdout.decode())
    # print(result.stderr.decode())
    assert result.returncode == 1
    assert "Did not find blob hash" in result.stderr.decode()

    # edit the script to remove the git integration & ln.finish
    content = filepath.read_text()
    content_lines = content.split("\n")
    content_lines.remove(
        'ln.settings.sync_git_repo = "https://github.com/laminlabs/lamin-cli"'
    )
    content_lines.remove("    ln.finish()")
    content = "\n".join(content_lines)
    filepath.write_text(content)

    # re-run the script
    result = subprocess.run(
        f"python {filepath}",
        shell=True,
        capture_output=True,
        env=env,
    )
    # print(result.stdout.decode())
    # print(result.stderr.decode())
    assert result.returncode == 1
    assert "Source code changed, bump revision by setting" in result.stderr.decode()

    # update the uid
    content = filepath.read_text()
    filepath.write_text(content.replace("m5uCHTTpJnjQ0000", "m5uCHTTpJnjQ0001"))

    # re-run the script that lacks ln.finish(), hence doesn't yet save source code
    result = subprocess.run(
        f"python {filepath}",
        shell=True,
        capture_output=True,
        env=env,
    )
    # print(result.stdout.decode())
    # print(result.stderr.decode())
    assert result.returncode == 0
    assert "created Transform(" in result.stdout.decode()
    assert "started new Run(" in result.stdout.decode()

    # login a different user
    assert ln.setup.settings.user.handle != "testuser2"
    result = subprocess.run(
        "lamin login testuser2",
        shell=True,
        capture_output=True,
        env=env,
    )
    # re-run the script through the second user
    result = subprocess.run(
        f"python {filepath}",
        shell=True,
        capture_output=True,
        env=env,
    )
    # print(result.stdout.decode())
    # print(result.stderr.decode())
    assert result.returncode == 1
    assert "already works on this draft" in result.stderr.decode()

    # try to get the the source code via command line
    result = subprocess.run(
        "yes | lamin load"
        f" https://lamin.ai/{settings.user.handle}/laminci-unit-tests/transform/m5uCHTTpJnjQ0000",  # noqa
        shell=True,
        capture_output=True,
    )
    print(result.stderr.decode())
    assert result.returncode == 0

    result = subprocess.run(
        f"yes | lamin load transform --key {filepath.name}",  # noqa
        shell=True,
        capture_output=True,
    )
    print(result.stderr.decode())
    assert result.returncode == 0

    result = subprocess.run(
        f"yes | lamin load transform --uid m5uCHTTpJnjQ0000",  # noqa
        shell=True,
        capture_output=True,
    )
    print(result.stderr.decode())
    assert result.returncode == 0


def test_run_save_with_params():
    env = os.environ
    env["LAMIN_TESTING"] = "true"
    filepath = scripts_dir / "run-track-with-params.py"

    # define params
    ln.Param(name="dataset_key", dtype="str").save()
    ln.Param(name="learning_rate", dtype="float").save()
    ln.Param(name="downsample", dtype="bool").save()

    # run the script
    result = subprocess.run(
        f"python {filepath} --dataset-key mydata --learning-rate 0.01 --downsample",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert result.returncode == 0
    assert "created Transform" in result.stdout.decode()
    assert "JjRF4mAC" in result.stdout.decode()
    assert "started new Run" in result.stdout.decode()

    # you can re-save the script
    result = subprocess.run(
        f"lamin save {filepath}",
        shell=True,
        capture_output=True,
        env=env,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert result.returncode == 0
    assert (
        "run-track-with-params.py' on uid 'JjRF4mACd9m00000'" in result.stdout.decode()
    )
