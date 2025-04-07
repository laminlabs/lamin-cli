import os
import subprocess
from pathlib import Path

import lamindb as ln

scripts_dir = Path(__file__).parent.parent.resolve() / "scripts"


def test_run_save_cache():
    env = os.environ
    env["LAMIN_TESTING"] = "true"
    filepath = scripts_dir / "run-track.R"

    transform = ln.Transform(
        uid="EPnfDtJz8qbE0000", name="run-track.R", key="run-track.R", type="script"
    ).save()
    ln.Run(transform=transform).save()

    assert transform.source_code is None

    result = subprocess.run(
        f"lamin save {filepath}",
        shell=True,
        capture_output=True,
    )
    # print(result.stdout.decode())
    # print(result.stderr.decode())
    assert result.returncode == 0
    assert "on uid 'EPnfDtJz8qbE0000'" in result.stdout.decode()

    transform = ln.Transform.get("EPnfDtJz8qbE0000")
    assert transform.source_code is not None
    assert transform.type == "script"

    # now test a .qmd file (.Rmd adheres to same principles)
    filepath = scripts_dir / "run-track.qmd"

    transform = ln.Transform(
        uid="HPnfDtJz8qbE0000",
        name="run-track.qmd",
        key="run-track.qmd",
        type="notebook",
    ).save()
    ln.Run(transform=transform).save()

    assert transform.source_code is None
    assert transform.latest_run.report is None

    result = subprocess.run(
        f"lamin save {filepath}",
        shell=True,
        capture_output=True,
    )
    # print(result.stdout.decode())
    # print(result.stderr.decode())
    assert result.returncode == 1
    assert "Please export your" in result.stderr.decode()

    filepath.with_suffix(".html").write_text("dummy html")

    result = subprocess.run(
        f"lamin save {filepath}",
        shell=True,
        capture_output=True,
    )
    print(result.stdout.decode())
    print(result.stderr.decode())
    assert result.returncode == 0

    transform = ln.Transform.get("HPnfDtJz8qbE0000")
    assert transform.source_code is not None
    assert transform.latest_run.report is not None
    assert transform.type == "notebook"

    filepath.with_suffix(".html").unlink()
