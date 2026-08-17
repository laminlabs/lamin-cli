import itertools
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
import lamindb as ln
import pytest
from lamin_cli.agents import copilot as copilot_agent
from lamin_cli.agents.copilot import (
    _TRANSFORM_KEY,
    _run_uid_file,
    finish_copilot_session,
    track_copilot_session,
)

# Fake clock so timestamps in a written transcript are deterministic and
# monotonically increasing, independent of real wall-clock time.
_fake_clock = itertools.count()


def _next_timestamp() -> str:
    dt = datetime.now(timezone.utc) + timedelta(milliseconds=10 * next(_fake_clock))
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _write_transcript(state_dir: Path, session_id: str, events: list[dict]) -> None:
    session_dir = state_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    with (session_dir / "events.jsonl").open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _write_full_transcript(state_dir: Path, session_id: str) -> None:
    """A realistic user/assistant exchange for report rendering.

    Includes a tool call and `outputTokens` on the assistant messages, since
    `outputTokens` is the only token field Copilot persists to events.jsonl
    before actual CLI shutdown — used to verify usage-metric extraction.
    """
    now = _next_timestamp()
    events = [
        {
            "type": "user.message",
            "data": {"content": "do something"},
            "id": "u1",
            "timestamp": now,
            "parentId": None,
        },
        {
            "type": "assistant.message",
            "data": {
                "content": "on it",
                "toolRequests": [
                    {
                        "toolCallId": "t1",
                        "name": "bash",
                        "arguments": {"command": "echo hi"},
                    }
                ],
                "outputTokens": 15,
            },
            "id": "a1",
            "timestamp": now,
            "parentId": "u1",
        },
        {
            "type": "tool.execution_start",
            "data": {
                "toolCallId": "t1",
                "toolName": "bash",
                "arguments": {"command": "echo hi"},
            },
            "id": "tc1",
            "timestamp": now,
            "parentId": "a1",
        },
        {
            "type": "tool.execution_complete",
            "data": {"toolCallId": "t1", "success": True, "result": {"content": "hi"}},
            "id": "tc2",
            "timestamp": now,
            "parentId": "tc1",
        },
        {
            "type": "assistant.message",
            "data": {"content": "done", "toolRequests": [], "outputTokens": 25},
            "id": "a2",
            "timestamp": now,
            "parentId": "tc2",
        },
        {
            "type": "assistant.message",
            "data": {
                "content": "closing session",
                "toolRequests": [
                    {
                        "toolCallId": "t2",
                        "name": "bash",
                        "arguments": {"command": "lamin finish"},
                    }
                ],
                "outputTokens": 0,
            },
            "id": "a3",
            "timestamp": now,
            "parentId": "a2",
        },
        {
            "type": "tool.execution_start",
            "data": {
                "toolCallId": "t2",
                "toolName": "bash",
                "arguments": {"command": "lamin finish"},
            },
            "id": "tc3",
            "timestamp": now,
            "parentId": "a3",
        },
    ]
    _write_transcript(state_dir, session_id, events)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    copilot_home = tmp_path / "copilot_home"
    state_dir = copilot_home / "session-state"
    state_dir.mkdir(parents=True)

    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(copilot_agent, "_copilot_session_state_dir", lambda: state_dir)
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)

    yield state_dir, project_dir

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


def test_full_track_finish_flow(isolated, monkeypatch):
    state_dir, project_dir = isolated
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session-a")

    track_copilot_session(name="integration test")

    uid_file = _run_uid_file("session-a")
    assert uid_file.exists()
    uid = uid_file.read_text().strip()
    session_run = ln.Run.get(uid=uid)
    assert session_run.finished_at is None

    # simulate a self-tracking script run with LAMIN_INITIATED_BY_RUN_UID set
    child_transform = ln.Transform(key="analysis.py", kind="script").save()
    child_run = ln.Run(child_transform, initiated_by_run=session_run)
    child_run.finished_at = datetime.now(timezone.utc)
    child_run.save()

    _write_full_transcript(state_dir, "session-a")
    finish_copilot_session()

    assert not uid_file.exists()
    session_run = ln.Run.get(uid=uid)
    assert session_run.finished_at is not None
    assert session_run.report is not None

    child_transform = ln.Transform.get(key="analysis.py")
    assert child_transform.run is not None
    assert child_transform.run.uid == uid

    child_run.delete(permanent=True)
    child_transform.delete(permanent=True)


def test_finish_extracts_output_only_usage_metrics(isolated, monkeypatch):
    state_dir, project_dir = isolated
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session-a")

    track_copilot_session(name="usage test")
    uid = _run_uid_file("session-a").read_text().strip()

    _write_full_transcript(state_dir, "session-a")
    finish_copilot_session()

    session_run = ln.Run.get(uid=uid)
    # n_tokens is an output-tokens-only lower bound here (not a full billed
    # total like Claude Code's), since Copilot only persists full input/cache
    # token accounting to events.jsonl in "session.shutdown", which hasn't
    # fired yet at `lamin finish` time.
    assert session_run.extra_data == {
        "n_tokens": 40,  # 15 (a1) + 25 (a2) output tokens; a3 (finish) has 0
        "n_steps": 3,  # a1, a2, a3
        "n_tool_calls": 2,  # "echo hi", "lamin finish"
    }


def test_multiple_sessions_use_separate_state_files(isolated, monkeypatch):
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session-a")
    track_copilot_session(name="session a")
    uid_a = _run_uid_file("session-a").read_text().strip()

    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session-b")
    track_copilot_session(name="session b")
    uid_b = _run_uid_file("session-b").read_text().strip()

    assert uid_a != uid_b
    assert _run_uid_file("session-a").exists()
    assert _run_uid_file("session-b").exists()


def test_track_reuses_transform_across_sessions(isolated, monkeypatch):
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session-a")
    track_copilot_session(name="session a")

    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session-b")
    track_copilot_session(name="session b")

    assert ln.Transform.filter(key=_TRANSFORM_KEY).count() == 1


def test_two_parallel_sessions_never_cross_match(isolated, monkeypatch):
    """The whole point of reading $COPILOT_AGENT_SESSION_ID directly: each
    session's track/finish call only ever sees its own environment, so two
    genuinely simultaneous sessions structurally cannot resolve to each
    other's identity -- there's no shared log or heuristic left to race on."""
    state_dir, project_dir = isolated

    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session-a")
    track_copilot_session(name="task A")
    uid_a = _run_uid_file("session-a").read_text().strip()

    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session-b")
    track_copilot_session(name="task B")
    uid_b = _run_uid_file("session-b").read_text().strip()

    assert uid_a != uid_b

    _write_full_transcript(state_dir, "session-b")
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session-b")
    finish_copilot_session()

    assert not _run_uid_file("session-b").exists()
    assert _run_uid_file("session-a").exists()  # untouched
    assert ln.Run.get(uid=uid_b).finished_at is not None
    assert ln.Run.get(uid=uid_a).finished_at is None

    ln.Run.get(uid=uid_a).delete(permanent=True)


def test_track_hard_errors_without_session_id_env_var(isolated):
    with pytest.raises(click.ClickException, match="COPILOT_AGENT_SESSION_ID"):
        track_copilot_session(name="no env var")


def test_finish_hard_errors_without_session_id_env_var(isolated):
    with pytest.raises(click.ClickException, match="COPILOT_AGENT_SESSION_ID"):
        finish_copilot_session()


def test_finish_with_session_id_env_but_nothing_tracked_exits_cleanly(
    isolated, monkeypatch
):
    """$COPILOT_AGENT_SESSION_ID is set (this really is a Copilot session),
    but track was never called (e.g. the user declined tracking) -- a
    normal no-op, not an error."""
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session-a")
    finish_copilot_session()


def test_track_without_name_still_works(isolated, monkeypatch):
    """--name is a label only now, not a resolution key -- optional."""
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session-a")
    track_copilot_session()
    assert _run_uid_file("session-a").exists()


def test_finish_waits_for_delayed_finish_command_write(isolated, monkeypatch):
    """End-to-end proof (not just the isolated _common helper): if the
    transcript is read before Copilot has flushed the closing `lamin finish`
    command to disk, finish must wait for it rather than rendering an
    incomplete report."""
    state_dir, project_dir = isolated
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session-a")

    track_copilot_session(name="delayed write test")
    uid = _run_uid_file("session-a").read_text().strip()

    _write_transcript(
        state_dir,
        "session-a",
        [
            {
                "type": "user.message",
                "data": {"content": "do something"},
                "id": "u1",
                "timestamp": _next_timestamp(),
                "parentId": None,
            },
        ],
    )
    events_path = state_dir / "session-a" / "events.jsonl"

    def write_delayed_finish_command():
        time.sleep(0.5)
        # _build_entries only turns toolRequests on an assistant.message into
        # tool_use blocks -- a bare tool.execution_start alone isn't enough.
        event = {
            "type": "assistant.message",
            "data": {
                "content": "closing session",
                "toolRequests": [
                    {
                        "toolCallId": "tf",
                        "name": "bash",
                        "arguments": {"command": "lamin finish"},
                    }
                ],
            },
            "id": "af",
            "timestamp": _next_timestamp(),
            "parentId": "u1",
        }
        with events_path.open("a") as f:
            f.write(json.dumps(event) + "\n")

    writer = threading.Thread(target=write_delayed_finish_command)
    writer.start()

    start = time.monotonic()
    finish_copilot_session()
    elapsed = time.monotonic() - start
    writer.join()

    assert 0.4 < elapsed < 3.0  # picked up shortly after the write, not the full 8s budget
    session_run = ln.Run.get(uid=uid)
    assert session_run.finished_at is not None
