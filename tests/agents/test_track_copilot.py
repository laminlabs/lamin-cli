import itertools
import json
import os
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

# Real Copilot events have millisecond timestamps, which is what
# `_resolve_session_via_self_invocation` relies on to break ties between
# sessions whose logged commands both match the same generic search text.
# A plain `datetime.now()` truncated to whole seconds (as opposed to real
# sub-second precision) can make two fixture-written events land on the
# identical string, which makes resolution order depend on filesystem
# iteration order instead of recency. Use a monotonically increasing fake
# clock instead so ordering between fixture calls is always deterministic.
_fake_clock = itertools.count()


def _next_timestamp() -> str:
    dt = datetime.now(timezone.utc) + timedelta(milliseconds=10 * next(_fake_clock))
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _write_fake_session(
    state_dir: Path, session_id: str, cwd: str, command_texts: list[str]
) -> None:
    session_dir = state_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    now = _next_timestamp()
    (session_dir / "workspace.yaml").write_text(
        f"id: {session_id}\ncwd: {cwd}\nclient_name: vscode\nname: test\ncreated_at: {now}\nupdated_at: {now}\n"
    )
    lines = [
        json.dumps(
            {
                "type": "session.start",
                "data": {"sessionId": session_id, "context": {"cwd": cwd}},
                "id": "e0",
                "timestamp": now,
                "parentId": None,
            }
        )
    ]
    for i, cmd in enumerate(command_texts):
        lines.append(
            json.dumps(
                {
                    "type": "tool.execution_start",
                    "data": {
                        "toolCallId": f"call{i}",
                        "toolName": "bash",
                        "arguments": {"command": cmd},
                    },
                    "id": f"e{i + 1}",
                    "timestamp": _next_timestamp(),
                    "parentId": "e0",
                }
            )
        )
    (session_dir / "events.jsonl").write_text("\n".join(lines) + "\n")


def _append_event(state_dir: Path, session_id: str, command_text: str) -> None:
    """Add another tool.execution_start to an existing fake session, e.g. for finish."""
    session_dir = state_dir / session_id
    now = _next_timestamp()
    with (session_dir / "events.jsonl").open("a") as f:
        f.write(
            json.dumps(
                {
                    "type": "tool.execution_start",
                    "data": {
                        "toolCallId": "call-finish",
                        "toolName": "bash",
                        "arguments": {"command": command_text},
                    },
                    "id": "e-finish",
                    "timestamp": now,
                    "parentId": "e0",
                }
            )
            + "\n"
        )


def _write_full_transcript(state_dir: Path, session_id: str) -> None:
    """Give a session a realistic user/assistant exchange for report rendering.

    Includes a tool call and `outputTokens` on the assistant messages, since
    `outputTokens` is the only token field Copilot persists to events.jsonl
    before actual CLI shutdown — used to verify usage-metric extraction.
    """
    session_dir = state_dir / session_id
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

    _write_fake_session(
        state_dir, "session-a", cwd, ["lamin track copilot --name integration-test"]
    )
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


def test_finish_extracts_output_only_usage_metrics(isolated):
    # single active session: finish resolves it directly without needing a
    # self-invocation "lamin track finish" bookkeeping event, keeping the
    # tool-call count easy to reason about.
    state_dir, project_dir = isolated
    cwd = str(project_dir)

    _write_fake_session(
        state_dir, "session-a", cwd, ["lamin track copilot --name usage-test"]
    )
    track_copilot_session(name="usage test")
    uid = _run_uid_file("session-a").read_text().strip()

    _write_full_transcript(state_dir, "session-a")
    finish_copilot_session()

    session_run = ln.Run.get(uid=uid)
    # n_tokens is an output-tokens-only lower bound here (not a full billed
    # total like Claude Code's), since Copilot only persists full input/cache
    # token accounting to events.jsonl in "session.shutdown", which hasn't
    # fired yet at `lamin track finish` time.
    assert session_run.extra_data == {
        "n_tokens": 40,  # 15 (a1) + 25 (a2) output tokens
        "n_steps": 2,  # a1, a2
        "n_tool_calls": 2,  # the "lamin track copilot" bookkeeping call + "echo hi"
    }


def test_resolve_finds_own_event_buried_past_a_fixed_line_count(isolated):
    """A single visible tool call logs several internal events (hooks,
    permission prompts, ...), so by the time resolution actually runs, many
    more events can have piled up after the target one in the SAME
    session's own log — a fixed line-count tail must not cause a session to
    miss its own very recent event (which previously fell back to the
    cwd-only workspace scan and could resolve to a different session
    entirely)."""
    state_dir, project_dir = isolated
    cwd = str(project_dir)

    target_name = "Create a text file of all divisors of 360 and save it as a LaminDB artifact."
    _write_fake_session(
        state_dir, "session-a", cwd, [f'lamin track copilot --name "{target_name}"']
    )
    # pile on far more than the old fixed 50-line tail window, all still
    # well within the 5s time window (10ms apart via the fake clock)
    for i in range(80):
        _append_event(state_dir, "session-a", f"some other internal event {i}")

    # a second, unrelated session sharing the same cwd, created slightly
    # later -- if resolution wrongly falls back to the cwd-only workspace
    # scan, it lands here instead of session-a.
    _write_fake_session(state_dir, "session-b", cwd, ["lamin track copilot --name b"])

    assert copilot_agent._resolve_session_via_self_invocation(target_name) == "session-a"


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


def test_track_disambiguates_parallel_sessions_via_distinct_names(isolated):
    """Two sessions both calling `lamin track copilot` within the same
    window must not collide just because both commands share the generic
    "track copilot" substring — matching on the mandated, distinct --name
    text instead must correctly resolve each to its own session, even
    though both sessions' events are already on disk when each resolves
    (simulating genuine overlap, not a sequential write-lag race)."""
    state_dir, project_dir = isolated
    cwd = str(project_dir)

    _write_fake_session(
        state_dir,
        "session-a",
        cwd,
        ['lamin track copilot --name "sum of squares of first 50 even numbers"'],
    )
    _write_fake_session(
        state_dir,
        "session-b",
        cwd,
        ['lamin track copilot --name "20th fibonacci number"'],
    )

    track_copilot_session(name="sum of squares of first 50 even numbers")
    assert _run_uid_file("session-a").exists()
    assert not _run_uid_file("session-b").exists()

    track_copilot_session(name="20th fibonacci number")
    assert _run_uid_file("session-b").exists()

    uid_a = _run_uid_file("session-a").read_text().strip()
    uid_b = _run_uid_file("session-b").read_text().strip()
    assert uid_a != uid_b


def test_track_reuses_transform_across_sessions(isolated):
    state_dir, project_dir = isolated
    cwd = str(project_dir)

    _write_fake_session(state_dir, "session-a", cwd, ["lamin track copilot --name a"])
    track_copilot_session(name="session a")

    _write_fake_session(state_dir, "session-b", cwd, ["lamin track copilot --name b"])
    track_copilot_session(name="session b")

    assert ln.Transform.filter(key=_TRANSFORM_KEY).count() == 1


def test_finish_disambiguates_via_self_invocation_when_multiple_sessions_active(
    isolated,
):
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


def test_resolve_session_retries_until_delayed_write_appears(isolated):
    """Simulates the write-lag race: the session's own event isn't on disk yet
    when resolution starts, appears partway through the retry window, and
    should be picked up as soon as it lands rather than requiring the full
    retry budget or failing outright."""
    state_dir, project_dir = isolated
    cwd = str(project_dir)

    def write_delayed_session():
        time.sleep(0.5)
        _write_fake_session(state_dir, "delayed-session", cwd, ["lamin finish"])

    writer = threading.Thread(target=write_delayed_session)
    writer.start()

    start = time.monotonic()
    result = copilot_agent._resolve_session(
        "lamin finish", retry_total_seconds=3.0, retry_interval_seconds=0.1
    )
    elapsed = time.monotonic() - start
    writer.join()

    assert result == "delayed-session"
    assert 0.4 < elapsed < 2.0  # picked up shortly after the write landed, not the full budget


def test_resolve_session_gives_up_after_retry_deadline(isolated):
    """If nothing ever appears, resolution must still return None promptly at
    the deadline rather than hanging indefinitely."""
    start = time.monotonic()
    result = copilot_agent._resolve_session(
        "lamin finish", retry_total_seconds=0.5, retry_interval_seconds=0.1
    )
    elapsed = time.monotonic() - start

    assert result is None
    assert 0.4 < elapsed < 1.0


def test_finish_ignores_stale_run_uid_files_without_deleting_them(isolated):
    """A run_uid file left behind by a past hard error/crash must not force
    log-based resolution (or worse, wrongly route an unrelated plain
    shell-script finish into the Copilot branch) — it should be excluded
    from candidate counts. But it must NOT be deleted: its age only measures
    time since `lamin track copilot` ran, not time since the session was
    last active, so an unrelated finish call must never be able to destroy
    another still-open session's bookkeeping just because it's old."""
    state_dir, project_dir = isolated
    cwd = str(project_dir)

    _write_fake_session(state_dir, "session-a", cwd, ["lamin track copilot --name a"])
    track_copilot_session(name="session a")
    uid_a = _run_uid_file("session-a").read_text().strip()

    stale_uid_file = copilot_agent._run_uid_file("stale-abandoned-session")
    stale_uid_file.write_text("does-not-matter")
    old_time = time.time() - copilot_agent._RUN_UID_FILE_STALENESS_SECONDS - 3600
    os.utime(stale_uid_file, (old_time, old_time))

    assert copilot_agent._active_run_uid_files() == [_run_uid_file("session-a")]
    assert stale_uid_file.exists()  # excluded from the count, but left on disk

    # single genuinely active candidate -> resolves directly, no "lamin
    # finish" event needed at all.
    finish_copilot_session()

    assert not _run_uid_file("session-a").exists()
    assert ln.Run.get(uid=uid_a).finished_at is not None
    assert stale_uid_file.exists()  # still untouched by an unrelated finish

    stale_uid_file.unlink()


def test_finish_with_explicit_session_id_bypasses_resolution(isolated):
    """The whole point of passing --session-id: it must work even when log-based
    resolution would fail or pick the wrong session (e.g. two sessions racing on
    the identical generic command text) — no `_resolve_session` call needed."""
    state_dir, project_dir = isolated
    cwd = str(project_dir)

    _write_fake_session(state_dir, "session-a", cwd, ["lamin track copilot --name a"])
    track_copilot_session(name="session a")
    uid_a = _run_uid_file("session-a").read_text().strip()

    _write_fake_session(state_dir, "session-b", cwd, ["lamin track copilot --name b"])
    track_copilot_session(name="session b")
    uid_b = _run_uid_file("session-b").read_text().strip()

    # No "lamin finish" event logged for either session at all — pure
    # text-match resolution would return None here (hard error). Passing
    # session_id explicitly must still succeed.
    finish_copilot_session(session_id="session-b")

    assert not _run_uid_file("session-b").exists()
    assert _run_uid_file("session-a").exists()  # untouched
    assert ln.Run.get(uid=uid_b).finished_at is not None
    assert ln.Run.get(uid=uid_a).finished_at is None

    ln.Run.get(uid=uid_a).delete(permanent=True)


def test_finish_with_unknown_session_id_hard_errors(isolated):
    with pytest.raises(click.ClickException, match="No active Copilot run found"):
        finish_copilot_session(session_id="never-tracked-session")


def test_resolve_session_immediate_match_has_no_retry_overhead(isolated):
    """An already-present match must resolve on the first attempt, not wait
    out any part of the retry interval."""
    state_dir, project_dir = isolated
    cwd = str(project_dir)
    _write_fake_session(state_dir, "instant-session", cwd, ["lamin finish"])

    start = time.monotonic()
    result = copilot_agent._resolve_session(
        "lamin finish", retry_total_seconds=5.0, retry_interval_seconds=0.3
    )
    elapsed = time.monotonic() - start

    assert result == "instant-session"
    assert elapsed < 0.3
