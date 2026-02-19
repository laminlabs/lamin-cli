import subprocess
from pathlib import Path

import lamindb as ln

scripts_dir = Path(__file__).parent.parent / "scripts"


def _run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def test_update_and_get_branch_status():
    branch_name = "update_status_branch_cli"
    branch = ln.Branch.filter(name=branch_name).one_or_none()
    if branch is not None:
        branch.delete(permanent=True)

    result = _run(f"lamin switch -c {branch_name}")
    assert result.returncode == 0, result.stderr or result.stdout

    result = _run("lamin update branch --status draft")
    assert result.returncode == 0, result.stderr or result.stdout

    result = _run("lamin get branch --status")
    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.strip().split("\n")[-1] == "draft"

    result = _run("lamin update branch --status review")
    assert result.returncode == 0, result.stderr or result.stdout

    result = _run("lamin get branch --status")
    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.strip().split("\n")[-1] == "review"

    result = _run("lamin switch main")
    assert result.returncode == 0, result.stderr or result.stdout
    result = _run(f"lamin delete branch --name {branch_name} --permanent")
    assert result.returncode == 0, result.stderr or result.stdout


def test_update_and_get_description_for_entities():
    project_name = "update_desc_project_cli"
    artifact_key = "update_desc_artifact_cli.txt"
    collection_key = "update_desc_collection_cli"
    script_path = scripts_dir / "update_desc_transform_cli.py"
    transform_key = f"scripts/{script_path.name}"
    artifact_path = scripts_dir / "update_desc_artifact_cli.txt"

    project = ln.Project.filter(name=project_name).one_or_none()
    if project is not None:
        project.delete(permanent=True)
    transform = ln.Transform.filter(key=transform_key).one_or_none()
    if transform is not None:
        transform.delete(permanent=True)
    collection = ln.Collection.filter(key=collection_key).one_or_none()
    if collection is not None:
        collection.delete(permanent=True)
    artifact = ln.Artifact.filter(key=artifact_key).one_or_none()
    if artifact is not None:
        artifact.delete(permanent=True)

    artifact_path.write_text("artifact for update description test", encoding="utf-8")
    script_path.write_text("print('update desc transform test')\n", encoding="utf-8")

    try:
        ln.Project(name=project_name).save()
        result = _run(
            f'lamin update project --name {project_name} --description "project description"'
        )
        assert result.returncode == 0, result.stderr or result.stdout
        result = _run(f"lamin get project --name {project_name} --description")
        assert result.returncode == 0, result.stderr or result.stdout
        assert result.stdout.strip().split("\n")[-1] == "project description"

        result = _run(
            f"lamin save {artifact_path} --key {artifact_key} --registry artifact"
        )
        assert result.returncode == 0, result.stderr or result.stdout
        result = _run(
            f'lamin update artifact --key {artifact_key} --description "artifact description"'
        )
        assert result.returncode == 0, result.stderr or result.stdout
        result = _run(f"lamin get artifact --key {artifact_key} --description")
        assert result.returncode == 0, result.stderr or result.stdout
        assert result.stdout.strip().split("\n")[-1] == "artifact description"

        artifact = ln.Artifact.get(key=artifact_key)
        ln.Collection([artifact], key=collection_key).save()
        result = _run(
            f'lamin update collection --key {collection_key} --description "collection description"'
        )
        assert result.returncode == 0, result.stderr or result.stdout
        result = _run(f"lamin get collection --key {collection_key} --description")
        assert result.returncode == 0, result.stderr or result.stdout
        assert result.stdout.strip().split("\n")[-1] == "collection description"

        result = _run(f"lamin settings dev-dir set {scripts_dir.parent}")
        assert result.returncode == 0, result.stderr or result.stdout
        result = _run(f"lamin save {script_path}")
        assert result.returncode == 0, result.stderr or result.stdout
        result = _run(
            f'lamin update transform --key {transform_key} --description "transform description"'
        )
        assert result.returncode == 0, result.stderr or result.stdout
        result = _run(f"lamin get transform --key {transform_key} --description")
        assert result.returncode == 0, result.stderr or result.stdout
        assert result.stdout.strip().split("\n")[-1] == "transform description"
    finally:
        _run("lamin settings dev-dir unset")
        _run(f"lamin delete collection --key {collection_key} --permanent")
        _run(f"lamin delete transform --key {transform_key} --permanent")
        _run(f"lamin delete artifact --key {artifact_key} --permanent")
        project = ln.Project.filter(name=project_name).one_or_none()
        if project is not None:
            project.delete(permanent=True)
        artifact_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)


def test_update_and_get_invalid_field_entity_combinations():
    result = _run("lamin update artifact --status draft")
    assert result.returncode != 0

    result = _run("lamin update branch --description 'branch description'")
    assert result.returncode != 0

    result = _run("lamin get artifact --status")
    assert result.returncode != 0

    result = _run("lamin get branch --description")
    assert result.returncode != 0
