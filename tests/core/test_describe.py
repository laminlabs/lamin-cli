"""Tests for `lamin describe` across entity types (artifact, transform, run, record, project, ulabel, collection)."""

import subprocess
from pathlib import Path

import lamindb as ln

scripts_dir = Path(__file__).parent.parent / "scripts"


def test_describe_artifact_by_uid_and_key():
    """Describe artifact by --uid and by --key."""
    script_path = scripts_dir / "testscript.py"
    script_path.write_text("print('describe_artifact_test')")
    # Key suffix must match path suffix for artifact
    result = subprocess.run(
        f"lamin save {script_path} --key describe_artifact_test.py --registry artifact",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()

    artifact = ln.Artifact.get(key="describe_artifact_test.py")

    result = subprocess.run(
        f"lamin describe artifact --uid {artifact.uid}",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert artifact.uid in result.stdout or "Artifact" in result.stdout

    result = subprocess.run(
        "lamin describe artifact --key describe_artifact_test.py",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    # cleanup
    subprocess.run(
        "lamin delete artifact --key describe_artifact_test.py --permanent",
        shell=True,
        capture_output=True,
    )


def test_describe_transform_by_uid_and_key():
    """Describe transform by --uid and by --key."""
    # Transform key is path relative to dev-dir; set dev-dir so script path is under it
    subprocess.run(
        f"lamin settings dev-dir set {scripts_dir.parent}",
        shell=True,
        capture_output=True,
    )
    script_path = scripts_dir / "describe_transform_script.py"
    script_path.write_text("print('describe_transform_test')")
    result = subprocess.run(
        f"lamin save {script_path}",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()

    transform_key = f"scripts/{script_path.name}"
    transform = ln.Transform.get(key=transform_key)

    result = subprocess.run(
        f"lamin describe transform --uid {transform.uid}",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert transform.uid in result.stdout or "Transform" in result.stdout

    result = subprocess.run(
        f"lamin describe transform --key {transform_key}",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    subprocess.run(
        f"lamin delete transform --key {transform_key} --permanent",
        shell=True,
        capture_output=True,
    )
    script_path.unlink(missing_ok=True)
    # Restore dev-dir so other tests (e.g. test_save_annotate_scripts) are not affected
    subprocess.run("lamin settings dev-dir unset", shell=True, capture_output=True)


def test_describe_run_by_uid():
    """Describe run by --uid."""
    subprocess.run(
        f"lamin settings dev-dir set {scripts_dir.parent}",
        shell=True,
        capture_output=True,
    )
    script_path = scripts_dir / "describe_run_script.py"
    script_path.write_text("print('describe_run_test')")
    result = subprocess.run(
        f"lamin save {script_path}",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()

    transform_key = f"scripts/{script_path.name}"
    transform = ln.Transform.get(key=transform_key)
    # Run may not be created by save(); create one so we can describe it
    run = ln.Run.filter(transform=transform).order_by("-started_at").first()
    if run is None:
        run = ln.Run(transform=transform).save()

    result = subprocess.run(
        f"lamin describe run --uid {run.uid}",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert run.uid in result.stdout or "Run" in result.stdout

    subprocess.run(
        f"lamin delete transform --key {transform_key} --permanent",
        shell=True,
        capture_output=True,
    )
    script_path.unlink(missing_ok=True)
    # Restore dev-dir so other tests (e.g. test_save_annotate_scripts) are not affected
    subprocess.run("lamin settings dev-dir unset", shell=True, capture_output=True)


def test_describe_record_by_name_and_uid():
    """Describe record by --name and by --uid."""
    record = ln.Record(name="describe_test_record_xyz").save()

    result = subprocess.run(
        "lamin describe record --name describe_test_record_xyz",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert record.uid in result.stdout or "Record" in result.stdout

    result = subprocess.run(
        f"lamin describe record --uid {record.uid}",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    record.delete()


def test_describe_project_by_name_and_uid():
    """Describe project by --name and by --uid."""
    project = ln.Project(name="describe_test_project_xyz").save()

    result = subprocess.run(
        "lamin describe project --name describe_test_project_xyz",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert project.uid in result.stdout or "Project" in result.stdout

    result = subprocess.run(
        f"lamin describe project --uid {project.uid}",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    project.delete()


def test_describe_ulabel_by_name_and_uid():
    """Describe ulabel by --name and by --uid."""
    ulabel = ln.ULabel(name="describe_test_ulabel_xyz").save()

    result = subprocess.run(
        "lamin describe ulabel --name describe_test_ulabel_xyz",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert ulabel.uid in result.stdout or "ULabel" in result.stdout

    result = subprocess.run(
        f"lamin describe ulabel --uid {ulabel.uid}",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    ulabel.delete()


def test_describe_collection_by_uid_and_key():
    """Describe collection by --uid and by --key."""
    # Create an artifact first (key suffix must match path)
    script_path = scripts_dir / "testscript.py"
    script_path.write_text("print('describe_coll_art')")
    result = subprocess.run(
        f"lamin save {script_path} --key describe_coll_artifact.py --registry artifact",
        shell=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()

    artifact = ln.Artifact.get(key="describe_coll_artifact.py")
    collection = ln.Collection([artifact], key="describe_test_collection_xyz").save()

    result = subprocess.run(
        f"lamin describe collection --uid {collection.uid}",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert collection.uid in result.stdout or "Collection" in result.stdout

    result = subprocess.run(
        "lamin describe collection --key describe_test_collection_xyz",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    collection.delete(permanent=True)
    subprocess.run(
        "lamin delete artifact --key describe_coll_artifact.py --permanent",
        shell=True,
        capture_output=True,
    )


def test_describe_invalid_entity():
    """Describe with invalid entity exits non-zero and lists valid entities."""
    result = subprocess.run(
        "lamin describe invalid_entity_xyz",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    err = (result.stderr or result.stdout).lower()
    assert "artifact" in err or "transform" in err
    assert "invalid" in err or "must be" in err


def test_describe_entity_requires_identifier():
    """Describe record without --uid or --name exits non-zero."""
    result = subprocess.run(
        "lamin describe record",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    err = (result.stderr or result.stdout).lower()
    assert "uid" in err or "name" in err

    # run requires --uid
    result = subprocess.run(
        "lamin describe run",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    err = (result.stderr or result.stdout).lower()
    assert "uid" in err
