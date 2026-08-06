from __future__ import annotations

import json
import tempfile
import time
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

# Copilot's write/edit tool naming isn't observed yet, so transform-stamping
# fallback stays empty; fill in once verified against real Copilot sessions.
_SCRIPT_TOOL_NAMES: frozenset[str] = frozenset()
_SCRIPT_PATH_KEYS: tuple[str, ...] = ()
_SUFFIX_TO_KIND: dict[str, str] = {}

_SELF_MATCH_WINDOW_SECONDS = 5.0
_WORKSPACE_SCAN_STALENESS_SECONDS = 60.0

# Copilot logs tool calls to session-state asynchronously; retrying absorbs
# that write-lag without widening _SELF_MATCH_WINDOW_SECONDS itself.
_RESOLVE_RETRY_INTERVAL_SECONDS = 0.3
_RESOLVE_RETRY_TOTAL_SECONDS = 5.0

# Excludes ancient leftover run_uid files (from past hard errors/crashes)
# from candidate counts. Not a deletion age: only measures time since
# `track copilot` ran, so an unrelated finish call must never delete it.
_RUN_UID_FILE_STALENESS_SECONDS = 24 * 60 * 60


# --- session resolution ---
# Copilot has no $CLAUDE_CODE_SESSION_ID equivalent, so "which session is
# this" is resolved via (1) matching this process's own command text against
# recent tool-call events, then (2) falling back to a cwd-based workspace
# scan if that finds nothing.


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
        # A single visible tool call logs several internal events (hooks,
        # permission prompts, ...), so a fixed line count can put a busy
        # session's own very recent event past the tail before this even
        # runs. Walk backward and stop once timestamps age past the window
        # instead — events are appended in order, so everything earlier is
        # older still.
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_str = entry.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if now - ts > window_seconds:
                break
            if entry.get("type") != "tool.execution_start":
                continue
            data = entry.get("data")
            if not isinstance(data, dict):
                continue
            arguments = data.get("arguments")
            if not isinstance(arguments, dict):
                # some tools (e.g. apply_patch) encode arguments as a raw
                # string (a diff/patch), not {"command": ...} — can't be a
                # match for a command-text search, so it's not a candidate.
                continue
            cmd = arguments.get("command", "")
            if not isinstance(cmd, str) or match_text not in cmd:
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
        "See https://docs.lamin.ai/cli#track for details."
    )


def _resolve_session(
    match_text: str,
    retry_total_seconds: float = _RESOLVE_RETRY_TOTAL_SECONDS,
    retry_interval_seconds: float = _RESOLVE_RETRY_INTERVAL_SECONDS,
) -> str | None:
    deadline = time.monotonic() + retry_total_seconds
    while True:
        result = _resolve_session_via_self_invocation(
            match_text
        ) or _resolve_session_via_workspace_scan()
        if result is not None:
            return result
        if time.monotonic() >= deadline:
            return None
        time.sleep(retry_interval_seconds)


def _state_dir() -> Path:
    return _common.resolve_state_dir(".copilot")


def _run_uid_file(session_id: str) -> Path:
    return _state_dir() / f".lamindb_run_uid_copilot_{session_id}"


def _transcript_path(session_id: str) -> Path:
    return _copilot_session_state_dir() / session_id / "events.jsonl"


def _active_run_uid_files(
    staleness_seconds: float = _RUN_UID_FILE_STALENESS_SECONDS,
) -> list[Path]:
    """Run-uid files young enough to count as a genuinely active session.

    Filters, never deletes — a `--session-id` lookup still finds a stale
    file directly regardless of age.
    """
    now = time.time()
    active: list[Path] = []
    for f in sorted(_state_dir().glob(".lamindb_run_uid_copilot_*")):
        try:
            age = now - f.stat().st_mtime
        except OSError:
            continue
        if age > staleness_seconds:
            continue
        active.append(f)
    return active


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

        # Matching on the generic "track copilot" text alone can't tell two
        # parallel sessions apart if both call this within the same window —
        # `name` is mandated by the skill to be a distinct one-sentence
        # description, so prefer it when present; it's what actually
        # differs between two genuinely simultaneous invocations.
        session_id = _resolve_session(name if name else "track copilot")
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


def finish_copilot_session(session_id: str | None = None) -> None:
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

        if session_id is not None:
            # Caller already knows which session it is (skill captured
            # SESSION_ID from track's own output) — skip log-based guessing.
            run_uid_file = _run_uid_file(session_id)
            if not run_uid_file.exists():
                _common.hard_error(
                    f"No active Copilot run found for session {session_id!r}. "
                    "Run `lamin track copilot` first, or double check the "
                    "SESSION_ID printed there was copied exactly."
                )
        else:
            candidates = _active_run_uid_files()
            if not candidates:
                _common.warn("no active Copilot session found, skipping session finish")
                return

            if len(candidates) == 1:
                run_uid_file = candidates[0]
            else:
                resolved_session_id = _resolve_session("lamin finish")
                if resolved_session_id is None:
                    _hard_error_session_not_resolved()
                match = _run_uid_file(resolved_session_id)
                if match in candidates:
                    run_uid_file = match
                else:
                    candidate_ids = ", ".join(
                        c.name.removeprefix(".lamindb_run_uid_copilot_")
                        for c in candidates
                    )
                    _common.hard_error(
                        f"Resolved active Copilot session {resolved_session_id!r}, but "
                        "no matching local state file was found among the candidates: "
                        f"{candidate_ids}. This looks like inconsistent leftover state "
                        "— remove the stale files under .copilot/ and try again."
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
