"""Tests for `lamin annotate --readme` and `lamin annotate --comment`."""

import subprocess
import tempfile
from pathlib import Path

import lamindb as ln
import pytest

test_file = Path(__file__).parent.parent.parent.resolve() / ".gitignore"


def _create_artifact():
    return ln.Artifact(test_file, key="readme_artifact").save()


def _create_collection():
    artifact = ln.Artifact(test_file, key="readme_artifact").save()
    collection = ln.Collection(artifact, key="readme_collection").save()
    return collection, artifact


@pytest.mark.parametrize(
    "registry,create_entity,annotate_args",
    [
        ("artifact", _create_artifact, lambda obj: f"--uid {obj.uid}"),
        (
            "transform",
            lambda: ln.Transform(key="readme_transform").save(),
            lambda obj: f"--uid {obj.uid}",
        ),
        ("collection", _create_collection, lambda obj: f"--uid {obj.uid}"),
        (
            "branch",
            lambda: ln.Branch(name="readme_test_branch").save(),
            lambda obj: f"--name {obj.name}",
        ),
        (
            "feature",
            lambda: ln.Feature(name="readme_test_feature", dtype="str").save(),
            lambda obj: f"--name {obj.name}",
        ),
        (
            "schema",
            lambda: ln.Schema(name="readme_test_schema", itype=ln.Feature).save(),
            lambda obj: f"--name {obj.name}",
        ),
    ],
)
def test_annotate_with_readme(registry, create_entity, annotate_args):
    """Create entity, annotate with readme via CLI, delete entity."""
    result = create_entity()
    if isinstance(result, tuple):
        obj, *extra = result
        to_delete = [obj] + extra
    else:
        obj = result
        to_delete = [obj]

    assert obj.ablocks.filter(kind="readme").count() == 0

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, prefix="readme_"
    ) as f:
        f.write("# Test Readme\n\nContent for readme block.")
        readme_path = Path(f.name)

    try:
        result = subprocess.run(
            f"lamin annotate {registry} {annotate_args(obj)} --readme {readme_path}",
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
        for x in to_delete:
            x.delete(permanent=True)


@pytest.mark.parametrize(
    "registry,create_entity,annotate_args",
    [
        ("artifact", _create_artifact, lambda obj: f"--uid {obj.uid}"),
        (
            "transform",
            lambda: ln.Transform(key="comment_transform").save(),
            lambda obj: f"--uid {obj.uid}",
        ),
    ],
)
def test_annotate_with_comment(registry, create_entity, annotate_args):
    """Create entity, annotate with comment string via CLI, delete entity."""
    obj = create_entity()
    to_delete = [obj]

    assert obj.ablocks.filter(kind="comment").count() == 0

    result = subprocess.run(
        [
            "lamin",
            "annotate",
            registry,
            *annotate_args(obj).split(),
            "--comment",
            "QC passed on 2024-01-15",
        ],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()

    obj.refresh_from_db()
    blocks = obj.ablocks.filter(kind="comment")
    assert blocks.count() == 1
    assert "QC passed on 2024-01-15" in blocks.one().content

    for x in to_delete:
        x.delete(permanent=True)


def test_raise_incomplete_annotate_call():
    result = subprocess.run(
        ["lamin", "annotate", "--comment", "test"],
        capture_output=True,
    )
    assert result.returncode != 0
    stderr = result.stderr.decode()
    assert "For artifact pass --key or --uid" in stderr
    assert "Traceback" not in stderr
