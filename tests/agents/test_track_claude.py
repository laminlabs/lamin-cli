import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import lamindb as ln
import pytest
from lamin_cli.agents.claude import (
    _TRANSFORM_KEY,
    _run_uid_file,
    _transcript_path_file,
    finish_claudecode_session,
    track_claudecode_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "test-session")
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
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_finish",
                    "name": "Bash",
                    "input": {"command": "lamin finish"},
                }
            ],
        },
    ]:
        with p.open("a") as f:
            f.write(json.dumps({"message": entry}) + "\n")
    return p


def _write_transcript_with_usage(tmp_path: Path) -> Path:
    """Mirror Claude Code's real format: one JSONL line per content block,
    with every block of the same turn repeating the same message id and the
    same usage totals (must be de-duplicated by id when summing tokens)."""
    p = tmp_path / "session.jsonl"
    usage_1 = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 5,
    }
    usage_2 = {
        "input_tokens": 20,
        "output_tokens": 30,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 200,
    }
    entries = [
        {"role": "user", "content": "do something"},
        {
            "role": "assistant",
            "id": "msg_1",
            "content": [{"type": "thinking", "thinking": "let me look"}],
            "usage": usage_1,
        },
        {
            "role": "assistant",
            "id": "msg_1",
            "content": [{"type": "text", "text": "on it"}],
            "usage": usage_1,
        },
        {
            "role": "assistant",
            "id": "msg_1",
            "content": [
                {"type": "tool_use", "id": "tool_1", "name": "Bash", "input": {}}
            ],
            "usage": usage_1,
        },
        {"role": "user", "content": "continue"},
        {
            "role": "assistant",
            "id": "msg_2",
            "content": [{"type": "text", "text": "done"}],
            "usage": usage_2,
        },
        {
            "role": "assistant",
            "id": "msg_2",
            "content": [
                {"type": "tool_use", "id": "tool_2", "name": "Bash", "input": {}}
            ],
            "usage": usage_2,
        },
        {
            "role": "assistant",
            "id": "msg_3",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_finish",
                    "name": "Bash",
                    "input": {"command": "lamin finish"},
                }
            ],
            # no usage on this one -- Claude Code hasn't returned a result for
            # its own still-in-flight finish call yet, so no usage is billed
        },
    ]
    with p.open("a") as f:
        for entry in entries:
            f.write(json.dumps({"message": entry}) + "\n")
    return p


def test_full_track_finish_flow(tmp_path):
    # track opens a run and writes state files
    track_claudecode_session(name="integration test")

    assert _run_uid_file().exists()
    uid = _run_uid_file().read_text().strip()
    session_run = ln.Run.get(uid=uid)
    assert session_run.finished_at is None

    # simulate a script that ran with LAMIN_INITIATED_BY_RUN_UID set
    child_transform = ln.Transform(key="analysis.py", kind="script").save()
    child_run = ln.Run(child_transform, initiated_by_run=session_run)
    child_run.finished_at = datetime.now(timezone.utc)
    child_run.save()

    # finish closes the run, saves report, and stamps child transform
    transcript = _write_transcript(tmp_path)
    _transcript_path_file().write_text(str(transcript))
    finish_claudecode_session()

    assert not _run_uid_file().exists()
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


def test_finish_extracts_deduped_usage_metrics(tmp_path):
    track_claudecode_session(name="usage test")
    uid = _run_uid_file().read_text().strip()

    transcript = _write_transcript_with_usage(tmp_path)
    _transcript_path_file().write_text(str(transcript))
    finish_claudecode_session()

    session_run = ln.Run.get(uid=uid)
    assert session_run.extra_data == {
        "n_tokens": 415,  # (100+20) + (50+30) + (5+200) + (10+0); msg_3 has no usage yet
        "n_steps": 3,  # msg_1, msg_2, msg_3
        "n_tool_calls": 3,  # tool_1, tool_2, and the finish command itself
    }


def test_parallel_sessions_use_separate_state_files(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-a")
    track_claudecode_session(name="session a")
    uid_a = _run_uid_file().read_text().strip()

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-b")
    track_claudecode_session(name="session b")
    uid_b = _run_uid_file().read_text().strip()

    assert uid_a != uid_b
    assert _run_uid_file("session-a").exists()
    assert _run_uid_file("session-b").exists()


def test_track_reuses_transform_across_sessions():
    track_claudecode_session(name="session 1")
    track_claudecode_session(name="session 2")

    assert ln.Transform.filter(key=_TRANSFORM_KEY).count() == 1


def test_finish_without_active_session_exits_cleanly():
    # finish called with no prior track — must not raise
    finish_claudecode_session()


def test_finish_waits_for_delayed_finish_command_write(tmp_path):
    """End-to-end proof (not just the isolated _common helper): if the
    transcript is read before Claude Code has flushed the closing `lamin
    finish` command to disk, finish must wait for it rather than rendering
    an incomplete report."""
    track_claudecode_session(name="delayed write test")
    uid = _run_uid_file().read_text().strip()

    transcript = tmp_path / "session.jsonl"
    for entry in [
        {"role": "user", "content": "do something"},
        {"role": "assistant", "content": "done"},
    ]:
        with transcript.open("a") as f:
            f.write(json.dumps({"message": entry}) + "\n")
    _transcript_path_file().write_text(str(transcript))

    def write_delayed_finish_command():
        time.sleep(0.5)
        entry = {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_finish",
                    "name": "Bash",
                    "input": {"command": "lamin finish"},
                }
            ],
        }
        with transcript.open("a") as f:
            f.write(json.dumps({"message": entry}) + "\n")

    writer = threading.Thread(target=write_delayed_finish_command)
    writer.start()

    start = time.monotonic()
    finish_claudecode_session()
    elapsed = time.monotonic() - start
    writer.join()

    assert (
        0.4 < elapsed < 3.0
    )  # picked up shortly after the write, not the full 8s budget
    session_run = ln.Run.get(uid=uid)
    assert session_run.finished_at is not None
