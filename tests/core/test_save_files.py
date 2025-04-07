import subprocess
from pathlib import Path

import lamindb_setup as ln_setup

test_file = Path(__file__).parent.parent.parent.resolve() / ".gitignore"


def test_save_file():
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

    print(ln_setup.settings.instance.slug)

    result = subprocess.run(
        f"lamin save {filepath} --key mytest",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert "key='mytest'" in result.stdout.decode()
    assert "storage path:" in result.stdout.decode()
    assert result.returncode == 0

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
