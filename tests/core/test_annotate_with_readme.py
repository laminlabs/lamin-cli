"""Tests for `lamin annotate --readme`."""

import subprocess
import tempfile
from pathlib import Path

import lamindb as ln
import pytest

test_file = Path(__file__).parent.parent.parent.resolve() / ".gitignore"


@pytest.mark.parametrize(
    "registry,create_entity,delete_cmd",
    [
        (
            "artifact",
            lambda: ln.Artifact(test_file, key="readme_artifact").save(),
            "lamin delete artifact --key readme_artifact --permanent",
        ),
        (
            "transform",
            lambda: ln.Transform(key="readme_transform").save(),
            "lamin delete transform --key readme_transform --permanent",
        ),
        (
            "collection",
            lambda: ln.Collection(
                ln.Artifact(test_file, key="readme_artifact").save(),
                key="readme_collection",
            ).save(),
            "lamin delete collection --key readme_collection --permanent",
        ),
    ],
)
def test_annotate_with_readme(registry, create_entity, delete_cmd):
    """Create entity, annotate with readme via CLI, delete entity."""
    obj = create_entity()
    assert obj.ablocks.filter(kind="readme").count() == 0

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, prefix="readme_"
    ) as f:
        f.write("# Test Readme\n\nContent for readme block.")
        readme_path = Path(f.name)

    try:
        result = subprocess.run(
            f"lamin annotate {registry} --uid {obj.uid} --readme {readme_path}",
            shell=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr.decode()

        obj.refresh_from_db()
        blocks = obj.ablocks.filter(kind="readme")
        assert blocks.count() == 1
        assert "Test Readme" in blocks.one().content
    finally:
        readme_path.unlink(missing_ok=True)
        subprocess.run(delete_cmd, shell=True, capture_output=True)
        if registry == "collection":
            subprocess.run(
                "lamin delete artifact --key readme_artifact --permanent",
                shell=True,
                capture_output=True,
            )
