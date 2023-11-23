import os
import subprocess


scripts_dir = "./sub/lamin-cli/tests/scripts/"


def test_initialize():
    env = os.environ
    env["LAMIN_TESTING"] = "true"
    result = subprocess.run(
        f"lamin track {scripts_dir}/not-initialized.py",
        shell=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0

    with open(f"{scripts_dir}/not-initialized.py") as f:
        content = f.read()
    prepend = f'___uid_prefix__ = "'
    assert content.startswith(prepend)
