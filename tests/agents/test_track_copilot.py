import json
from datetime import datetime, timezone
from pathlib import Path

import lamindb as ln
import pytest
from lamin_cli.agents import copilot as copilot_agent
from lamin_cli.agents.copilot import (
    _STATE_DIR,
    _TRANSFORM_KEY,
    _run_uid_file,
    finish_copilot_session,
    track_copilot_session,
)


def _write_fake_session(state_dir: Path, session_id: str, cwd: str, command_texts: list[str]) -> None:
    session_dir = state_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    (session_dir / "workspace.yaml").write_text(
        f"id: {session_id}\ncwd: {cwd}\nclient_name: vscode\nname: test\ncreated_at: {now}\nupdated_at: {now}\n"
    )
    lines = [
        json.dumps({"type": "session.start", "data": {"sessionId": session_id, "context": {"cwd": cwd}}, "id": "e0", "timestamp": now, "parentId": None})
    ]
    for i, cmd in enumerate(command_texts):
        lines.append(
            json.dumps(
                {
                    "type": "tool.execution_start",
                    "data": {"toolCallId": f"call{i}", "toolName": "bash", "arguments": {"command": cmd}},
                    "id": f"e{i + 1}",
                    "timestamp": now,
                    "parentId": "e0",
                }
            )
        )
    (session_dir / "events.jsonl").write_text("\n".join(lines) + "\n")


def _append_event(state_dir: Path, session_id: str, command_text: str) -> None:
    """Add another tool.execution_start to an existing fake session, e.g. for finish."""
    session_dir = state_dir / session_id
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with (session_dir / "events.jsonl").open("a") as f:
        f.write(
            json.dumps(
                {
                    "type": "tool.execution_start",
                    "data": {"toolCallId": "call-finish", "toolName": "bash", "arguments": {"command": command_text}},
                    "id": "e-finish",
                    "timestamp": now,
                    "parentId": "e0",
                }
            )
            + "\n"
        )


def _write_full_transcript(state_dir: Path, session_id: str) -> None:
    """Give a session a realistic user/assistant exchange for report rendering."""
    session_dir = state_dir / session_id
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    events = [
        {"type": "user.message", "data": {"content": "do something"}, "id": "u1", "timestamp": now, "parentId": None},
        {"type": "assistant.message", "data": {"content": "done", "toolRequests": []}, "id": "a1", "timestamp": now, "parentId": "u1"},
    ]
    with (session_dir / "events.jsonl").open("a") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    copilot_home = tmp_path / "copilot_home"
    state_dir = copilot_home / "session-state"
    state_dir.mkdir(parents=True)

    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(copilot_agent, "_copilot_session_state_dir", lambda: state_dir)

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


def test_full_track_finish_flow(isolated):
    state_dir, project_dir = isolated
    cwd = str(project_dir)

    _write_fake_session(state_dir, "session-a", cwd, ["lamin track copilot --name integration-test"])
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
    _append_event(state_dir, "session-a", "lamin track finish")
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


def test_multiple_sessions_use_separate_state_files(isolated):
    state_dir, project_dir = isolated
    cwd = str(project_dir)

    _write_fake_session(state_dir, "session-a", cwd, ["lamin track copilot --name a"])
    track_copilot_session(name="session a")
    uid_a = _run_uid_file("session-a").read_text().strip()

    _write_fake_session(state_dir, "session-b", cwd, ["lamin track copilot --name b"])
    track_copilot_session(name="session b")
    uid_b = _run_uid_file("session-b").read_text().strip()

    assert uid_a != uid_b
    assert _run_uid_file("session-a").exists()
    assert _run_uid_file("session-b").exists()


def test_track_reuses_transform_across_sessions(isolated):
    state_dir, project_dir = isolated
    cwd = str(project_dir)

    _write_fake_session(state_dir, "session-a", cwd, ["lamin track copilot --name a"])
    track_copilot_session(name="session a")

    _write_fake_session(state_dir, "session-b", cwd, ["lamin track copilot --name b"])
    track_copilot_session(name="session b")

    assert ln.Transform.filter(key=_TRANSFORM_KEY).count() == 1


def test_finish_disambiguates_via_self_invocation_when_multiple_sessions_active(isolated):
    state_dir, project_dir = isolated
    cwd = str(project_dir)

    _write_fake_session(state_dir, "session-a", cwd, ["lamin track copilot --name a"])
    track_copilot_session(name="session a")
    uid_a = _run_uid_file("session-a").read_text().strip()

    _write_fake_session(state_dir, "session-b", cwd, ["lamin track copilot --name b"])
    track_copilot_session(name="session b")
    uid_b = _run_uid_file("session-b").read_text().strip()

    # both sessions are now tracked in the same directory; only session-b
    # logs a "lamin track finish" — session-a must be left untouched.
    _write_full_transcript(state_dir, "session-b")
    _append_event(state_dir, "session-b", "lamin track finish")
    finish_copilot_session()

    assert not _run_uid_file("session-b").exists()
    assert _run_uid_file("session-a").exists()  # untouched
    assert ln.Run.get(uid=uid_b).finished_at is not None
    assert ln.Run.get(uid=uid_a).finished_at is None

    # cleanup session-a's still-open run
    ln.Run.get(uid=uid_a).delete(permanent=True)


def test_finish_without_active_session_exits_cleanly(isolated):
    finish_copilot_session()
