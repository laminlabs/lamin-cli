import subprocess
from pathlib import Path

test_file = Path(__file__).parent.parent.resolve() / ".gitignore"


def test_save_file():
    filepath = test_file
    result = subprocess.run(
        f"lamin save {filepath}",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert result.returncode == 1
    assert "storage path:" in result.stdout.decode()
