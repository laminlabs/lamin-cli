from pathlib import Path
import subprocess
import os
import lamindb as ln

scripts_dir = Path(__file__).parent.resolve() / "scripts"


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
    assert result.returncode == 0
    assert "on uid 'EPnfDtJz8qbE0000'" in result.stdout.decode()

    transform = ln.Transform.get("EPnfDtJz8qbE0000")
    assert transform.source_code is not None
