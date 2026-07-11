import json
from datetime import datetime, timezone
from pathlib import Path

import lamindb as ln
import pytest
from lamin_cli.agents.claude import (
    _RUN_UID_FILE,
    _TRANSCRIPT_PATH_FILE,
    _TRANSFORM_KEY,
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
            report = run.report
            if report is not None:
                run.report = None
                run.save()
            run.delete(permanent=True)
            if report is not None:
                report.delete(permanent=True)
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
    # track opens a run and writes state files
    track_claudecode_session(name="integration test")

    assert _RUN_UID_FILE.exists()
    uid = _RUN_UID_FILE.read_text().strip()
    session_run = ln.Run.get(uid=uid)
    assert session_run.finished_at is None

    # simulate a script that ran with LAMIN_INITIATED_BY_RUN_UID set
    child_transform = ln.Transform(key="analysis.py", kind="script").save()
    child_run = ln.Run(child_transform, initiated_by_run=session_run)
    child_run.finished_at = datetime.now(timezone.utc)
    child_run.save()

    # finish closes the run, saves report, and stamps child transform
    transcript = _write_transcript(tmp_path)
    _TRANSCRIPT_PATH_FILE.write_text(str(transcript))
    finish_claudecode_session()

    assert not _RUN_UID_FILE.exists()
    session_run = ln.Run.get(uid=uid)
    assert session_run.finished_at is not None
    assert session_run.report is not None

    # child Transform.run must point to session run (shows up as session output)
    child_transform = ln.Transform.get(key="analysis.py")
    assert child_transform.run is not None
    assert child_transform.run.uid == uid

    # Cleanup
    child_run.delete(permanent=True)
    child_transform.delete(permanent=True)


def test_track_reuses_transform_across_sessions():
    track_claudecode_session(name="session 1")
    track_claudecode_session(name="session 2")

    assert ln.Transform.filter(key=_TRANSFORM_KEY).count() == 1


def test_finish_without_active_session_exits_cleanly():
    # finish called with no prior track — must not raise
    finish_claudecode_session()
