import subprocess
from pathlib import Path

scripts_dir = Path(__file__).parent.parent.resolve() / "scripts"


def test_run_on_modal():
    filepath = scripts_dir / "run-track-and-finish.py"

    result = subprocess.run(
        f"lamin run {filepath}",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    assert result.returncode == 0
    assert "created Transform" in result.stdout.decode()
