from __future__ import annotations

import json
import os
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path

import click

from lamin_cli.agents import _common

# --- constants ---

_TRANSFORM_KEY = "__codex__"
_TRANSFORM_UID = "Bd9sLm4nQx7k0000"
_SKILL_MARKER = "Base directory for this skill:"
_SHELL_TOOL_NAMES = frozenset({"exec"})


def _thread_id_from_env() -> str:
    return os.environ.get("CODEX_THREAD_ID", "").strip()


def _require_thread_id() -> str:
    thread_id = _thread_id_from_env()
    if not thread_id:
        _common.hard_error(
            "Cannot find CODEX_THREAD_ID in the environment. "
            "Run this from a Codex tool call so Codex can identify the chat."
        )
    return thread_id


def _codex_root() -> Path:
    return Path.home() / ".codex"


def _state_dir() -> Path:
    return _common.resolve_state_dir(".codex")


def _run_uid_file(thread_id: str | None = None) -> Path:
    sid = thread_id if thread_id is not None else _require_thread_id()
    return _state_dir() / f".lamindb_run_uid_codex_{sid}"


def _persistent_run_uid_file(thread_id: str, ln: object) -> Path:
    return _common.persistent_run_uid_file(_run_uid_file(thread_id), ln)


# --- session start ---


def track_codex_session(name: str | None = None) -> None:
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

        thread_id = _require_thread_id()
        transform = ln.Transform.filter(uid=_TRANSFORM_UID).one_or_none()
        if transform is None:
            transform, _ = ln.Transform.objects.get_or_create(
                uid=_TRANSFORM_UID,
                defaults={
                    "key": _TRANSFORM_KEY,
                    "kind": "function",
                    "description": "A Codex session.",
                },
            )

        _state_dir().mkdir(parents=True, exist_ok=True)
        active_file = _run_uid_file(thread_id)
        mapping_file = _persistent_run_uid_file(thread_id, ln)
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
        _common.info(f"{message} Codex session: {run.uid}")
    except click.ClickException:
        raise
    except Exception as e:
        _common.warn(
            f"lamindb session tracking failed, continuing without tracking: {e}"
        )


# --- transcript parsing ---
# One Codex chat can continue into a new rollout file. The thread index stores
# only the newest path, so finish reads every rollout whose session id is this
# thread, oldest first. Completed items are the visible chat; response items
# repeat those and also carry the hidden developer prompt.


def _is_bookkeeping_bash_cmd(cmd: str) -> bool:
    return False


def _session_meta(path: Path) -> dict:
    try:
        with path.open() as handle:
            line = handle.readline()
    except OSError:
        return {}
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return {}
    payload = entry.get("payload")
    return payload if isinstance(payload, dict) else {}


def _rollout_paths(thread_id: str) -> list[Path]:
    """Live and archived rollout files for this thread, oldest first."""
    chosen: dict[str, Path] = {}
    root = _codex_root()
    for folder in (root / "sessions", root / "archived_sessions"):
        if not folder.is_dir():
            continue
        for path in folder.rglob("rollout-*.jsonl"):
            name = path.name
            if f"-{thread_id}.jsonl" not in name and f"-{thread_id}_" not in name:
                continue
            meta = _session_meta(path)
            session_id = meta.get("id") or meta.get("session_id")
            if session_id != thread_id:
                continue
            timestamp = meta.get("timestamp") or name
            current = chosen.get(str(timestamp))
            if current is None or "archived_sessions" in current.parts:
                chosen[str(timestamp)] = path
    return [chosen[key] for key in sorted(chosen)]


def _plain_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if isinstance(block, str):
            text = block.strip()
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            text = block["text"].strip()
        else:
            continue
        if text:
            parts.append(text)
    return "\n".join(parts)


def _command_text(item: dict) -> str:
    parsed = item.get("parsed_cmd")
    if isinstance(parsed, list):
        commands = [
            part["cmd"]
            for part in parsed
            if isinstance(part, dict) and isinstance(part.get("cmd"), str)
        ]
        if commands:
            return "\n".join(commands)
    command = item.get("command")
    if isinstance(command, list) and command:
        if (
            len(command) >= 3
            and str(command[0]).endswith("zsh")
            and command[1] == "-lc"
            and isinstance(command[2], str)
        ):
            return command[2]
        return " ".join(str(part) for part in command)
    if isinstance(command, str):
        return command
    return ""


def _append_item(entries: list[dict], item: dict, counts: dict[str, int]) -> None:
    kind = item.get("type")
    if kind == "UserMessage":
        text = _plain_text(item.get("content"))
        if text:
            entries.append({"role": "user", "content": [{"type": "text", "text": text}]})
        return
    if kind == "AgentMessage":
        text = _plain_text(item.get("content"))
        if text:
            counts["n_steps"] += 1
            entries.append(
                {"role": "assistant", "content": [{"type": "text", "text": text}]}
            )
        return
    if kind == "Reasoning":
        text = _plain_text(item.get("summary_text")) or _plain_text(
            item.get("raw_content")
        )
        if text:
            entries.append(
                {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": text}],
                }
            )
        return
    if kind != "CommandExecution":
        return
    call_id = str(item.get("id") or f"exec-{counts['n_tool_calls']}")
    output = item.get("aggregated_output")
    if not isinstance(output, str):
        output = item.get("stdout") if isinstance(item.get("stdout"), str) else ""
    counts["n_tool_calls"] += 1
    entries.append(
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": "exec",
                    "input": {"command": _command_text(item)},
                },
                {"type": "tool_result", "tool_use_id": call_id, "content": output},
            ],
        }
    )


def _load_transcript(thread_id: str) -> tuple[list[dict], dict[str, int], Path | None]:
    entries: list[dict] = []
    counts = {"n_steps": 0, "n_tool_calls": 0}
    paths = _rollout_paths(thread_id)
    for path in paths:
        with path.open() as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = entry.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "item_completed":
                    continue
                item = payload.get("item")
                if isinstance(item, dict):
                    _append_item(entries, item, counts)
    newest = paths[-1] if paths else None
    return entries, counts, newest


# --- session finish ---


def finish_codex_session() -> None:
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

        thread_id = _require_thread_id()
        run_uid_file = _run_uid_file(thread_id)
        if not run_uid_file.exists():
            _common.warn("no active Codex session found, skipping session finish")
            return

        uid = run_uid_file.read_text().strip()
        run = ln.Run.get(uid=uid)
        paths = _rollout_paths(thread_id)
        if not paths:
            _common.warn(
                "Codex transcript not found — closing run without report "
                "(is CODEX_THREAD_ID the chat that was tracked?)"
            )
            run._status_code = 0  # completed
            run.finished_at = datetime.now(UTC)
            run.save()
            run_uid_file.unlink()
            return

        def _read() -> tuple[list[dict], dict[str, int]]:
            loaded, counts, _newest = _load_transcript(thread_id)
            return loaded, counts

        entries, counts = _common.wait_for_finish_invocation(
            read_fn=_read,
            is_done_fn=lambda result: _common.contains_finish_invocation(
                result[0], _SHELL_TOOL_NAMES
            ),
            transcript_path=paths[-1],
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
                description="Codex session transcript (rendered)",
                ln=ln,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        _common.stamp_transforms(run, entries, ln)
        run.extra_data = {**(run.extra_data or {}), **counts}
        run._status_code = 0  # completed
        run.finished_at = datetime.now(UTC)
        run.save()
        run_uid_file.unlink()
        _common.info(f"finished tracking Codex session: {run.uid}")
    except click.ClickException:
        raise
    except Exception as e:
        _common.warn(f"lamindb session finish failed, continuing: {e}")
        _common.warn(traceback.format_exc())
