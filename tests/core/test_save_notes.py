import subprocess
import sys
from pathlib import Path

import lamindb as ln


def run_lamin(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "lamin_cli", *args],
        capture_output=True,
        text=True,
    )


def test_save_markdown_note_creates_record_and_recordblock():
    topic = "cli-note-topic"
    note_name = "cli-note"
    branch = ln.Branch(name="cli_notes_branch").save()
    type_record = ln.Record(name=topic, is_type=True).save()

    notes_root = Path(__file__).parent / "notes"
    note_dir = notes_root / topic
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{note_name}.md"
    note_path.write_text("# First version\n\nhello")

    try:
        ln.setup.switch(branch.name)
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr

        result = run_lamin("save", str(note_path))
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "saved note" in result.stdout

        note_record = (
            ln.Record.filter(name=note_name, type=type_record)
            .order_by("-created_at")
            .first()
        )
        assert note_record is not None
        assert note_record.is_type is True
        assert note_record.branch == branch
        readmes = note_record.ablocks.filter(kind="readme").order_by("created_at")
        assert readmes.count() == 1
        assert readmes.first().branch == branch
        assert "First version" in readmes.first().content

        note_path.write_text("# Second version\n\nhello again")
        result = run_lamin("save", str(note_path))
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        readmes = note_record.ablocks.filter(kind="readme").order_by("created_at")
        assert readmes.count() == 2
        assert "Second version" in readmes.last().content
        assert readmes.last().is_latest
    finally:
        ln.setup.switch("main")
        run_lamin("settings", "dev-dir", "unset")
        note_path.unlink(missing_ok=True)
        if note_dir.exists() and not any(note_dir.iterdir()):
            note_dir.rmdir()
        if notes_root.exists() and not any(notes_root.iterdir()):
            notes_root.rmdir()
        note_record = ln.Record.filter(
            name=note_name, type=type_record, branch=branch
        ).one_or_none()
        if note_record is not None:
            note_record.delete(permanent=True)
        if ln.Record.filter(uid=type_record.uid).one_or_none() is not None:
            type_record.refresh_from_db()
            type_record.delete(permanent=True)
        branch.delete(permanent=True)


def test_save_markdown_note_requires_existing_type():
    topic = "cli-missing-note-type"
    note_name = "missing-type-note"
    notes_root = Path(__file__).parent / "notes_missing_type"
    note_dir = notes_root / topic
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{note_name}.md"
    note_path.write_text("# Missing type")

    try:
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr
        result = run_lamin("save", str(note_path))
        assert result.returncode == 1
        assert f"Record type '{topic}' not found" in result.stderr
    finally:
        run_lamin("settings", "dev-dir", "unset")
        note_path.unlink(missing_ok=True)
        if note_dir.exists() and not any(note_dir.iterdir()):
            note_dir.rmdir()
        if notes_root.exists() and not any(notes_root.iterdir()):
            notes_root.rmdir()


def test_save_markdown_note_type_match_is_case_insensitive():
    topic_type_name = "Blog"
    topic_folder_name = "blog"
    note_name = "case-sensitive-note"
    branch = ln.Branch(name="cli_notes_case_branch").save()
    type_record = ln.Record(name=topic_type_name, is_type=True).save()

    notes_root = Path(__file__).parent / "notes_case_insensitive"
    note_dir = notes_root / topic_folder_name
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{note_name}.md"
    note_path.write_text("# Case mapping")

    try:
        ln.setup.switch(branch.name)
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr

        result = run_lamin("save", str(note_path))
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        note_record = (
            ln.Record.filter(name=note_name, type=type_record)
            .order_by("-created_at")
            .first()
        )
        assert note_record is not None
    finally:
        ln.setup.switch("main")
        run_lamin("settings", "dev-dir", "unset")
        note_path.unlink(missing_ok=True)
        if note_dir.exists() and not any(note_dir.iterdir()):
            note_dir.rmdir()
        if notes_root.exists() and not any(notes_root.iterdir()):
            notes_root.rmdir()
        note_record = ln.Record.filter(
            name=note_name, type=type_record, branch=branch
        ).one_or_none()
        if note_record is not None:
            note_record.delete(permanent=True)
        if ln.Record.filter(uid=type_record.uid).one_or_none() is not None:
            type_record.refresh_from_db()
            type_record.delete(permanent=True)
        branch.delete(permanent=True)


def test_save_markdown_note_registry_record_forces_record():
    topic = "registry-record-topic"
    note_name = "registry-record-note"
    branch = ln.Branch(name="cli_notes_registry_record_branch").save()
    type_record = ln.Record(name=topic, is_type=True).save()

    notes_root = Path(__file__).parent / "notes_registry_record"
    note_dir = notes_root / topic
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{note_name}.md"
    note_path.write_text("# Registry record")

    try:
        ln.setup.switch(branch.name)
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr

        result = run_lamin(
            "save", str(note_path), "--registry", "record", "--key", "ignored"
        )
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        note_record = (
            ln.Record.filter(name=note_name, type=type_record)
            .order_by("-created_at")
            .first()
        )
        assert note_record is not None
    finally:
        ln.setup.switch("main")
        run_lamin("settings", "dev-dir", "unset")
        note_path.unlink(missing_ok=True)
        if note_dir.exists() and not any(note_dir.iterdir()):
            note_dir.rmdir()
        if notes_root.exists() and not any(notes_root.iterdir()):
            notes_root.rmdir()
        note_record = ln.Record.filter(
            name=note_name, type=type_record, branch=branch
        ).one_or_none()
        if note_record is not None:
            note_record.delete(permanent=True)
        if ln.Record.filter(uid=type_record.uid).one_or_none() is not None:
            type_record.refresh_from_db()
            type_record.delete(permanent=True)
        branch.delete(permanent=True)


def test_save_markdown_note_registry_artifact_forces_artifact():
    topic = "registry-artifact-topic"
    note_name = "registry-artifact-note"
    branch = ln.Branch(name="cli_notes_registry_artifact_branch").save()
    type_record = ln.Record(name=topic, is_type=True).save()
    artifact_key = "notes/forced-artifact-note.md"
    artifact_uid = None

    notes_root = Path(__file__).parent / "notes_registry_artifact"
    note_dir = notes_root / topic
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{note_name}.md"
    note_path.write_text("# Registry artifact")

    try:
        ln.setup.switch(branch.name)
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr

        result = run_lamin(
            "save",
            str(note_path),
            "--registry",
            "artifact",
            "--key",
            artifact_key,
        )
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        artifact = ln.Artifact.get(key=artifact_key)
        assert artifact is not None
        artifact_uid = artifact.uid
        note_record = ln.Record.filter(
            name=note_name, type=type_record, branch=branch
        ).one_or_none()
        assert note_record is None
    finally:
        ln.setup.switch("main")
        run_lamin("settings", "dev-dir", "unset")
        note_path.unlink(missing_ok=True)
        if note_dir.exists() and not any(note_dir.iterdir()):
            note_dir.rmdir()
        if notes_root.exists() and not any(notes_root.iterdir()):
            notes_root.rmdir()
        artifact = (
            ln.Artifact.filter(uid=artifact_uid).one_or_none()
            if artifact_uid is not None
            else None
        )
        if artifact is not None:
            artifact.delete(permanent=True)
        if ln.Record.filter(uid=type_record.uid).one_or_none() is not None:
            type_record.refresh_from_db()
            type_record.delete(permanent=True)
        branch.delete(permanent=True)
