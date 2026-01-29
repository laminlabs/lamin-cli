import os
import subprocess
from pathlib import Path

import lamindb as ln

scripts_dir = Path(__file__).parent.parent.resolve() / "scripts"


def test_save_transform_wrong_branch():
    env = os.environ.copy()
    env["LAMIN_TESTING"] = "true"

    transform = ln.Transform(uid="s4bgVoaLOBjx0000", key="run-track-transform-branch.py", type="script").save()
    ln.Run(transform=transform).save()
    # now move the transform to trash
    transform.delete()

    script_path = scripts_dir / "run-track-transform-branch.py"
    script_path.write_text("import lamindb as ln\nln.track('s4bgVoaLOBjx')\nln.finish()")

    result = subprocess.run(
        f"lamin save {script_path}",
        shell=True,
        env=env,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "Transform is in the trash" in result.stderr.decode()

    script_path.unlink()
