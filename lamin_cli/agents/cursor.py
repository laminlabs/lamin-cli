from __future__ import annotations

import json
import os
import secrets
import sqlite3
import sys
import tempfile
import traceback
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import click

from lamin_cli.agents import _common

_TRANSFORM_KEY = "__cursor__"
_TRANSFORM_UID = "QhKpVnRsTzWx0000"
_SHELL_TOOL_NAMES = frozenset({"Shell"})
_SUFFIX_TO_KIND = {
    ".ipynb": "notebook",
    ".py": "script",
    ".R": "script",
    ".Rmd": "script",
    ".qmd": "script",
}
_MARKER_PREFIX = "LAMIN_CURSOR_RUN_MARKER="


def _state_dir() -> Path:
    return _common.resolve_state_dir(".cursor")


def _run_uid_file() -> Path:
    return _state_dir() / ".lamindb_run_uid_cursor"


def _marker_file() -> Path:
    return _state_dir() / ".lamindb_cursor_marker"


def _cursor_db_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", ""))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def _read_tool_result(value: dict) -> dict | None:
    tool = value.get("toolFormerData")
    if not isinstance(tool, dict):
        return None
    result = tool.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return None
    return result if isinstance(result, dict) else None


def _cursor_tool_rows(
    conversation_id: str | None = None,
    db_path: Path | None = None,
    marker: str | None = None,
) -> list[tuple[str, dict]]:
    db_path = db_path or _cursor_db_path()
    if not db_path.is_file():
        raise FileNotFoundError(f"Cursor chat database not found: {db_path}")
    prefix = f"bubbleId:{conversation_id}:%" if conversation_id else "bubbleId:%"
    query = "SELECT key, value FROM cursorDiskKV WHERE key LIKE ?"
    params: tuple[str, ...] = (prefix,)
    if marker is not None:
        query += " AND instr(CAST(value AS TEXT), ?) > 0"
        params += (marker,)
    with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as conn:
        rows = conn.execute(query, params).fetchall()
    parsed = []
    for key, raw in rows:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            parsed.append((key, value))
    return parsed


def _conversation_id_for_marker(marker: str, db_path: Path | None = None) -> str:
    matches = set()
    for key, value in _cursor_tool_rows(db_path=db_path, marker=marker):
        tool = value.get("toolFormerData")
        if not isinstance(tool, dict) or tool.get("name") != "run_terminal_command_v2":
            continue
        result = _read_tool_result(value)
        output = result.get("output") if result else None
        if isinstance(output, str) and _MARKER_PREFIX + marker in output.splitlines():
            parts = key.split(":", 2)
            if len(parts) == 3:
                matches.add(parts[1])
    if len(matches) != 1:
        raise ValueError(
            f"expected one Cursor chat for the tracking marker, found {len(matches)}"
        )
    return matches.pop()


def _transcript_path(conversation_id: str) -> Path:
    projects = Path.home() / ".cursor" / "projects"
    matches = list(
        projects.glob(f"*/agent-transcripts/{conversation_id}/{conversation_id}.jsonl")
    )
    if len(matches) != 1:
        raise FileNotFoundError(
            f"expected one Cursor JSONL transcript for {conversation_id}, "
            f"found {len(matches)}"
        )
    return matches[0]


def _shell_outputs(
    conversation_id: str, db_path: Path | None = None
) -> dict[str, deque[str]]:
    calls: list[tuple[str, str, str]] = []
    for _, value in _cursor_tool_rows(conversation_id, db_path):
        tool = value.get("toolFormerData")
        if not isinstance(tool, dict) or tool.get("name") != "run_terminal_command_v2":
            continue
        params = tool.get("params")
        result = _read_tool_result(value)
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                continue
        if not isinstance(params, dict) or result is None:
            continue
        command, output = params.get("command"), result.get("output")
        if isinstance(command, str) and isinstance(output, str):
            calls.append((str(value.get("createdAt", "")), command, output))
    outputs: dict[str, deque[str]] = defaultdict(deque)
    for _, command, output in sorted(calls):
        outputs[command].append(output)
    return outputs


def _parse_transcript(path: Path, outputs: dict[str, deque[str]]) -> list[dict]:
    entries = []
    tool_number = 0
    with path.open() as stream:
        for line in stream:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # Cursor may still be writing the last line.
            role, message = entry.get("role"), entry.get("message")
            if role not in ("user", "assistant") or not isinstance(message, dict):
                continue
            blocks = message.get("content")
            if not isinstance(blocks, list):
                continue
            content = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    content.append(block)
                    continue
                tool_number += 1
                tool_id = f"cursor-tool-{tool_number}"
                content.append({**block, "id": tool_id})
                if block.get("name") == "Shell":
                    command = block.get("input", {}).get("command")
                    if isinstance(command, str) and outputs.get(command):
                        content.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": outputs[command].popleft(),
                            }
                        )
            entries.append({"role": role, "content": content})
    return entries


def track_cursor_session(name: str | None = None) -> None:
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
        transform = ln.Transform.filter(uid=_TRANSFORM_UID).one_or_none()
        if transform is None:
            transform, _ = ln.Transform.objects.get_or_create(
                uid=_TRANSFORM_UID,
                defaults={
                    "key": _TRANSFORM_KEY,
                    "kind": "function",
                    "description": "A Cursor session.",
                },
            )
        _state_dir().mkdir(parents=True, exist_ok=True)
        active_file = _run_uid_file()
        mapping_file = _common.persistent_run_uid_file(active_file, ln)
        with _common.session_state_lock(mapping_file):
            run = _common.get_mapped_run(ln, mapping_file, _TRANSFORM_UID)
            if run is None:
                run = ln.Run(transform, status="started", name=name).save()
                mapping_file.write_text(run.uid)
                marker = secrets.token_hex(16)
                _marker_file().write_text(marker)
                message = "started tracking"
            else:
                run._status_code = -2
                run.finished_at = None
                run.save()
                marker = (
                    _marker_file().read_text().strip()
                    if _marker_file().exists()
                    else secrets.token_hex(16)
                )
                _marker_file().write_text(marker)
                message = "resumed tracking"
            active_file.write_text(run.uid)
        _common.info(f"{message} Cursor session: {run.uid}")
        click.echo(_MARKER_PREFIX + marker)
    except click.ClickException:
        raise
    except Exception as e:
        _common.warn(
            f"lamindb session tracking failed, continuing without tracking: {e}"
        )


def finish_cursor_session() -> None:
    try:
        import lamindb as ln
    except Exception as e:
        _common.warn(f"lamindb not available, skipping session finish: {e}")
        return

    try:
        if not _common.instance_connected(ln):
            _common.hard_error("No lamindb instance connected.")
        active_file = _run_uid_file()
        if not active_file.exists():
            _common.warn("no active Cursor session found, skipping session finish")
            return
        run = ln.Run.get(uid=active_file.read_text().strip())
        marker = _marker_file().read_text().strip()
        try:
            conversation_id = _conversation_id_for_marker(marker)
            transcript_path = _transcript_path(conversation_id)
            outputs = _shell_outputs(conversation_id)

            def _read() -> list[dict]:
                return _parse_transcript(
                    transcript_path, {k: deque(v) for k, v in outputs.items()}
                )

            entries = _common.wait_for_finish_invocation(
                read_fn=_read,
                is_done_fn=lambda value: _common.contains_finish_invocation(
                    value, _SHELL_TOOL_NAMES
                ),
                transcript_path=transcript_path,
            )
            html_doc = _common.render_transcript_html(
                entries,
                is_bookkeeping_bash_cmd=lambda cmd: False,
                skill_marker="Base directory for this skill:",
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
                    description="Cursor session transcript (rendered)",
                    ln=ln,
                )
            finally:
                tmp_path.unlink(missing_ok=True)
            _common.stamp_transforms(
                run,
                entries,
                ln,
                script_tool_names=frozenset({"Write", "StrReplace"}),
                script_path_keys=("path",),
                suffix_to_kind=_SUFFIX_TO_KIND,
            )
            run.extra_data = {
                **(run.extra_data or {}),
                "n_steps": sum(entry["role"] == "assistant" for entry in entries),
                "n_tool_calls": sum(
                    block.get("type") == "tool_use"
                    for entry in entries
                    for block in entry["content"]
                ),
            }
        except (FileNotFoundError, ValueError, sqlite3.Error) as e:
            _common.warn(f"Cursor report unavailable: {e}; closing run without report")
        run._status_code = 0
        run.finished_at = datetime.now(timezone.utc)
        run.save()
        active_file.unlink()
        _common.info(f"finished tracking Cursor session: {run.uid}")
    except click.ClickException:
        raise
    except Exception as e:
        _common.warn(f"lamindb session finish failed, continuing: {e}")
        _common.warn(traceback.format_exc())
