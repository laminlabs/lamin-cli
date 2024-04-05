from pathlib import Path
import subprocess

scripts_dir = Path(__file__).parent.resolve() / "scripts"


def test_run_script_in_parallel():
    filepath = scripts_dir / "initialized.py"
    result = subprocess.run(
        f"python {filepath}",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
