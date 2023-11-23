import os
import subprocess


scripts_dir = "./sub/lamin-cli/tests/scripts/"


def test_initialize():
    result = subprocess.run(
        f"lamin track {scripts_dir}not-initialized.py",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0

    with open(f"{scripts_dir}not-initialized.py") as f:
        content = f.read()
    prepend = f'__lamindb_uid_prefix__ = "'
    assert content.startswith(prepend)


def test_run():
    result = subprocess.run(
        f"python {scripts_dir}initialized.py",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert "saved: Transform" in result.stdout.decode()
