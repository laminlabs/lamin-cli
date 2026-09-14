import json
import sqlite3
from pathlib import Path

import pytest
from lamin_cli.agents import _common, cursor


def _add_tool(
    conn: sqlite3.Connection,
    conversation_id: str,
    bubble_id: str,
    command: str,
    output: str,
    created_at: str,
) -> None:
    value = {
        "createdAt": created_at,
        "toolFormerData": {
            "name": "run_terminal_command_v2",
            "params": {"command": command},
            "result": json.dumps({"output": output, "exitCode": 0}),
        },
    }
    conn.execute(
        "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
        (f"bubbleId:{conversation_id}:{bubble_id}", json.dumps(value)),
    )


def _make_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value BLOB)")
    return conn


def _write_transcript(path: Path, commands: list[str]) -> None:
    entries = [
        {"role": "user", "message": {"content": [{"type": "text", "text": "hello"}]}},
        {
            "role": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Shell", "input": {"command": command}}
                    for command in commands
                ]
            },
        },
    ]
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))


def test_marker_identifies_chat_and_report_includes_raw_output(tmp_path):
    db = tmp_path / "state.vscdb"
    marker = "a" * 32
    with _make_db(db) as conn:
        _add_tool(
            conn,
            "chat-a",
            "1",
            "lamin track cursor --name test",
            f"{cursor._MARKER_PREFIX}{marker}\n",
            "2026-01-01T00:00:00Z",
        )
        _add_tool(conn, "chat-a", "2", "echo hi", "hi\n", "2026-01-01T00:00:01Z")
        _add_tool(conn, "chat-b", "3", "echo hi", "wrong\n", "2026-01-01T00:00:02Z")

    assert cursor._conversation_id_for_marker(marker, db) == "chat-a"
    transcript = tmp_path / "chat-a.jsonl"
    _write_transcript(transcript, ["lamin track cursor --name test", "echo hi"])
    entries = cursor._parse_transcript(transcript, cursor._shell_outputs("chat-a", db))
    html = _common.render_transcript_html(
        entries,
        is_bookkeeping_bash_cmd=lambda command: False,
        skill_marker="Base directory for this skill:",
        shell_tool_names=cursor._SHELL_TOOL_NAMES,
    )
    assert "hi" in html
    assert "wrong" not in html
    assert cursor._MARKER_PREFIX + marker in html


def test_repeated_commands_keep_their_output_order(tmp_path):
    db = tmp_path / "state.vscdb"
    with _make_db(db) as conn:
        _add_tool(conn, "chat-a", "2", "date", "second", "2026-01-01T00:00:02Z")
        _add_tool(conn, "chat-a", "1", "date", "first", "2026-01-01T00:00:01Z")
    transcript = tmp_path / "chat-a.jsonl"
    _write_transcript(transcript, ["date", "date", "lamin finish"])
    entries = cursor._parse_transcript(transcript, cursor._shell_outputs("chat-a", db))
    results = [
        block["content"]
        for entry in entries
        for block in entry["content"]
        if block.get("type") == "tool_result"
    ]
    assert results == ["first", "second"]
    assert _common.contains_finish_invocation(entries, cursor._SHELL_TOOL_NAMES)


def test_marker_lookup_refuses_missing_or_ambiguous_chats(tmp_path):
    db = tmp_path / "state.vscdb"
    marker = "b" * 32
    with _make_db(db) as conn:
        _add_tool(
            conn,
            "chat-a",
            "1",
            "lamin track cursor",
            f"{cursor._MARKER_PREFIX}{marker}\n",
            "2026-01-01T00:00:00Z",
        )
    with pytest.raises(ValueError, match="found 0"):
        cursor._conversation_id_for_marker("missing", db)
    with sqlite3.connect(db) as conn:
        _add_tool(
            conn,
            "chat-b",
            "2",
            "echo copied marker",
            f"{cursor._MARKER_PREFIX}{marker}\n",
            "2026-01-01T00:00:01Z",
        )
    with pytest.raises(ValueError, match="found 2"):
        cursor._conversation_id_for_marker(marker, db)
