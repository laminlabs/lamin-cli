from __future__ import annotations

import json
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

import click

from lamin_cli.agents import _common

# --- constants ---

_TRANSFORM_KEY = "__copilot__"
_TRANSFORM_UID = "vl12ppCqQp2P0000"
_SKILL_MARKER = "Base directory for this skill:"
_SHELL_TOOL_NAMES = frozenset({"bash"})

# Copilot's write/edit tool naming hasn't been directly observed yet, so the
# transcript-scan fallback for transform stamping is left empty — the primary
# path (child runs linked via LAMIN_INITIATED_BY_RUN_UID) is what matters and
# is fully agent-agnostic already. Fill these in once verified against real
# Copilot sessions that write files via a dedicated tool rather than bash.
_SCRIPT_TOOL_NAMES: frozenset[str] = frozenset()
_SCRIPT_PATH_KEYS: tuple[str, ...] = ()
_SUFFIX_TO_KIND: dict[str, str] = {}

_SELF_MATCH_WINDOW_SECONDS = 5.0
_WORKSPACE_SCAN_STALENESS_SECONDS = 60.0


# --- session resolution ---
# Copilot has no equivalent of $CLAUDE_CODE_SESSION_ID, so "which session is
# this" has to be resolved rather than read directly. Two strategies, tried
# in order:
#
# 1. Self-invocation match: this process's own command text (e.g. "lamin
#    track copilot") gets logged by Copilot to that session's events.jsonl
#    as a tool.execution_start *before* this code even starts running
#    (verified empirically: ~1ms after the tool call is issued, tens of ms
#    before the shell actually executes) — so searching for a recent event
#    containing that text reliably identifies which session issued this
#    exact call, even with multiple sessions active in the same directory.
# 2. Workspace scan fallback: match workspace.yaml's cwd against Path.cwd(),
#    pick the most recently updated one. Only a heuristic, but it's the
#    fallback path, not the primary one, and only matters if the log-based
#    match somehow finds nothing (e.g. a very old/stale invocation).


def _copilot_session_state_dir() -> Path:
    return Path.home() / ".copilot" / "session-state"


def _resolve_session_via_self_invocation(
    match_text: str, window_seconds: float = _SELF_MATCH_WINDOW_SECONDS
) -> str | None:
    state_dir = _copilot_session_state_dir()
    if not state_dir.exists():
        return None

    now = datetime.now(timezone.utc).timestamp()
    best_id: str | None = None
    best_ts = -1.0

    for session_dir in state_dir.iterdir():
        events_file = session_dir / "events.jsonl"
        if not events_file.exists():
            continue
        try:
            lines = events_file.read_text().splitlines()
        except OSError:
            continue
        # only the tail is relevant; this call was issued very recently
        for line in reversed(lines[-50:]):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "tool.execution_start":
                continue
            cmd = entry.get("data", {}).get("arguments", {}).get("command", "")
            if match_text not in cmd:
                continue
            ts_str = entry.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if now - ts > window_seconds:
                continue
            if ts > best_ts:
                best_ts = ts
                best_id = session_dir.name

    return best_id


def _resolve_session_via_workspace_scan(
    staleness_seconds: float = _WORKSPACE_SCAN_STALENESS_SECONDS,
) -> str | None:
    state_dir = _copilot_session_state_dir()
    if not state_dir.exists():
        return None

    cwd = str(Path.cwd())
    now = datetime.now(timezone.utc).timestamp()
    best_id: str | None = None
    best_updated = ""

    for session_dir in state_dir.iterdir():
        wf = session_dir / "workspace.yaml"
        if not wf.exists():
            continue
        try:
            text = wf.read_text()
        except OSError:
            continue
        data: dict[str, str] = {}
        for line in text.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                data[k.strip()] = v.strip()
        if data.get("cwd") != cwd:
            continue
        updated = data.get("updated_at", "")
        if updated > best_updated:
            best_updated = updated
            best_id = session_dir.name

    if best_id is None:
        return None

    # Guard against matching a stale, unrelated past session that merely
    # shares this cwd: without a recency check, this fallback would happily
    # bind a brand-new run to a session from days ago that has nothing to do
    # with the current invocation (observed in practice).
    try:
        best_ts = datetime.fromisoformat(best_updated.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    if now - best_ts > staleness_seconds:
        return None

    return best_id


def _hard_error_session_not_resolved() -> None:
    _common.hard_error(
        f"Cannot find your active Copilot session under {_copilot_session_state_dir()}. "
        'This usually means "Local" is selected in the Copilot Chat panel instead of '
        '"Copilot" — select "Copilot" instead and try again. '
        "See https://docs.lamin.ai/api#track for details."
    )


def _resolve_session(match_text: str) -> str | None:
    return (
        _resolve_session_via_self_invocation(match_text)
        or _resolve_session_via_workspace_scan()
    )


def _state_dir() -> Path:
    return _common.resolve_state_dir(".copilot")


def _run_uid_file(session_id: str) -> Path:
    return _state_dir() / f".lamindb_run_uid_copilot_{session_id}"


def _transcript_path(session_id: str) -> Path:
    return _copilot_session_state_dir() / session_id / "events.jsonl"


# --- session start ---


def track_copilot_session(name: str | None = None) -> None:
    try:
        import lamindb as ln
    except Exception as e:
        _common.warn(f"lamindb not available, skipping session tracking: {e}")
        return

    try:
        if not _common.instance_connected(ln):
            _common.hard_error(
                "No lamindb instance connected. Run `lamin connect <instance>` "
                "(or `lamin init` for a new one) and try again."
            )

        session_id = _resolve_session("track copilot")
        if session_id is None:
            _hard_error_session_not_resolved()

        transform = ln.Transform.filter(uid=_TRANSFORM_UID).one_or_none()
        if transform is None:
            transform, _ = ln.Transform.objects.get_or_create(
                uid=_TRANSFORM_UID,
                defaults={
                    "key": _TRANSFORM_KEY,
                    "kind": "function",
                    "description": "A Copilot session.",
                },
            )

        run = ln.Run(transform, status="started", name=name).save()

        _state_dir().mkdir(parents=True, exist_ok=True)
        _run_uid_file(session_id).write_text(run.uid)
        _common.info(
            f"started tracking Copilot session: SESSION_ID={session_id} run_uid={run.uid}"
        )
    except click.ClickException:
        raise
    except Exception as e:
        _common.warn(
            f"lamindb session tracking failed, continuing without tracking: {e}"
        )


# --- transcript parsing ---
# Normalizes Copilot's native event shape into the same {"role", "content":
# [...]} shape claude.py's transcript already uses, so _common.py's renderer
# and transform-stamping logic work unchanged for both agents.


def _is_bookkeeping_bash_cmd(cmd: str) -> bool:
    return False


def _load_raw_events(transcript_path: Path) -> list[dict]:
    raw_events: list[dict] = []
    with transcript_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw_events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return raw_events


def _build_entries(raw_events: list[dict]) -> list[dict]:
    results_by_call_id: dict[str, object] = {}
    for event in raw_events:
        if event.get("type") == "tool.execution_complete":
            data = event.get("data", {})
            call_id = data.get("toolCallId")
            if call_id:
                results_by_call_id[call_id] = data.get("result", {}).get("content", "")

    entries: list[dict] = []
    for event in raw_events:
        etype = event.get("type")
        data = event.get("data", {})

        if etype == "user.message":
            text = data.get("content", "")
            if isinstance(text, str) and text.strip():
                entries.append(
                    {"role": "user", "content": [{"type": "text", "text": text}]}
                )

        elif etype == "assistant.message":
            content_blocks: list[dict] = []
            text = data.get("content", "")
            if isinstance(text, str) and text.strip():
                content_blocks.append({"type": "text", "text": text})
            for tool_req in data.get("toolRequests", []) or []:
                call_id = tool_req.get("toolCallId", "")
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": tool_req.get("name", "tool"),
                        "input": tool_req.get("arguments", {}),
                    }
                )
                if call_id in results_by_call_id:
                    content_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": results_by_call_id[call_id],
                        }
                    )
            if content_blocks:
                entries.append({"role": "assistant", "content": content_blocks})

    return entries


def _parse_transcript(transcript_path: Path) -> list[dict]:
    return _build_entries(_load_raw_events(transcript_path))


# --- usage metrics ---
# Copilot only persists full per-model token totals (input/cache tokens) to
# events.jsonl in the "session.shutdown" event, which fires at actual CLI
# process exit — i.e. *after* `lamin track finish` already ran, since finish
# is invoked as the agent's own last bash tool call while the process is
# still alive. The only token field persisted before shutdown is
# "assistant.message.outputTokens", so n_tokens here is an output-tokens-only
# lower bound, not a full billed total — unlike Claude Code's n_tokens, which
# includes input/cache tokens. Not directly comparable across the two agents.


def _extract_usage_metrics(raw_events: list[dict]) -> dict:
    n_tokens = n_steps = n_tool_calls = 0
    for event in raw_events:
        etype = event.get("type")
        data = event.get("data", {})
        if etype == "assistant.message":
            n_steps += 1
            n_tokens += data.get("outputTokens") or 0
        elif etype == "tool.execution_start":
            n_tool_calls += 1
    return {
        "n_tokens": n_tokens,
        "n_steps": n_steps,
        "n_tool_calls": n_tool_calls,
    }


# --- session finish ---


def finish_copilot_session() -> None:
    try:
        import lamindb as ln
    except Exception as e:
        _common.warn(f"lamindb not available, skipping session finish: {e}")
        return

    try:
        if not _common.instance_connected(ln):
            _common.hard_error(
                "No lamindb instance connected. Run `lamin connect <instance>` "
                "(or `lamin init` for a new one) and try again."
            )

        candidates = sorted(_state_dir().glob(".lamindb_run_uid_copilot_*"))
        if not candidates:
            _common.warn("no active Copilot session found, skipping session finish")
            return

        if len(candidates) == 1:
            run_uid_file = candidates[0]
        else:
            session_id = _resolve_session("lamin finish")
            if session_id is None:
                # Same root cause as track_copilot_session's hard error: no
                # session resolves at all right now (almost always "Local"
                # selected instead of "Copilot"). The candidate files below
                # are very likely just stale leftovers, not genuinely
                # competing active sessions — surface the same, clearer
                # diagnosis instead of a confusing "which of N" error.
                _hard_error_session_not_resolved()
            match = _run_uid_file(session_id)
            if match in candidates:
                run_uid_file = match
            else:
                candidate_ids = ", ".join(
                    c.name.removeprefix(".lamindb_run_uid_copilot_") for c in candidates
                )
                _common.hard_error(
                    f"Resolved active Copilot session {session_id!r}, but no matching "
                    f"local state file was found among the candidates: {candidate_ids}. "
                    "This looks like inconsistent leftover state — remove the stale "
                    "files under .copilot/ and try again."
                )

        session_id = run_uid_file.name.removeprefix(".lamindb_run_uid_copilot_")
        uid = run_uid_file.read_text().strip()
        run = ln.Run.get(uid=uid)
        transcript_path = _transcript_path(session_id)

        if not transcript_path.exists():
            _common.warn(
                f"transcript file not found: {transcript_path} — closing run without report"
            )
            run._status_code = 0  # completed
            run.finished_at = datetime.now(timezone.utc)
            run.save()
            run_uid_file.unlink()
            return

        raw_events = _load_raw_events(transcript_path)
        entries = _build_entries(raw_events)
        html_doc = _common.render_transcript_html(
            entries,
            is_bookkeeping_bash_cmd=_is_bookkeeping_bash_cmd,
            skill_marker=_SKILL_MARKER,
            shell_tool_names=_SHELL_TOOL_NAMES,
        )

        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False)
        tmp_path = Path(tmp.name)
        try:
            tmp.write(html_doc)
            tmp.close()
            artifact = ln.Artifact(
                tmp.name,
                description="Copilot session transcript (rendered)",
                kind="__lamindb_run__",
                run=False,
            ).save()
        finally:
            tmp_path.unlink(missing_ok=True)

        run.report = artifact
        _common.stamp_transforms(
            run,
            entries,
            ln,
            script_tool_names=_SCRIPT_TOOL_NAMES,
            script_path_keys=_SCRIPT_PATH_KEYS,
            suffix_to_kind=_SUFFIX_TO_KIND,
        )

        usage_metrics = _extract_usage_metrics(raw_events)
        run.extra_data = {**(run.extra_data or {}), **usage_metrics}

        run._status_code = 0  # completed
        run.finished_at = datetime.now(timezone.utc)
        run.save()

        run_uid_file.unlink()
        _common.info(f"finished tracking Copilot session: {run.uid}")
    except click.ClickException:
        raise
    except Exception as e:
        _common.warn(f"lamindb session finish failed, continuing: {e}")
        _common.warn(traceback.format_exc())
