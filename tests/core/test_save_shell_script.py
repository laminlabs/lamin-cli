import os
import subprocess
from pathlib import Path

import lamindb as ln

scripts_dir = Path(__file__).parent.parent.resolve() / "scripts"


def test_save_shell_script():
    """Test that saving a shell script creates a transform with source_code and type='script'."""
    env = os.environ.copy()
    env["LAMIN_TESTING"] = "true"

    script_path = scripts_dir / "test-shell-script.sh"
    script_content = "#!/bin/bash\necho 'test script'\n"
    script_path.write_text(script_content)

    result = subprocess.run(
        f"lamin save {script_path}",
        shell=True,
        env=env,
        capture_output=True,
    )
    assert result.returncode == 0
    assert "created Transform" in result.stdout.decode()

    transform = ln.Transform.get(key=script_path.name)

    assert transform.source_code is not None, (
        "Transform source_code should be populated"
    )
    assert len(transform.source_code) > 0, "Transform source_code should not be empty"
    assert transform.type == "script", (
        f"Transform type should be 'script', got '{transform.type}'"
    )

    script_path.unlink()
