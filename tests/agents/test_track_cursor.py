import json
import sqlite3
from datetime import datetime, timezone

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


def _workspace(fs_path: str, workspace_id: str = "ws") -> dict:
    return {"id": workspace_id, "uri": {"fsPath": fs_path}}


def _set_composer_headers(conn, composers: list[dict]) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ItemTable (key TEXT PRIMARY KEY, value BLOB)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)",
        (
            "composer.composerHeaders",
            json.dumps({"allComposers": composers}),
        ),
    )


def _set_composer_data(conn, conversation_id, bubble_ids: list[str]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO cursorDiskKV (key, value) VALUES (?, ?)",
        (
            f"composerData:{conversation_id}",
            json.dumps(
                {
                    "fullConversationHeadersOnly": [
                        {"bubbleId": bubble_id} for bubble_id in bubble_ids
                    ]
                }
            ),
        ),
    )


def _composer_header(
    composer_id,
    *,
    archived=False,
    draft=False,
    fs_path=None,
    created_at=1,
):
    header = {
        "composerId": composer_id,
        "isArchived": archived,
        "isDraft": draft,
        "createdAt": created_at,
    }
    if fs_path is not None:
        header["workspaceIdentifier"] = _workspace(fs_path, composer_id)
    return header


def _add_session_marker(conn, conversation_id, marker):
    _add_bubble(
        conn,
        conversation_id,
        f"marker-{conversation_id}",
        "2026-01-01T00:00:00Z",
        tool_name="run_terminal_command_v2",
        params={"command": f"echo LAMIN_CURSOR_SESSION_ID={marker}"},
        result={"output": f"LAMIN_CURSOR_SESSION_ID={marker}\n"},
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
    monkeypatch.chdir(project)
    monkeypatch.setattr(cursor, "_state_dir", lambda: project / ".cursor")
    monkeypatch.setattr(cursor, "_cursor_db_path", lambda: db)
    yield db, project

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


def test_full_track_finish_flow(isolated, monkeypatch):
    db, _ = isolated
    monkeypatch.setenv("LAMIN_CURSOR_SESSION_ID", "marker-a")
    with sqlite3.connect(db) as conn:
        _add_session_marker(conn, "chat-a", "marker-a")
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


def test_follow_up_reuses_run_and_replaces_report(isolated, monkeypatch):
    db, _ = isolated
    monkeypatch.setenv("LAMIN_CURSOR_SESSION_ID", "marker-a")
    with sqlite3.connect(db) as conn:
        _add_session_marker(conn, "chat-a", "marker-a")
        _add_conversation_content(conn, "chat-a", "1", "First task", "first")
    cursor.track_cursor_session(name="first task")
    uid = cursor._run_uid_file("chat-a").read_text().strip()
    cursor.finish_cursor_session()
    run = ln.Run.get(uid=uid)
    report_uid, first_hash = run.report.uid, run.report.hash

    with sqlite3.connect(db) as conn:
        _add_conversation_content(
            conn, "chat-a", "3", "Follow-up task", "updated output"
        )
    cursor.track_cursor_session(name="follow-up task")
    assert cursor._run_uid_file("chat-a").read_text().strip() == uid
    assert ln.Run.get(uid=uid).finished_at is None
    cursor.finish_cursor_session()

    run = ln.Run.get(uid=uid)
    assert run.finished_at is not None
    assert run.report.uid == report_uid
    assert run.report.hash != first_hash
    report = run.report.path.read_text()
    assert "Follow-up task" in report
    assert "updated output" in report
    assert run.transform.runs.count() == 1


def test_parallel_sessions_never_cross_match(isolated, monkeypatch):
    db, _ = isolated
    with sqlite3.connect(db) as conn:
        _add_session_marker(conn, "chat-a", "marker-a")
        _add_conversation_content(conn, "chat-a", "1", "Task A", "output A")
        _add_session_marker(conn, "chat-b", "marker-b")
        _add_conversation_content(conn, "chat-b", "2", "Task B", "output B")

    monkeypatch.setenv("LAMIN_CURSOR_SESSION_ID", "marker-a")
    cursor.track_cursor_session(name="session a")
    uid_a = cursor._run_uid_file("chat-a").read_text().strip()
    monkeypatch.setenv("LAMIN_CURSOR_SESSION_ID", "marker-b")
    cursor.track_cursor_session(name="session b")
    uid_b = cursor._run_uid_file("chat-b").read_text().strip()
    assert uid_a != uid_b

    monkeypatch.setenv("LAMIN_CURSOR_SESSION_ID", "marker-b")
    cursor.finish_cursor_session()
    report_b = ln.Run.get(uid=uid_b).report.path.read_text()
    assert "output B" in report_b
    assert "output A" not in report_b

    monkeypatch.setenv("LAMIN_CURSOR_SESSION_ID", "marker-a")
    cursor.finish_cursor_session()
    report_a = ln.Run.get(uid=uid_a).report.path.read_text()
    assert "output A" in report_a
    assert "output B" not in report_a


def test_worktree_copy_prefers_live_chat_over_archived(isolated, monkeypatch):
    db, project = isolated
    monkeypatch.setenv("LAMIN_CURSOR_SESSION_ID", "marker-a")
    with sqlite3.connect(db) as conn:
        _add_session_marker(conn, "chat-archived", "marker-a")
        _add_session_marker(conn, "chat-live", "marker-a")
        _add_bubble(
            conn,
            "chat-new",
            "track",
            "2026-01-01T00:00:10Z",
            tool_name="run_terminal_command_v2",
            params={
                "command": "LAMIN_CURSOR_SESSION_ID=marker-a lamin track cursor --name x"
            },
            result={"output": "started tracking\n"},
        )
        _add_bubble(
            conn,
            "chat-paste",
            "user",
            "2026-01-01T00:00:11Z",
            text="LAMIN_CURSOR_SESSION_ID=marker-a lamin track cursor",
            bubble_type=1,
        )
        _set_composer_headers(
            conn,
            [
                _composer_header(
                    "chat-archived",
                    archived=True,
                    fs_path=str(project.parent),
                    created_at=1,
                ),
                _composer_header("chat-live", fs_path=str(project), created_at=2),
                _composer_header("chat-new", fs_path=str(project), created_at=3),
                _composer_header("chat-paste", fs_path=str(project.parent)),
            ],
        )
    assert cursor._conversation_id_for_session("marker-a") == "chat-live"
    cursor.track_cursor_session(name="worktree copy")
    assert cursor._run_uid_file("chat-live").exists()
    assert not cursor._run_uid_file("chat-archived").exists()


def test_two_live_chats_prefer_workspace_matching_cwd(isolated):
    db, project = isolated
    sibling = project.parent / "other-workspace"
    sibling.mkdir()
    with sqlite3.connect(db) as conn:
        _add_session_marker(conn, "chat-here", "marker-a")
        _add_session_marker(conn, "chat-elsewhere", "marker-a")
        _set_composer_headers(
            conn,
            [
                _composer_header("chat-here", fs_path=str(project)),
                _composer_header("chat-elsewhere", fs_path=str(sibling)),
            ],
        )
    assert cursor._conversation_id_for_session("marker-a") == "chat-here"


def test_echo_command_identifies_session_before_result(isolated):
    db, _ = isolated
    with sqlite3.connect(db) as conn:
        _add_bubble(
            conn,
            "chat-a",
            "echo",
            "2026-01-01T00:00:00Z",
            tool_name="run_terminal_command_v2",
            params={"command": "echo LAMIN_CURSOR_SESSION_ID=marker-a"},
        )
    assert cursor._conversation_id_for_session("marker-a") == "chat-a"


def test_transcript_follows_conversation_header_order(isolated):
    db, _ = isolated
    with sqlite3.connect(db) as conn:
        _add_bubble(
            conn, "chat-a", "stale", "2026-01-01T00:00:00Z", text="stale leftover"
        )
        _add_bubble(
            conn,
            "chat-a",
            "asst",
            "2026-01-01T00:00:01Z",
            text="assistant first by time",
        )
        _add_bubble(
            conn,
            "chat-a",
            "user",
            "2026-01-01T00:00:09Z",
            text="user prompt",
            bubble_type=1,
        )
        _set_composer_data(conn, "chat-a", ["user", "asst"])
    texts = [
        block["text"]
        for entry in cursor._parse_sqlite_conversation("chat-a")
        for block in entry["content"]
        if block.get("type") == "text"
    ]
    assert texts[0] == "user prompt"
    assert "stale leftover" not in texts
