"""Tests for `lamin annotate --readme` for artifact, transform, and collection.

This file is structured to allow adding tests for feature, ulabel, and other
entity types when the annotate command supports them.
"""

import subprocess
import tempfile
from pathlib import Path

import lamindb as ln

scripts_dir = Path(__file__).parent.parent.resolve() / "scripts"
test_file = Path(__file__).parent.parent.parent.resolve() / ".gitignore"


def test_annotate_artifact_with_readme():
    """Annotate an artifact with a readme file via --readme."""
    ln.Project(name="test_project_readme").save()
    branch = ln.Branch(name="contrib_readme").save()

    # Save artifact
    result = subprocess.run(
        f"lamin save {test_file} --key readme_test_artifact --project test_project_readme --branch contrib_readme",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()

    artifact = ln.Artifact.get(key="readme_test_artifact", branch=branch)
    assert artifact.ablocks.filter(kind="readme").count() == 0

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, prefix="readme_"
    ) as f:
        f.write("# My Artifact Readme\n\nThis describes the artifact.")
        readme_path = Path(f.name)

    try:
        result = subprocess.run(
            f"lamin annotate --uid {artifact.uid} --readme {readme_path}",
            shell=True,
            capture_output=True,
        )
        print(result.stdout.decode())
        print(result.stderr.decode())
        assert result.returncode == 0

        artifact.refresh_from_db()
        readme_blocks = artifact.ablocks.filter(kind="readme")
        assert readme_blocks.count() == 1
        block = readme_blocks.one()
        assert "My Artifact Readme" in block.content
        assert "describes the artifact" in block.content
    finally:
        readme_path.unlink(missing_ok=True)

    # Cleanup
    subprocess.run(
        "lamin delete artifact --key readme_test_artifact --permanent",
        shell=True,
        capture_output=True,
    )


def test_annotate_transform_with_readme():
    """Annotate a transform with a readme file via --readme."""
    subprocess.run("lamin settings dev-dir unset", shell=True, capture_output=True)

    script_path = scripts_dir / "readme_test_transform.py"
    script_path.write_text("print('hello')")

    result = subprocess.run(
        f"lamin save {script_path}",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()

    transform = ln.Transform.filter(key="readme_test_transform.py").first()
    assert transform is not None
    assert transform.ablocks.filter(kind="readme").count() == 0

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, prefix="readme_"
    ) as f:
        f.write("# Transform Documentation\n\nHow to run this script.")
        readme_path = Path(f.name)

    try:
        result = subprocess.run(
            f"lamin annotate transform --uid {transform.uid} --readme {readme_path}",
            shell=True,
            capture_output=True,
        )
        print(result.stdout.decode())
        print(result.stderr.decode())
        assert result.returncode == 0

        transform.refresh_from_db()
        readme_blocks = transform.ablocks.filter(kind="readme")
        assert readme_blocks.count() == 1
        block = readme_blocks.one()
        assert "Transform Documentation" in block.content
    finally:
        readme_path.unlink(missing_ok=True)

    # Cleanup
    script_path.unlink(missing_ok=True)
    subprocess.run(
        "lamin delete transform --key readme_test_transform.py --permanent",
        shell=True,
        capture_output=True,
    )


def test_annotate_collection_with_readme():
    """Annotate a collection with a readme file via --readme."""
    # Create artifact and collection (use main branch, no --branch)
    result = subprocess.run(
        f"lamin save {test_file} --key readme_coll_artifact --registry artifact",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()

    artifact = ln.Artifact.filter(key="readme_coll_artifact").first()
    assert artifact is not None
    collection = ln.Collection([artifact], key="readme_test_collection").save()
    assert collection.ablocks.filter(kind="readme").count() == 0

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, prefix="readme_"
    ) as f:
        f.write("# Collection Overview\n\nThis collection groups related data.")
        readme_path = Path(f.name)

    try:
        result = subprocess.run(
            f"lamin annotate collection --uid {collection.uid} --readme {readme_path}",
            shell=True,
            capture_output=True,
        )
        print(result.stdout.decode())
        print(result.stderr.decode())
        assert result.returncode == 0

        collection.refresh_from_db()
        readme_blocks = collection.ablocks.filter(kind="readme")
        assert readme_blocks.count() == 1
        block = readme_blocks.one()
        assert "Collection Overview" in block.content
    finally:
        readme_path.unlink(missing_ok=True)

    # Cleanup
    collection.delete(permanent=True)
    subprocess.run(
        "lamin delete artifact --key readme_coll_artifact --permanent",
        shell=True,
        capture_output=True,
    )


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
