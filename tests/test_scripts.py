from pathlib import Path
import subprocess
import os


scripts_dir = Path(__file__).parent.resolve() / "scripts"

print(scripts_dir)

def test_initialize():
    filepath = scripts_dir / "not-initialized.py"
    print(scripts_dir)
    result = subprocess.run(
        f"lamin track {str(filepath)}",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0

    with open(filepath) as f:
        content = f.read()
    prepend = f'__transform_stem_uid__ = "'
    assert content.startswith(prepend)


def test_run_and_save():
    env = os.environ
    env["LAMIN_TESTING"] = "true"
    
    filepath = scripts_dir / "initialized.py"
    # python sub/lamin-cli/tests/scripts/initialized.py     
    result = subprocess.run(
        f"python {str(filepath)}",
        shell=True,
        capture_output=True,
    )
    print(result)
    assert result.returncode == 0
    assert "saved: Transform" in result.stdout.decode()

    # save the script
    # lamin save sub/lamin-cli/tests/scripts/initialized.py
    result = subprocess.run(
        f"lamin save {str(filepath)}",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert "saved transform" in result.stdout.decode()
    assert filepath.exists()  # test that it's not cleaned out!

    # python sub/lamin-cli/tests/scripts/initialized.py
    # now, trying to run the same thing again will error
    result = subprocess.run(
        f"python {str(filepath)}",
        shell=True,
        capture_output=True,
        env=env,
    )
    print(result)
    assert result.returncode == 1
    assert "You can now rerun the script." in result.stderr.decode()

    result = subprocess.run(
        f"python {str(filepath)}",
        shell=True,
        capture_output=True,
    )
    print(result)
    assert result.returncode == 0
