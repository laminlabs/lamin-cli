from __future__ import annotations

import json
import os
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

# Copilot's write/edit tool naming isn't observed yet, so transform-stamping
# fallback stays empty; fill in once verified against real Copilot sessions.
_SCRIPT_TOOL_NAMES: frozenset[str] = frozenset()
_SCRIPT_PATH_KEYS: tuple[str, ...] = ()
_SUFFIX_TO_KIND: dict[str, str] = {}


# --- session resolution ---
# Copilot sets $COPILOT_AGENT_SESSION_ID in every tool call's own
# environment — same idea as Claude Code's $CLAUDE_CODE_SESSION_ID. Read
# directly; no log-matching or retries needed.


def _copilot_session_state_dir() -> Path:
    return Path.home() / ".copilot" / "session-state"


def _session_id_from_env() -> str:
    return os.environ.get("COPILOT_AGENT_SESSION_ID", "")


def _hard_error_no_session_id() -> None:
    _common.hard_error(
        "Cannot find COPILOT_AGENT_SESSION_ID in the environment. "
        'This usually means "Local" is selected in the Copilot Chat panel instead of '
        '"Copilot" — select "Copilot" instead and try again. '
        "See https://docs.lamin.ai/cli#track for details."
    )


def _state_dir() -> Path:
    return _common.resolve_state_dir(".copilot")


def _run_uid_filename(session_id: str) -> str:
    return f".lamindb_run_uid_copilot_{session_id}"


def _run_uid_file(session_id: str) -> Path:
    return _state_dir() / _run_uid_filename(session_id)


def _persistent_run_uid_file(session_id: str, ln: object) -> Path:
    return _common.persistent_run_uid_file(_run_uid_file(session_id), ln)


def _configured_dest_state_dir() -> Path | None:
    """Configured dest-dir `.copilot/`, not the worktree branch folder."""
    try:
        from lamindb_setup import settings as ln_setup_settings

        dest_dir = ln_setup_settings.dev_dir
    except Exception:
        dest_dir = None
    return Path(dest_dir) / ".copilot" if dest_dir is not None else None


def _uid_search_dirs() -> list[Path]:
    dirs: list[Path] = []
    for path in (_state_dir(), _configured_dest_state_dir()):
        if path is not None and path not in dirs:
            dirs.append(path)
    return dirs


def _existing_active_file(session_id: str) -> Path | None:
    for directory in _uid_search_dirs():
        path = directory / _run_uid_filename(session_id)
        if path.exists():
            return path
    return None


def _write_shared_run_uid(session_id: str, run_uid: str, ln: object) -> None:
    for directory in _uid_search_dirs():
        directory.mkdir(parents=True, exist_ok=True)
        active = directory / _run_uid_filename(session_id)
        mapping = _common.persistent_run_uid_file(active, ln)
        with _common.session_state_lock(mapping):
            mapping.write_text(run_uid)
            active.write_text(run_uid)


def _unlink_active_files(session_id: str) -> None:
    for directory in _uid_search_dirs():
        (directory / _run_uid_filename(session_id)).unlink(missing_ok=True)


def _transcript_path(session_id: str) -> Path:
    return _copilot_session_state_dir() / session_id / "events.jsonl"


def _known_session_ids() -> set[str]:
    root = _copilot_session_state_dir()
    if not root.exists():
        return set()
    return {
        path.parent.name
        for path in root.glob("*/events.jsonl")
        if path.parent.name
    }


def _foreign_session_ids_in_text(
    text: str, session_id: str, known: set[str]
) -> list[str]:
    return sorted(
        other_id
        for other_id in known
        if other_id != session_id and other_id in text
    )


def _single_link_ids(transcript: Path, session_id: str, known: set[str]) -> list[str]:
    """Other session ids that appear alone in one jsonl event.

    A create/get on one child has a single id. A list of many sessions has
    several ids in one event and is ignored.
    """
    hits: set[str] = set()
    try:
        lines = transcript.read_text(errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        foreign = _foreign_session_ids_in_text(line, session_id, known)
        if len(foreign) == 1:
            hits.add(foreign[0])
    return sorted(hits)


def _parents_of(session_id: str, known: set[str]) -> list[str]:
    """Other sessions with a single-id event pointing at this session."""
    if not session_id:
        return []
    hits: list[str] = []
    for events in _copilot_session_state_dir().glob("*/events.jsonl"):
        other_id = events.parent.name
        if other_id == session_id:
            continue
        if session_id in _single_link_ids(events, other_id, known):
            hits.append(other_id)
    return sorted(hits)


def _children_of(session_id: str, known: set[str]) -> list[str]:
    """Children named by a single-id event in this session's own transcript."""
    transcript = _transcript_path(session_id)
    if not session_id or not transcript.exists():
        return []
    return _single_link_ids(transcript, session_id, known)


def _session_id_from_uid_filename(name: str) -> str | None:
    prefix = ".lamindb_run_uid_copilot_"
    if not name.startswith(prefix):
        return None
    rest = name[len(prefix) :]
    if not rest or rest.endswith(".lock"):
        return None
    maybe_key = rest.rsplit("_", 1)[-1]
    if len(maybe_key) == 12 and maybe_key.isalnum() and "_" in rest:
        try:
            int(maybe_key, 16)
        except ValueError:
            return rest
        return rest[: -(len(maybe_key) + 1)]
    return rest


def _sessions_mapped_to_run(run_uid: str) -> list[str]:
    if not run_uid:
        return []
    hits: set[str] = set()
    for directory in _uid_search_dirs():
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if not path.is_file():
                continue
            session_id = _session_id_from_uid_filename(path.name)
            if session_id is None:
                continue
            try:
                if path.read_text().strip() == run_uid:
                    hits.add(session_id)
            except OSError:
                continue
    return sorted(hits)


def _report_session_ids(
    session_id: str, run_uid: str | None = None
) -> tuple[list[str], list[str]]:
    """Return (transcript order, other session ids that should share this run)."""
    known = _known_session_ids()
    parents = _parents_of(session_id, known)
    children = _children_of(session_id, known)
    mapped = [
        other_id
        for other_id in _sessions_mapped_to_run(run_uid or "")
        if other_id != session_id
    ]
    if parents:
        extras = sorted((set(children) | set(mapped)) - set(parents) - {session_id})
        return [*parents, session_id, *extras], parents
    others = sorted(set(children) | set(mapped))
    if others:
        return [session_id, *others], []
    return [session_id], []


def _load_joined_transcript(
    session_ids: list[str],
    *,
    this_session_id: str,
    this_raw: list[dict],
    this_entries: list[dict],
) -> tuple[list[dict], list[dict]]:
    raw_events: list[dict] = []
    entries: list[dict] = []
    for sid in session_ids:
        if sid == this_session_id:
            raw_events.extend(this_raw)
            entries.extend(this_entries)
            continue
        path = _transcript_path(sid)
        if not path.exists():
            continue
        raw = _load_raw_events(path)
        raw_events.extend(raw)
        entries.extend(_build_entries(raw))
    return raw_events, entries


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

        session_id = _session_id_from_env()
        if not session_id:
            _hard_error_no_session_id()

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

        _state_dir().mkdir(parents=True, exist_ok=True)
        active_file = _run_uid_file(session_id)
        mapping_file = _persistent_run_uid_file(session_id, ln)
        with _common.session_state_lock(mapping_file):
            run = _common.get_mapped_run(ln, mapping_file, _TRANSFORM_UID)
            if run is None:
                run = ln.Run(transform, status="started", name=name).save()
                mapping_file.write_text(run.uid)
                message = "started tracking"
            else:
                run._status_code = -2  # re-started
                run.finished_at = None
                run.save()
                message = "resumed tracking"
            active_file.write_text(run.uid)
        _common.info(f"{message} Copilot session: {run.uid}")
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
            reasoning = data.get("reasoningText")
            if isinstance(reasoning, str) and reasoning.strip():
                content_blocks.append({"type": "thinking", "thinking": reasoning})
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

        session_id = _session_id_from_env()
        if not session_id:
            _hard_error_no_session_id()

        run_uid_file = _existing_active_file(session_id)
        if run_uid_file is None:
            _common.warn("no active Copilot session found, skipping session finish")
            return

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
            _unlink_active_files(session_id)
            return

        def _read() -> tuple[list[dict], list[dict]]:
            raw_events = _load_raw_events(transcript_path)
            return raw_events, _build_entries(raw_events)

        raw_events, entries = _common.wait_for_finish_invocation(
            read_fn=_read,
            is_done_fn=lambda result: _common.contains_finish_invocation(
                result[1], _SHELL_TOOL_NAMES
            ),
            transcript_path=transcript_path,
        )
        report_ids, share_ids = _report_session_ids(session_id, run.uid)
        raw_events, entries = _load_joined_transcript(
            report_ids,
            this_session_id=session_id,
            this_raw=raw_events,
            this_entries=entries,
        )
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
            _common.save_or_replace_report(
                run,
                tmp_path,
                description="Copilot session transcript (rendered)",
                ln=ln,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

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

        for other_id in share_ids:
            _write_shared_run_uid(other_id, run.uid, ln)
        _unlink_active_files(session_id)
        _common.info(f"finished tracking Copilot session: {run.uid}")
    except click.ClickException:
        raise
    except Exception as e:
        _common.warn(f"lamindb session finish failed, continuing: {e}")
        _common.warn(traceback.format_exc())
