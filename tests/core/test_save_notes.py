import shutil
import subprocess
import sys
import time
from pathlib import Path

import lamindb as ln


def run_lamin(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "lamin_cli", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
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


def test_save_readme_in_dev_dir_root_stays_artifact_and_block():
    unique = time.time_ns()
    branch = ln.Branch(name=f"cli_notes_readme_root_branch_{unique}").save()
    notes_root = Path(__file__).parent / f"notes_readme_root_{unique}"
    notes_root.mkdir(parents=True, exist_ok=True)
    readme_path = notes_root / "README.md"
    readme_path.write_text("# README root\n\ncontent")
    block_uids: list[str] = []
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
        block_uids.append(block.uid)
        assert "README root" in block.content
        readme_record = ln.Record.filter(
            name="README", type=None, branch=branch
        ).first()
        assert readme_record is None
        readme_artifact = ln.Artifact.filter(key="README.md", branch=branch).first()
        assert readme_artifact is not None
    finally:
        ln.setup.switch(branch.name)
        run_lamin("settings", "dev-dir", "unset")
        readme_path.unlink(missing_ok=True)
        if notes_root.exists() and not any(notes_root.iterdir()):
            notes_root.rmdir()
        for artifact in ln.Artifact.filter(key="README.md", branch=branch):
            artifact.delete(permanent=True)
        for uid in block_uids:
            block = ln.models.Block.filter(uid=uid).one_or_none()
            if block is not None:
                block.delete(permanent=True)
        ln.setup.switch("main")
        branch.delete(permanent=True)


def test_save_readme_outside_dev_dir_creates_artifact_and_block():
    unique = time.time_ns()
    branch = ln.Branch(name=f"cli_notes_readme_outside_dev_dir_branch_{unique}").save()
    outside_root = Path(__file__).parent / f"notes_readme_outside_dev_dir_{unique}"
    outside_root.mkdir(parents=True, exist_ok=True)
    readme_path = outside_root / "README.md"
    readme_path.write_text("# README outside dev-dir\n\ncontent")
    block_uids: list[str] = []
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
        block_uids.append(block.uid)
        assert "outside dev-dir" in block.content
        readme_record = ln.Record.filter(
            name="README", type=None, branch=branch
        ).first()
        assert readme_record is None
        readme_artifact = ln.Artifact.filter(key="README.md", branch=branch).first()
        assert readme_artifact is not None
    finally:
        ln.setup.switch(branch.name)
        readme_path.unlink(missing_ok=True)
        if outside_root.exists() and not any(outside_root.iterdir()):
            outside_root.rmdir()
        for artifact in ln.Artifact.filter(key="README.md", branch=branch):
            artifact.delete(permanent=True)
        for uid in block_uids:
            block = ln.models.Block.filter(uid=uid).one_or_none()
            if block is not None:
                block.delete(permanent=True)
        ln.setup.switch("main")
        branch.delete(permanent=True)


def test_save_readme_relative_path_in_dev_dir_saves_artifact_and_block():
    unique = time.time_ns()
    branch = ln.Branch(name=f"cli_notes_readme_relative_branch_{unique}").save()
    notes_root = Path(__file__).parent / f"notes_readme_relative_{unique}"
    notes_root.mkdir(parents=True, exist_ok=True)
    readme_path = notes_root / "README.md"
    readme_path.write_text("# README relative\n\ncontent")
    block_uids: list[str] = []
    try:
        ln.setup.switch(branch.name)
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr
        result = subprocess.run(
            [sys.executable, "-m", "lamin_cli", "save", "README.md"],
            capture_output=True,
            text=True,
            cwd=notes_root,
        )
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        artifact = ln.Artifact.filter(key="README.md", branch=branch).first()
        assert artifact is not None
        block = (
            ln.models.Block.filter(key="README.md", branch=branch)
            .order_by("-created_at")
            .first()
        )
        assert block is not None
        block_uids.append(block.uid)
        assert "README relative" in block.content
    finally:
        ln.setup.switch(branch.name)
        run_lamin("settings", "dev-dir", "unset")
        readme_path.unlink(missing_ok=True)
        if notes_root.exists() and not any(notes_root.iterdir()):
            notes_root.rmdir()
        for artifact in ln.Artifact.filter(key="README.md", branch=branch):
            artifact.delete(permanent=True)
        for uid in block_uids:
            block = ln.models.Block.filter(uid=uid).one_or_none()
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
        artifact = ln.Artifact.filter(key="README.md", branch=branch).first()
        assert artifact is not None
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


def test_save_markdown_note_three_level_hierarchy():
    unique = time.time_ns()
    branch = ln.Branch(name=f"cli_notes_three_level_branch_{unique}").save()
    notes_root = Path(__file__).parent / f"notes_three_level_{unique}"

    topic_type = ln.Record(name=f"topic-{unique}", is_type=True).save()
    subtopic_type = ln.Record(
        name=f"subtopic-{unique}",
        type=topic_type,
        is_type=True,
    ).save()
    note_record = None
    try:
        ln.setup.switch(branch.name)
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr

        # Use type names from DB while keeping 3-level filesystem nesting.
        typed_note_path = (
            notes_root / topic_type.name / subtopic_type.name / "my-note.md"
        )
        typed_note_path.parent.mkdir(parents=True, exist_ok=True)
        typed_note_path.write_text("# Three level note\n\ncontent")

        result = run_lamin("save", str(typed_note_path))
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        note_record = (
            ln.Record.filter(name="my-note", type=subtopic_type, branch=branch)
            .order_by("-created_at")
            .first()
        )
        assert note_record is not None
        readme_block = (
            note_record.ablocks.filter(kind="readme").order_by("-created_at").first()
        )
        assert readme_block is not None
        assert "Three level note" in readme_block.content
    finally:
        ln.setup.switch("main")
        run_lamin("settings", "dev-dir", "unset")
        if notes_root.exists():
            shutil.rmtree(notes_root)
        if note_record is not None:
            note_record.delete(permanent=True)
        subtopic_lookup = ln.Record.filter(uid=subtopic_type.uid).one_or_none()
        if subtopic_lookup is not None:
            subtopic_type.refresh_from_db()
            subtopic_type.delete(permanent=True)
        topic_lookup = ln.Record.filter(uid=topic_type.uid).one_or_none()
        if topic_lookup is not None:
            topic_type.refresh_from_db()
            topic_type.delete(permanent=True)
        branch.delete(permanent=True)


def test_load_markdown_note_inside_dev_dir_preserves_nesting():
    unique = time.time_ns()
    branch = ln.Branch(name=f"cli_load_note_nested_branch_{unique}").save()
    notes_root = Path(__file__).parent / f"load_notes_nested_{unique}"

    topic_type = ln.Record(name=f"topic-load-{unique}", is_type=True).save()
    subtopic_type = ln.Record(
        name=f"subtopic-load-{unique}",
        type=topic_type,
        is_type=True,
    ).save()
    typed_source_path = notes_root / topic_type.name / subtopic_type.name / "my-note.md"
    typed_source_path.parent.mkdir(parents=True, exist_ok=True)
    typed_source_path.write_text("# Nested load\n\ninside dev-dir")
    note_record = None
    try:
        ln.setup.switch(branch.name)
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr
        save_result = run_lamin("save", str(typed_source_path))
        assert save_result.returncode == 0, (
            f"stdout: {save_result.stdout}\nstderr: {save_result.stderr}"
        )
        note_record = (
            ln.Record.filter(name="my-note", type=subtopic_type, branch=branch)
            .order_by("-created_at")
            .first()
        )
        assert note_record is not None

        typed_source_path.unlink()
        load_result = run_lamin(
            "load", f"{topic_type.name}/{subtopic_type.name}/my-note", cwd=notes_root
        )
        assert load_result.returncode == 0, (
            f"stdout: {load_result.stdout}\nstderr: {load_result.stderr}"
        )
        assert typed_source_path.exists()
        assert "inside dev-dir" in typed_source_path.read_text()

        typed_source_path.unlink()
        load_result = run_lamin(
            "load",
            f"{topic_type.name}/{subtopic_type.name}/my-note.md",
            cwd=notes_root,
        )
        assert load_result.returncode == 0, (
            f"stdout: {load_result.stdout}\nstderr: {load_result.stderr}"
        )
        assert typed_source_path.exists()
        assert "inside dev-dir" in typed_source_path.read_text()
    finally:
        ln.setup.switch("main")
        run_lamin("settings", "dev-dir", "unset")
        if notes_root.exists():
            shutil.rmtree(notes_root)
        if note_record is not None:
            note_record.delete(permanent=True)
        subtopic_lookup = ln.Record.filter(uid=subtopic_type.uid).one_or_none()
        if subtopic_lookup is not None:
            subtopic_type.refresh_from_db()
            subtopic_type.delete(permanent=True)
        topic_lookup = ln.Record.filter(uid=topic_type.uid).one_or_none()
        if topic_lookup is not None:
            topic_type.refresh_from_db()
            topic_type.delete(permanent=True)
        branch.delete(permanent=True)


def test_load_markdown_note_outside_dev_dir_flattens_output():
    unique = time.time_ns()
    branch = ln.Branch(name=f"cli_load_note_flatten_branch_{unique}").save()
    notes_root = Path(__file__).parent / f"load_notes_flatten_{unique}"
    outside_root = Path(__file__).parent / f"load_notes_flatten_outside_{unique}"
    outside_root.mkdir(parents=True, exist_ok=True)
    topic_type = ln.Record(name=f"topic-flat-{unique}", is_type=True).save()
    subtopic_type = ln.Record(
        name=f"subtopic-flat-{unique}",
        type=topic_type,
        is_type=True,
    ).save()
    source_path = notes_root / topic_type.name / subtopic_type.name / "my-note.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("# Flatten load\n\noutside dev-dir")
    note_record = None
    try:
        ln.setup.switch(branch.name)
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr
        save_result = run_lamin("save", str(source_path))
        assert save_result.returncode == 0, (
            f"stdout: {save_result.stdout}\nstderr: {save_result.stderr}"
        )
        note_record = (
            ln.Record.filter(name="my-note", type=subtopic_type, branch=branch)
            .order_by("-created_at")
            .first()
        )
        assert note_record is not None

        flattened_path = outside_root / "my-note.md"
        flattened_path.unlink(missing_ok=True)
        load_result = run_lamin(
            "load",
            f"{topic_type.name}/{subtopic_type.name}/my-note",
            cwd=outside_root,
        )
        assert load_result.returncode == 0, (
            f"stdout: {load_result.stdout}\nstderr: {load_result.stderr}"
        )
        assert flattened_path.exists()
        assert "outside dev-dir" in flattened_path.read_text()
        assert not (
            outside_root / topic_type.name / subtopic_type.name / "my-note.md"
        ).exists()
    finally:
        ln.setup.switch("main")
        run_lamin("settings", "dev-dir", "unset")
        if notes_root.exists():
            shutil.rmtree(notes_root)
        if outside_root.exists():
            shutil.rmtree(outside_root)
        if note_record is not None:
            note_record.delete(permanent=True)
        subtopic_lookup = ln.Record.filter(uid=subtopic_type.uid).one_or_none()
        if subtopic_lookup is not None:
            subtopic_type.refresh_from_db()
            subtopic_type.delete(permanent=True)
        topic_lookup = ln.Record.filter(uid=topic_type.uid).one_or_none()
        if topic_lookup is not None:
            topic_type.refresh_from_db()
            topic_type.delete(permanent=True)
        branch.delete(permanent=True)


def test_load_markdown_note_resolves_hierarchy_by_parent_chain():
    unique = time.time_ns()
    branch = ln.Branch(name=f"cli_load_note_parent_chain_branch_{unique}").save()
    notes_root = Path(__file__).parent / f"load_notes_parent_chain_{unique}"

    topic_a = ln.Record(name=f"topic-a-{unique}", is_type=True).save()
    topic_b = ln.Record(name=f"topic-b-{unique}", is_type=True).save()
    subtopic_name = f"shared-{unique}"
    subtopic_a = ln.Record(name=subtopic_name, type=topic_a, is_type=True).save()
    subtopic_b = ln.Record(name=subtopic_name, type=topic_b, is_type=True).save()
    note_record_a = None
    note_record_b = None
    try:
        ln.setup.switch(branch.name)
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr

        source_path_a = notes_root / topic_a.name / subtopic_name / "my-note.md"
        source_path_a.parent.mkdir(parents=True, exist_ok=True)
        source_path_a.write_text("# Note A\n\nfrom parent A")
        save_a = run_lamin("save", str(source_path_a))
        assert save_a.returncode == 0, (
            f"stdout: {save_a.stdout}\nstderr: {save_a.stderr}"
        )

        source_path_b = notes_root / topic_b.name / subtopic_name / "my-note.md"
        source_path_b.parent.mkdir(parents=True, exist_ok=True)
        source_path_b.write_text("# Note B\n\nfrom parent B")
        save_b = run_lamin("save", str(source_path_b))
        assert save_b.returncode == 0, (
            f"stdout: {save_b.stdout}\nstderr: {save_b.stderr}"
        )

        note_record_a = (
            ln.Record.filter(name="my-note", type=subtopic_a, branch=branch)
            .order_by("-created_at")
            .first()
        )
        note_record_b = (
            ln.Record.filter(name="my-note", type=subtopic_b, branch=branch)
            .order_by("-created_at")
            .first()
        )
        assert note_record_a is not None
        assert note_record_b is not None

        source_path_b.unlink()
        load_result = run_lamin(
            "load", f"{topic_b.name}/{subtopic_name}/my-note", cwd=notes_root
        )
        assert load_result.returncode == 0, (
            f"stdout: {load_result.stdout}\nstderr: {load_result.stderr}"
        )
        assert source_path_b.exists()
        assert "from parent B" in source_path_b.read_text()
    finally:
        ln.setup.switch("main")
        run_lamin("settings", "dev-dir", "unset")
        if notes_root.exists():
            shutil.rmtree(notes_root)
        if note_record_a is not None:
            note_record_a.delete(permanent=True)
        if note_record_b is not None:
            note_record_b.delete(permanent=True)
        for type_record in (subtopic_a, subtopic_b, topic_a, topic_b):
            existing = ln.Record.filter(uid=type_record.uid).one_or_none()
            if existing is not None:
                type_record.refresh_from_db()
                type_record.delete(permanent=True)
        branch.delete(permanent=True)


def test_save_markdown_note_missing_intermediate_type_in_chain():
    unique = time.time_ns()
    branch = ln.Branch(name=f"cli_notes_missing_chain_branch_{unique}").save()
    notes_root = Path(__file__).parent / f"notes_missing_chain_{unique}"
    notes_root.mkdir(parents=True, exist_ok=True)

    topic_type = ln.Record(name=f"topic-chain-{unique}", is_type=True).save()
    missing_subtopic_name = f"missing-subtopic-{unique}"
    note_path = notes_root / topic_type.name / missing_subtopic_name / "my-note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Missing chain\n\ncontent")
    try:
        ln.setup.switch(branch.name)
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr
        result = run_lamin("save", str(note_path))
        assert result.returncode == 1
        assert f"Record type '{missing_subtopic_name}' not found" in result.stderr
    finally:
        ln.setup.switch("main")
        run_lamin("settings", "dev-dir", "unset")
        if notes_root.exists():
            shutil.rmtree(notes_root)
        existing_topic = ln.Record.filter(uid=topic_type.uid).one_or_none()
        if existing_topic is not None:
            topic_type.refresh_from_db()
            topic_type.delete(permanent=True)
        branch.delete(permanent=True)


def test_load_readme_with_dev_dir_writes_to_dev_dir_root():
    unique = time.time_ns()
    branch = ln.Branch(name=f"cli_load_readme_dev_dir_branch_{unique}").save()
    notes_root = Path(__file__).parent / f"load_readme_dev_dir_{unique}"
    notes_root.mkdir(parents=True, exist_ok=True)
    readme_path = notes_root / "README.md"
    readme_path.write_text("# README in dev-dir\n\ncontent")
    block_uids: list[str] = []
    try:
        ln.setup.switch(branch.name)
        set_dev_dir = run_lamin("settings", "dev-dir", "set", str(notes_root))
        assert set_dev_dir.returncode == 0, set_dev_dir.stderr

        save_result = run_lamin("save", str(readme_path))
        assert save_result.returncode == 0, (
            f"stdout: {save_result.stdout}\nstderr: {save_result.stderr}"
        )
        readme_path.unlink()

        load_result = run_lamin("load", "README.md")
        assert load_result.returncode == 0, (
            f"stdout: {load_result.stdout}\nstderr: {load_result.stderr}"
        )
        assert readme_path.exists()
        assert "README in dev-dir" in readme_path.read_text()
        block = (
            ln.models.Block.filter(key="README.md", branch=branch)
            .order_by("-created_at")
            .first()
        )
        if block is not None:
            block_uids.append(block.uid)
    finally:
        ln.setup.switch(branch.name)
        run_lamin("settings", "dev-dir", "unset")
        if notes_root.exists():
            shutil.rmtree(notes_root)
        for artifact in ln.Artifact.filter(key="README.md", branch=branch):
            artifact.delete(permanent=True)
        for uid in block_uids:
            block = ln.models.Block.filter(uid=uid).one_or_none()
            if block is not None:
                block.delete(permanent=True)
        ln.setup.switch("main")
        branch.delete(permanent=True)


def test_load_readme_without_dev_dir_writes_to_cwd_root():
    unique = time.time_ns()
    branch = ln.Branch(name=f"cli_load_readme_cwd_branch_{unique}").save()
    source_root = Path(__file__).parent / f"load_readme_source_{unique}"
    outside_root = Path(__file__).parent / f"load_readme_cwd_{unique}"
    source_root.mkdir(parents=True, exist_ok=True)
    outside_root.mkdir(parents=True, exist_ok=True)
    readme_path = source_root / "README.md"
    readme_path.write_text("# README in cwd\n\ncontent")
    block_uids: list[str] = []
    try:
        ln.setup.switch(branch.name)
        run_lamin("settings", "dev-dir", "unset")

        save_result = run_lamin("save", str(readme_path))
        assert save_result.returncode == 0, (
            f"stdout: {save_result.stdout}\nstderr: {save_result.stderr}"
        )

        target_readme = outside_root / "README.md"
        target_readme.unlink(missing_ok=True)
        load_result = run_lamin("load", "README.md", cwd=outside_root)
        assert load_result.returncode == 0, (
            f"stdout: {load_result.stdout}\nstderr: {load_result.stderr}"
        )
        assert target_readme.exists()
        assert "README in cwd" in target_readme.read_text()
        block = (
            ln.models.Block.filter(key="README.md", branch=branch)
            .order_by("-created_at")
            .first()
        )
        if block is not None:
            block_uids.append(block.uid)
    finally:
        ln.setup.switch(branch.name)
        if source_root.exists():
            shutil.rmtree(source_root)
        if outside_root.exists():
            shutil.rmtree(outside_root)
        for artifact in ln.Artifact.filter(key="README.md", branch=branch):
            artifact.delete(permanent=True)
        for uid in block_uids:
            block = ln.models.Block.filter(uid=uid).one_or_none()
            if block is not None:
                block.delete(permanent=True)
        ln.setup.switch("main")
        branch.delete(permanent=True)
