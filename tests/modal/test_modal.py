import subprocess
from pathlib import Path

scripts_dir = Path(__file__).parent.parent.resolve() / "scripts"


def test_run_on_modal():
    filepath = scripts_dir / "run-track-and-finish.py"

    subprocess.run("lamin connect laminlabs/lamindata", shell=True, check=True)
    result = subprocess.run(
        f"lamin run {filepath} --project 1QLbS6N7wwiL",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    assert result.returncode == 0
    assert "hello!" in result.stdout.decode()
    assert "finished Run" in result.stdout.decode()
