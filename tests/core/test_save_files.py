import subprocess
from pathlib import Path

import lamindb as ln
import lamindb_setup as ln_setup

test_file = Path(__file__).parent.parent.parent.resolve() / ".gitignore"


def test_save_local_file():
    filepath = test_file

    # neither key nor description
    result = subprocess.run(
        f"lamin save {filepath}",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert (
        "Please pass a key or description via --key or --description"
        in result.stdout.decode()
    )
    assert result.returncode == 1

    project = ln.Project(name="test_project").save()
    # cannot define Space with regular user, is defined in lamindb/tests/permissions
    branch = ln.Branch(name="contrib1").save()

    result = subprocess.run(
        f"lamin save {filepath} --key mytest --project test_project --branch contrib1",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert "key='mytest'" in result.stdout.decode()
    assert "storage path:" in result.stdout.decode()
    assert "labeled with project: test_project" in result.stdout.decode()
    assert result.returncode == 0

    artifact = ln.Artifact.get(key="mytest")
    assert artifact.branch == branch
    assert project in artifact.projects.all()

    # test passing the registry and saving the same file
    result = subprocess.run(
        f"lamin save {filepath} --key mytest --registry artifact",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert "returning existing artifact with same hash" in result.stdout.decode()
    assert "key='mytest'" in result.stdout.decode()
    assert "storage path:" in result.stdout.decode()
    assert result.returncode == 0

    # test invalid registry param
    result = subprocess.run(
        f"lamin save {filepath} --key mytest --registry invalid",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert (
        "Allowed values for '--registry' are: 'artifact', 'transform'"
        in result.stderr.decode()
    )
    assert result.returncode == 1


def test_save_cloud_file():
    # should be no key for cloud paths
    result = subprocess.run(
        "lamin save s3://cellxgene-data-public/cell-census/2024-07-01/h5ads/fe1a73ab-a203-45fd-84e9-0f7fd19efcbd.h5ad --key wrongkey.h5ad",
        shell=True,
        check=False,
    )
    assert result.returncode == 1

    result = subprocess.run(
        "lamin save s3://cellxgene-data-public/cell-census/2024-07-01/h5ads/fe1a73ab-a203-45fd-84e9-0f7fd19efcbd.h5ad",
        shell=True,
        check=True,
    )
