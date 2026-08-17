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

        run = ln.Run(transform, status="started", name=name).save()

        _state_dir().mkdir(parents=True, exist_ok=True)
        _run_uid_file(session_id).write_text(run.uid)
        _common.info(f"started tracking Copilot session: {run.uid}")
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

        session_id = _session_id_from_env()
        if not session_id:
            _hard_error_no_session_id()

        run_uid_file = _run_uid_file(session_id)
        if not run_uid_file.exists():
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
            run_uid_file.unlink()
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
