import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import lamindb as ln
import pytest
from lamin_cli.agents import cursor


def _add_bubble(
    conn,
    conversation_id,
    bubble_id,
    created_at,
    *,
    text="",
    bubble_type=2,
    tool_name=None,
    params=None,
    result=None,
    started_at_ms=None,
):
    value = {"type": bubble_type, "text": text, "createdAt": created_at}
    if tool_name is not None:
        value["toolFormerData"] = {
            "name": tool_name,
            "status": "loading",
            "params": json.dumps(params or {}),
            "additionalData": {"startedAtMs": started_at_ms},
        }
        if result is not None:
            value["toolFormerData"]["result"] = json.dumps(result)
    conn.execute(
        "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
        (f"bubbleId:{conversation_id}:{bubble_id}", json.dumps(value)),
    )


def _add_invocation(conn, conversation_id, bubble_id, arguments, started_at_ms):
    command = "lamin " + " ".join(arguments)
    _add_bubble(
        conn,
        conversation_id,
        bubble_id,
        f"2026-01-01T00:00:{started_at_ms // 1000:02d}Z",
        tool_name="run_terminal_command_v2",
        params={"command": command, "cwd": str(Path.cwd())},
        started_at_ms=started_at_ms,
    )


def _add_conversation_content(conn, conversation_id, suffix, user_text, output):
    _add_bubble(
        conn,
        conversation_id,
        f"user-{suffix}",
        f"2026-01-01T00:00:{suffix}1Z",
        text=user_text,
        bubble_type=1,
    )
    _add_bubble(
        conn,
        conversation_id,
        f"assistant-{suffix}",
        f"2026-01-01T00:00:{suffix}2Z",
        text=f"Working on {user_text}",
    )
    _add_bubble(
        conn,
        conversation_id,
        f"shell-{suffix}",
        f"2026-01-01T00:00:{suffix}3Z",
        tool_name="run_terminal_command_v2",
        params={"command": f"echo {conversation_id}"},
        result={"output": output, "exitCode": 0},
        started_at_ms=int(suffix) * 1000 + 300,
    )


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    db = tmp_path / "state.vscdb"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value BLOB)")
    clock = [1000]
    monkeypatch.chdir(project)
    monkeypatch.setattr(cursor, "_state_dir", lambda: project / ".cursor")
    monkeypatch.setattr(cursor, "_cursor_db_path", lambda: db)
    monkeypatch.setattr(cursor, "_now_ms", lambda: clock[0])
    yield db, project, clock

    transform = ln.Transform.filter(key=cursor._TRANSFORM_KEY).first()
    if transform is not None:
        for run in transform.runs.all():
            report = run.report
            if report is not None:
                run.report = None
                run.save()
            run.delete(permanent=True)
            if report is not None:
                report.delete(permanent=True)
        transform.delete(permanent=True)


def test_full_track_finish_flow(isolated):
    db, _, clock = isolated
    with sqlite3.connect(db) as conn:
        _add_invocation(conn, "chat-a", "track", ("track", "cursor"), 1000)
    cursor.track_cursor_session(name="integration test")
    uid_file = cursor._run_uid_file("chat-a")
    uid = uid_file.read_text().strip()
    run = ln.Run.get(uid=uid)
    assert run.finished_at is None

    child_transform = ln.Transform(key="analysis.py", kind="script").save()
    child_run = ln.Run(child_transform, initiated_by_run=run)
    child_run.finished_at = datetime.now(timezone.utc)
    child_run.save()

    with sqlite3.connect(db) as conn:
        _add_conversation_content(
            conn, "chat-a", "1", "Create a FASTA file.", "raw shell output\n"
        )
        _add_invocation(conn, "chat-a", "finish", ("finish",), 20000)
    clock[0] = 20000
    cursor.finish_cursor_session()

    assert not uid_file.exists()
    run = ln.Run.get(uid=uid)
    assert run.finished_at is not None
    assert run.report is not None
    report = run.report.path.read_text()
    assert "Create a FASTA file." in report
    assert "raw shell output" in report
    assert ln.Transform.get(key="analysis.py").run.uid == uid

    child_run.delete(permanent=True)
    child_transform.delete(permanent=True)


def test_follow_up_reuses_run_and_replaces_report(isolated):
    db, _, clock = isolated
    with sqlite3.connect(db) as conn:
        _add_invocation(conn, "chat-a", "track-1", ("track", "cursor"), 1000)
        _add_conversation_content(conn, "chat-a", "1", "First task", "first")
    cursor.track_cursor_session(name="first task")
    uid = cursor._run_uid_file("chat-a").read_text().strip()
    with sqlite3.connect(db) as conn:
        _add_invocation(conn, "chat-a", "finish-1", ("finish",), 20000)
    clock[0] = 20000
    cursor.finish_cursor_session()
    run = ln.Run.get(uid=uid)
    report_uid, first_hash = run.report.uid, run.report.hash

    with sqlite3.connect(db) as conn:
        _add_invocation(conn, "chat-a", "track-2", ("track", "cursor"), 30000)
        _add_conversation_content(
            conn, "chat-a", "3", "Follow-up task", "updated output"
        )
    clock[0] = 30000
    cursor.track_cursor_session(name="follow-up task")
    assert cursor._run_uid_file("chat-a").read_text().strip() == uid
    assert ln.Run.get(uid=uid).finished_at is None
    with sqlite3.connect(db) as conn:
        _add_invocation(conn, "chat-a", "finish-2", ("finish",), 40000)
    clock[0] = 40000
    cursor.finish_cursor_session()

    run = ln.Run.get(uid=uid)
    assert run.finished_at is not None
    assert run.report.uid == report_uid
    assert run.report.hash != first_hash
    report = run.report.path.read_text()
    assert "Follow-up task" in report
    assert "updated output" in report
    assert run.transform.runs.count() == 1


def test_parallel_sessions_never_cross_match(isolated):
    db, _, clock = isolated
    with sqlite3.connect(db) as conn:
        _add_invocation(conn, "chat-a", "track", ("track", "cursor"), 1000)
        _add_conversation_content(conn, "chat-a", "1", "Task A", "output A")
        _add_invocation(conn, "chat-b", "track", ("track", "cursor"), 20000)
        _add_conversation_content(conn, "chat-b", "2", "Task B", "output B")

    cursor.track_cursor_session(name="session a")
    uid_a = cursor._run_uid_file("chat-a").read_text().strip()
    clock[0] = 20000
    cursor.track_cursor_session(name="session b")
    uid_b = cursor._run_uid_file("chat-b").read_text().strip()
    assert uid_a != uid_b

    with sqlite3.connect(db) as conn:
        _add_invocation(conn, "chat-b", "finish", ("finish",), 30000)
    clock[0] = 30000
    cursor.finish_cursor_session()
    report_b = ln.Run.get(uid=uid_b).report.path.read_text()
    assert "output B" in report_b
    assert "output A" not in report_b

    with sqlite3.connect(db) as conn:
        _add_invocation(conn, "chat-a", "finish", ("finish",), 40000)
    clock[0] = 40000
    cursor.finish_cursor_session()
    report_a = ln.Run.get(uid=uid_a).report.path.read_text()
    assert "output A" in report_a
    assert "output B" not in report_a
