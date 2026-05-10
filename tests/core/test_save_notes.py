import subprocess
import sys
import time
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


def test_save_markdown_note_at_dev_dir_root_creates_record_and_recordblock():
    unique = time.time_ns()
    note_name = "root-note"
    branch = ln.Branch(name=f"cli_notes_root_branch_{unique}").save()
    notes_root = Path(__file__).parent / f"notes_root_{unique}"
    notes_root.mkdir(parents=True, exist_ok=True)
    note_path = notes_root / f"{note_name}.md"
    note_path.write_text("# Root note\n\nhello")
    try:
        ln.setup.switch(branch.name)
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr
        result = run_lamin("save", str(note_path))
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        note_record = ln.Record.filter(name=note_name, type=None, branch=branch).first()
        assert note_record is not None
        readmes = note_record.ablocks.filter(kind="readme").order_by("created_at")
        assert readmes.count() == 1
        assert "Root note" in readmes.first().content
    finally:
        ln.setup.switch("main")
        run_lamin("settings", "dev-dir", "unset")
        note_path.unlink(missing_ok=True)
        if notes_root.exists() and not any(notes_root.iterdir()):
            notes_root.rmdir()
        note_record = ln.Record.filter(name=note_name, type=None, branch=branch).first()
        if note_record is not None:
            note_record.delete(permanent=True)
        branch.delete(permanent=True)


def test_save_readme_in_dev_dir_root_creates_standalone_block():
    unique = time.time_ns()
    branch = ln.Branch(name=f"cli_notes_readme_root_branch_{unique}").save()
    notes_root = Path(__file__).parent / f"notes_readme_root_{unique}"
    notes_root.mkdir(parents=True, exist_ok=True)
    readme_path = notes_root / "README.md"
    readme_path.write_text("# README root\n\ncontent")
    block_uid: str | None = None
    try:
        ln.setup.switch(branch.name)
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr
        result = run_lamin("save", str(readme_path))
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        block = (
            ln.models.Block.filter(key="README.md", branch=branch)
            .order_by("-created_at")
            .first()
        )
        assert block is not None
        block_uid = block.uid
        assert "README root" in block.content
        readme_record = ln.Record.filter(
            name="README", type=None, branch=branch
        ).first()
        assert readme_record is None
        readme_artifact = ln.Artifact.filter(key="README.md", branch=branch).first()
        assert readme_artifact is None
    finally:
        ln.setup.switch(branch.name)
        run_lamin("settings", "dev-dir", "unset")
        readme_path.unlink(missing_ok=True)
        if notes_root.exists() and not any(notes_root.iterdir()):
            notes_root.rmdir()
        block = (
            ln.models.Block.filter(uid=block_uid).one_or_none()
            if block_uid is not None
            else None
        )
        if block is not None:
            block.delete(permanent=True)
        ln.setup.switch("main")
        branch.delete(permanent=True)


def test_save_readme_outside_dev_dir_creates_standalone_block():
    unique = time.time_ns()
    branch = ln.Branch(name=f"cli_notes_readme_outside_dev_dir_branch_{unique}").save()
    outside_root = Path(__file__).parent / f"notes_readme_outside_dev_dir_{unique}"
    outside_root.mkdir(parents=True, exist_ok=True)
    readme_path = outside_root / "README.md"
    readme_path.write_text("# README outside dev-dir\n\ncontent")
    block_uid: str | None = None
    try:
        ln.setup.switch(branch.name)
        run_lamin("settings", "dev-dir", "unset")
        result = run_lamin("save", str(readme_path))
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        block = (
            ln.models.Block.filter(key="README.md", branch=branch)
            .order_by("-created_at")
            .first()
        )
        assert block is not None
        block_uid = block.uid
        assert "outside dev-dir" in block.content
        readme_record = ln.Record.filter(
            name="README", type=None, branch=branch
        ).first()
        assert readme_record is None
        readme_artifact = ln.Artifact.filter(key="README.md", branch=branch).first()
        assert readme_artifact is None
    finally:
        ln.setup.switch(branch.name)
        readme_path.unlink(missing_ok=True)
        if outside_root.exists() and not any(outside_root.iterdir()):
            outside_root.rmdir()
        block = (
            ln.models.Block.filter(uid=block_uid).one_or_none()
            if block_uid is not None
            else None
        )
        if block is not None:
            block.delete(permanent=True)
        ln.setup.switch("main")
        branch.delete(permanent=True)


def test_save_readme_as_artifact_also_creates_standalone_block():
    unique = time.time_ns()
    branch = ln.Branch(name=f"cli_readme_branch_{unique}").save()
    readme_path = Path(__file__).parent / f"README_{unique}.md"
    readme_path.write_text(f"# README {unique}\n\nfirst version")
    block_uids: list[str] = []
    try:
        ln.setup.switch(branch.name)
        result = run_lamin(
            "save",
            str(readme_path),
            "--key",
            "README.md",
            "--branch",
            branch.name,
            "--registry",
            "artifact",
        )
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        artifact = ln.Artifact.get(key="README.md", branch=branch)
        block = (
            ln.models.Block.filter(key="README.md", branch=branch)
            .order_by("-created_at")
            .first()
        )
        assert block is not None
        block_uids.append(block.uid)
        assert f"README {unique}" in block.content
        assert block.is_latest

        readme_path.write_text(f"# README {unique}\n\nsecond version")
        result = run_lamin(
            "save",
            str(readme_path),
            "--key",
            "README.md",
            "--branch",
            branch.name,
            "--registry",
            "artifact",
        )
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        blocks = ln.models.Block.filter(key="README.md", branch=branch).order_by(
            "created_at"
        )
        assert blocks.count() >= 2
        latest = blocks.last()
        assert latest is not None
        block_uids.extend([b.uid for b in blocks if b.uid not in block_uids])
        assert f"README {unique}" in latest.content
        assert "second version" in latest.content
        assert latest.is_latest
        assert latest.stem_uid == block.uid[:16]
    finally:
        ln.setup.switch(branch.name)
        readme_path.unlink(missing_ok=True)
        for artifact in ln.Artifact.filter(key="README.md", branch=branch):
            artifact.delete(permanent=True)
        for uid in block_uids:
            block = ln.models.Block.filter(uid=uid).one_or_none()
            if block is not None:
                block.delete(permanent=True)
        ln.setup.switch("main")
        branch.delete(permanent=True)
