from pathlib import Path
import subprocess
import os


scripts_dir = Path(__file__).parent.resolve() / "scripts"


def test_run_save_stage():
    env = os.environ
    env["LAMIN_TESTING"] = "true"

    filepath = scripts_dir / "initialized.py"
    # attempt to save the script without it yet being run
    # lamin save sub/lamin-cli/tests/scripts/initialized.py
    result = subprocess.run(
        f"lamin save {filepath}",
        shell=True,
        capture_output=True,
    )
    print(result.stderr.decode())
    assert result.returncode == 1
    assert "Did you run ln.track()" in result.stdout.decode()

    # python sub/lamin-cli/tests/scripts/initialized.py
    result = subprocess.run(
        f"python {filepath}",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert result.returncode == 0
    assert "saved: Transform" in result.stdout.decode()
    assert "saved: Run" in result.stdout.decode()

    # python sub/lamin-cli/tests/scripts/initialized.py
    # you can rerun the same script
    result = subprocess.run(
        f"python {filepath}",
        shell=True,
        capture_output=True,
        env=env,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert result.returncode == 0

    result = subprocess.run(
        "lamin stage 'transform m5uCHTTpJnjQ5zKv'",
        shell=True,
        capture_output=True,
    )
    print(result.stderr.decode())
    assert result.returncode == 0
