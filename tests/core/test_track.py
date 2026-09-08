import os
import subprocess
from pathlib import Path

import lamindb as ln
from click.testing import CliRunner
from lamin_cli.__main__ import main

scripts_dir = Path(__file__).parent.parent.resolve() / "scripts"


def test_track_lineage_via_cli():
    """Test that lineage tracking works correctly via CLI commands."""
    env = os.environ.copy()
    env["LAMIN_TESTING"] = "true"

    script_path = scripts_dir / "track-lineage.sh"

    # Make the script executable
    script_path.chmod(0o755)

    # Run the shell script
    result = subprocess.run(
        ["sh", str(script_path)],
        env=env,
        capture_output=True,
        text=True,
    )

    # Check that the script ran successfully
    if result.returncode != 0:
        print(f"Script stdout: {result.stdout}")
        print(f"Script stderr: {result.stderr}")
    assert result.returncode == 0, (
        f"Script failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    transform = ln.Transform.get(key="track-lineage.sh")
    assert transform.kind == "script"
    run = transform.latest_run

    input_artifacts = run.input_artifacts.all()
    assert len(input_artifacts) == 1, (
        f"Expected 1 input artifact, got {len(input_artifacts)}"
    )
    input_artifact = input_artifacts[0]
    assert input_artifact.key == "test/input.txt", (
        f"Expected input artifact key 'test/input.txt', got '{input_artifact.key}'"
    )

    output_artifacts = run.output_artifacts.all()
    assert len(output_artifacts) == 1, (
        f"Expected 1 output artifact, got {len(output_artifacts)}"
    )
    output_artifact = output_artifacts[0]
    assert output_artifact.key == "test/output.txt", (
        f"Expected output artifact key 'test/output.txt', got '{output_artifact.key}'"
    )

    assert run.finished_at is not None, "Run should be finished"
    assert run._status_code == 0, "Run status code should be 0 (finished)"

    # Clean up
    input_artifact.delete(permanent=True)
    output_artifact.delete(permanent=True)
    run.delete(permanent=True)
    transform.delete(permanent=True)


def test_finish_canonical_and_track_finish_hidden_from_help() -> None:
    root_help = CliRunner().invoke(main, ["--help"])
    assert root_help.exit_code == 0
    assert "finish" in root_help.output
    assert "track finish" not in root_help.output

    track_help = CliRunner().invoke(main, ["track", "--help"])
    assert track_help.exit_code == 0
    assert "track finish" not in track_help.output
