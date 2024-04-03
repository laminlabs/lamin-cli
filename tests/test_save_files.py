import subprocess
from pathlib import Path

test_file = Path(__file__).parent.parent.resolve() / ".gitignore"


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
