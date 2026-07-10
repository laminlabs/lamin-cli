import json
from pathlib import Path

import lamindb as ln
import pytest

from lamin_cli._claudecode import (
    _TRANSFORM_KEY,
    _RUN_UID_FILE,
    _TRANSCRIPT_PATH_FILE,
    finish_claudecode_session,
    track_claudecode_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield
    t = ln.Transform.filter(key=_TRANSFORM_KEY).first()
    if t is not None:
        for run in t.runs.all():
            if run.report is not None:
                run.report.delete(permanent=True)
            run.delete(permanent=True)
        t.delete(permanent=True)


def _write_transcript(tmp_path: Path) -> Path:
    p = tmp_path / "session.jsonl"
    for entry in [
        {"role": "user", "content": "do something"},
        {"role": "assistant", "content": "done"},
    ]:
        with p.open("a") as f:
            f.write(json.dumps({"message": entry}) + "\n")
    return p


def test_full_track_finish_flow(tmp_path):
    # Step 1: track opens a run and writes state files
    track_claudecode_session(description="integration test")

    assert _RUN_UID_FILE.exists()
    uid = _RUN_UID_FILE.read_text().strip()
    run = ln.Run.get(uid=uid)
    assert run.finished_at is None

    # Step 2: finish closes the run and saves a report artifact
    transcript = _write_transcript(tmp_path)
    _TRANSCRIPT_PATH_FILE.write_text(str(transcript))

    finish_claudecode_session()

    assert not _RUN_UID_FILE.exists()
    run = ln.Run.get(uid=uid)
    assert run.finished_at is not None
    assert run.report is not None


def test_track_reuses_transform_across_sessions():
    track_claudecode_session(description="session 1")
    track_claudecode_session(description="session 2")

    assert ln.Transform.filter(key=_TRANSFORM_KEY).count() == 1


def test_finish_without_active_session_exits_cleanly():
    # finish called with no prior track — must not raise
    finish_claudecode_session()
