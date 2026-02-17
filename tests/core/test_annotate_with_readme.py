"""Tests for `lamin annotate --readme` for artifact, transform, and collection.

This file is structured to allow adding tests for feature, ulabel, and other
entity types when the annotate command supports them.
"""

import subprocess
import tempfile
from pathlib import Path

import lamindb as ln
import pytest

scripts_dir = Path(__file__).parent.parent.resolve() / "scripts"
test_file = Path(__file__).parent.parent.parent.resolve() / ".gitignore"


def _setup_artifact():
    """Create artifact, return (obj, readme_content, cleanup_func)."""
    ln.Project(name="test_project_readme").save()
    branch = ln.Branch(name="contrib_readme").save()
    result = subprocess.run(
        f"lamin save {test_file} --key readme_test_artifact --project test_project_readme --branch contrib_readme",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()
    obj = ln.Artifact.get(key="readme_test_artifact", branch=branch)

    def cleanup():
        subprocess.run(
            "lamin delete artifact --key readme_test_artifact --permanent",
            shell=True,
            capture_output=True,
        )

    return obj, "# My Artifact Readme\n\nThis describes the artifact.", cleanup


def _setup_transform():
    """Create transform, return (obj, readme_content, cleanup_func)."""
    subprocess.run("lamin settings dev-dir unset", shell=True, capture_output=True)
    script_path = scripts_dir / "readme_test_transform.py"
    script_path.write_text("print('hello')")
    result = subprocess.run(
        f"lamin save {script_path}",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()
    obj = ln.Transform.filter(key="readme_test_transform.py").first()
    assert obj is not None

    def cleanup():
        script_path.unlink(missing_ok=True)
        subprocess.run(
            "lamin delete transform --key readme_test_transform.py --permanent",
            shell=True,
            capture_output=True,
        )

    return obj, "# Transform Documentation\n\nHow to run this script.", cleanup


def _setup_collection():
    """Create collection, return (obj, readme_content, cleanup_func)."""
    result = subprocess.run(
        f"lamin save {test_file} --key readme_coll_artifact --registry artifact",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()
    artifact = ln.Artifact.filter(key="readme_coll_artifact").first()
    assert artifact is not None
    obj = ln.Collection([artifact], key="readme_test_collection").save()

    def cleanup():
        obj.delete(permanent=True)
        subprocess.run(
            "lamin delete artifact --key readme_coll_artifact --permanent",
            shell=True,
            capture_output=True,
        )

    return obj, "# Collection Overview\n\nThis collection groups related data.", cleanup


@pytest.mark.parametrize(
    "registry,setup_fn,expected_in_content",
    [
        ("artifact", _setup_artifact, "My Artifact Readme"),
        ("transform", _setup_transform, "Transform Documentation"),
        ("collection", _setup_collection, "Collection Overview"),
    ],
)
def test_annotate_with_readme_parametrized(registry, setup_fn, expected_in_content):
    """Parametrized test: annotate artifact, transform, or collection with a readme."""
    obj, readme_content, cleanup = setup_fn()
    assert obj.ablocks.filter(kind="readme").count() == 0

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, prefix="readme_"
    ) as f:
        f.write(readme_content)
        readme_path = Path(f.name)

    try:
        result = subprocess.run(
            f"lamin annotate {registry} --uid {obj.uid} --readme {readme_path}",
            shell=True,
            capture_output=True,
        )
        print(result.stdout.decode())
        print(result.stderr.decode())
        assert result.returncode == 0

        obj.refresh_from_db()
        readme_blocks = obj.ablocks.filter(kind="readme")
        assert readme_blocks.count() == 1
        block = readme_blocks.one()
        assert expected_in_content in block.content
    finally:
        readme_path.unlink(missing_ok=True)
        cleanup()


def test_annotate_rejects_feature_and_ulabel():
    """Annotate does not yet support feature, ulabel, and other entity types."""
    ln.Feature(name="readme_test_feature", dtype="str").save()
    ln.ULabel(name="readme_test_ulabel").save()

    feature = ln.Feature.get(name="readme_test_feature")
    ulabel = ln.ULabel.get(name="readme_test_ulabel")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, prefix="readme_"
    ) as f:
        f.write("# Test readme")
        readme_path = Path(f.name)

    try:
        # feature is not supported
        result = subprocess.run(
            f"lamin annotate feature --uid {feature.uid} --readme {readme_path}",
            shell=True,
            capture_output=True,
        )
        assert result.returncode != 0
        err = (result.stderr or b"").decode() + (result.stdout or b"").decode()
        assert (
            "artifact" in err.lower()
            or "transform" in err.lower()
            or "collection" in err.lower()
        )

        # ulabel is not supported
        result = subprocess.run(
            f"lamin annotate ulabel --uid {ulabel.uid} --readme {readme_path}",
            shell=True,
            capture_output=True,
        )
        assert result.returncode != 0
    finally:
        readme_path.unlink(missing_ok=True)
