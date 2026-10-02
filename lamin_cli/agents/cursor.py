from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import click

from lamin_cli.agents import _common

_TRANSFORM_KEY = "__cursor__"
_TRANSFORM_UID = "QhKpVnRsTzWx0000"
_SHELL_TOOL_NAMES = frozenset({"Shell"})
_TOOL_NAMES = {
    "ask_question": "AskQuestion",
    "await": "AwaitShell",
    "edit_file_v2": "Write",
    "glob_file_search": "Glob",
    "read_file_v2": "Read",
    "ripgrep_raw_search": "Grep",
    "run_terminal_command_v2": "Shell",
}
_SESSION_ID_ENV_VAR = "LAMIN_CURSOR_SESSION_ID"
_COMPOSER_HEADERS_KEY = "composer.composerHeaders"
# One retry covers a marker row that commits while the first read is open.
_MARKER_LOOKUP_DELAY_SECONDS = 0.5
# `&&` / `||` / `;` / newlines. A bare `|` stays intact so quoted text is not split.
_COMMAND_SEPARATORS = re.compile(r"&&|\|\||;|\n")
_SUFFIX_TO_KIND = {
    ".ipynb": "notebook",
    ".py": "script",
    ".R": "script",
    ".Rmd": "script",
    ".qmd": "script",
}


def _state_dir() -> Path:
    return _common.resolve_state_dir(".cursor")


def _run_uid_file(conversation_id: str) -> Path:
    return _state_dir() / f".lamindb_run_uid_cursor_{conversation_id}"


def _cursor_db_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", ""))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def _connect_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA query_only=ON")
    return conn


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


def _is_cursor_shell_tool(name: object) -> bool:
    # Cursor 3.20 still stores `run_terminal_command_v2`. Keep the unversioned
    # prefix and the rendered `Shell` name so a later rename still matches.
    return isinstance(name, str) and (
        name == "Shell" or name.startswith("run_terminal_command")
    )


def _canonical_tool_name(tool_name: str) -> str:
    if _is_cursor_shell_tool(tool_name):
        return "Shell"
    return _TOOL_NAMES.get(tool_name, tool_name)


def _cursor_tool_rows(
    conversation_id: str | None = None,
    db_path: Path | None = None,
    contains: str | None = None,
) -> list[tuple[str, dict]]:
    db_path = db_path or _cursor_db_path()
    if not db_path.is_file():
        raise FileNotFoundError(f"Cursor chat database not found: {db_path}")
    prefix = f"bubbleId:{conversation_id}:%" if conversation_id else "bubbleId:%"
    if contains is None:
        query = "SELECT key, value FROM cursorDiskKV WHERE key LIKE ?"
        params: tuple[str, ...] = (prefix,)
    else:
        # The marker always appears in the stored JSON (command or stdout).
        # Filtering in SQL avoids parsing every bubble on a multi-GB database.
        query = (
            "SELECT key, value FROM cursorDiskKV "
            "WHERE key LIKE ? AND instr(CAST(value AS TEXT), ?) > 0"
        )
        params = (prefix, contains)
    with _connect_db(db_path) as conn:
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


def _tool_params(value: dict) -> dict:
    tool = value.get("toolFormerData")
    if not isinstance(tool, dict):
        return {}
    params = tool.get("params")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError:
            return {}
    return params if isinstance(params, dict) else {}


def _cursor_session_id() -> str:
    session_id = os.environ.get(_SESSION_ID_ENV_VAR, "").strip()
    if not session_id:
        raise ValueError(f"{_SESSION_ID_ENV_VAR} is not set")
    return session_id


def _json_object(raw: object) -> dict | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw if isinstance(raw, dict) else None


def _item_table_composer_headers(db_path: Path) -> dict[str, dict]:
    # Older Cursor builds keep composer metadata in one ItemTable JSON blob.
    try:
        with _connect_db(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                (_COMPOSER_HEADERS_KEY,),
            ).fetchone()
    except sqlite3.Error:
        return {}
    payload = _json_object(row[0]) if row else None
    composers = payload.get("allComposers") if payload else None
    if not isinstance(composers, list):
        return {}
    headers: dict[str, dict] = {}
    for composer in composers:
        if not isinstance(composer, dict):
            continue
        composer_id = composer.get("composerId")
        if isinstance(composer_id, str) and composer_id:
            headers[composer_id] = composer
    return headers


def _table_composer_headers(db_path: Path) -> dict[str, dict]:
    # Cursor 3.20 moved the same metadata into a composerHeaders table.
    # isArchived is a column; isDraft and workspaceIdentifier stay in value.
    try:
        with _connect_db(db_path) as conn:
            rows = conn.execute(
                "SELECT composerId, isArchived, value FROM composerHeaders"
            ).fetchall()
    except sqlite3.Error:
        return {}
    headers: dict[str, dict] = {}
    for composer_id, is_archived, raw_value in rows:
        if not isinstance(composer_id, str) or not composer_id:
            continue
        header = dict(_json_object(raw_value) or {})
        header["composerId"] = composer_id
        if is_archived is not None:
            header["isArchived"] = bool(is_archived)
        headers[composer_id] = header
    return headers


def _composer_headers(db_path: Path) -> dict[str, dict]:
    headers = _item_table_composer_headers(db_path)
    for composer_id, header in _table_composer_headers(db_path).items():
        merged = dict(headers.get(composer_id, {}))
        merged.update(header)
        headers[composer_id] = merged
    return headers


def _header_workspace_path(header: dict) -> Path | None:
    workspace = header.get("workspaceIdentifier")
    if not isinstance(workspace, dict):
        return None
    uri = workspace.get("uri")
    path = uri.get("fsPath") if isinstance(uri, dict) else None
    if isinstance(path, str) and path:
        return Path(path)
    return None


def _workspace_contains_cwd(workspace: Path, cwd: Path) -> bool:
    try:
        workspace = workspace.resolve()
        cwd = cwd.resolve()
    except OSError:
        return False
    return cwd == workspace or workspace in cwd.parents


def _uniquely_identify_error() -> ValueError:
    return ValueError(
        f"could not uniquely identify Cursor session {_SESSION_ID_ENV_VAR} "
        "in the local chat database"
    )


def _command_tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _is_echo_marker_command(cmd: str, marker: str) -> bool:
    # Match `echo <marker>` even when it is only one segment of a compound
    # command (`lamin switch && echo <marker>`). The command text is stored
    # when the tool starts, so this still matches before stdout is flushed.
    # A `lamin track` invocation that only exports the marker is not a hit.
    for segment in _COMMAND_SEPARATORS.split(cmd):
        tokens = _command_tokens(segment.strip())
        if not tokens or Path(tokens[0]).name != "echo":
            continue
        if any(token.strip("'\"") == marker for token in tokens[1:]):
            return True
    return False


def _shell_output_is_marker(value: dict, marker: str) -> bool:
    tool = value.get("toolFormerData")
    if not isinstance(tool, dict) or not _is_cursor_shell_tool(tool.get("name")):
        return False
    result = _read_tool_result(value)
    if result is not None:
        output = result.get("output")
        if isinstance(output, str) and any(
            line.strip() == marker for line in output.splitlines()
        ):
            return True
    cmd = _tool_params(value).get("command", "")
    return isinstance(cmd, str) and _is_echo_marker_command(cmd, marker)


def _matching_conversation_ids(session_id: str, db_path: Path) -> set[str]:
    marker = f"{_SESSION_ID_ENV_VAR}={session_id}"
    return {
        key.split(":", 2)[1]
        for key, value in _cursor_tool_rows(db_path=db_path, contains=marker)
        if len(key.split(":", 2)) == 3 and _shell_output_is_marker(value, marker)
    }


def _db_recently_active(db_path: Path) -> bool:
    # WAL mode updates the sidecar while the main file stays unchanged until
    # the next checkpoint, so either mtime counts as a live Cursor session.
    now = time.time()
    for path in (db_path, db_path.with_name(db_path.name + "-wal")):
        try:
            if now - path.stat().st_mtime < _common._LIVENESS_WINDOW_SECONDS:
                return True
        except OSError:
            continue
    return False


def _conversation_id_for_session(
    session_id: str, db_path: Path | None = None, *, wait: bool = False
) -> str:
    db_path = db_path or _cursor_db_path()
    conversation_ids = _matching_conversation_ids(session_id, db_path)
    if not conversation_ids and wait and _db_recently_active(db_path):
        time.sleep(_MARKER_LOOKUP_DELAY_SECONDS)
        conversation_ids = _matching_conversation_ids(session_id, db_path)
    if len(conversation_ids) == 1:
        return conversation_ids.pop()
    if not conversation_ids:
        raise _uniquely_identify_error()

    headers = _composer_headers(db_path)
    live_ids = {
        conversation_id
        for conversation_id in conversation_ids
        if not headers.get(conversation_id, {}).get("isArchived")
        and not headers.get(conversation_id, {}).get("isDraft")
    }
    if len(live_ids) == 1:
        return live_ids.pop()
    if not live_ids:
        raise _uniquely_identify_error()

    cwd = Path.cwd()
    scored: list[tuple[int, str]] = []
    for conversation_id in live_ids:
        workspace = _header_workspace_path(headers.get(conversation_id, {}))
        if workspace is None or not _workspace_contains_cwd(workspace, cwd):
            continue
        try:
            path_len = len(str(workspace.resolve()))
        except OSError:
            path_len = len(str(workspace))
        scored.append((path_len, conversation_id))
    if scored:
        longest = max(path_len for path_len, _ in scored)
        tied = [cid for path_len, cid in scored if path_len == longest]
        if len(tied) == 1:
            return tied[0]

    raise _uniquely_identify_error()


def _composer_header_ids(conversation_id: str, db_path: Path) -> list[str] | None:
    try:
        with _connect_db(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?",
                (f"composerData:{conversation_id}",),
            ).fetchone()
    except sqlite3.Error:
        return None
    payload = _json_object(row[0]) if row else None
    headers = payload.get("fullConversationHeadersOnly") if payload else None
    if not isinstance(headers, list) or not headers:
        return None
    ids: list[str] = []
    for item in headers:
        if not isinstance(item, dict):
            continue
        bubble_id = item.get("bubbleId")
        if isinstance(bubble_id, str) and bubble_id:
            ids.append(bubble_id)
    return ids or None


def _ordered_conversation_values(conversation_id: str, db_path: Path) -> list[dict]:
    rows = _cursor_tool_rows(conversation_id=conversation_id, db_path=db_path)
    header_ids = _composer_header_ids(conversation_id, db_path)
    if header_ids is None:
        return [
            value
            for _, value in sorted(
                rows, key=lambda row: str(row[1].get("createdAt", ""))
            )
        ]
    by_id: dict[str, dict] = {}
    for key, value in rows:
        parts = key.split(":", 2)
        if len(parts) == 3:
            by_id[parts[2]] = value
    return [by_id[bubble_id] for bubble_id in header_ids if bubble_id in by_id]


def _parse_sqlite_conversation(
    conversation_id: str, db_path: Path | None = None
) -> list[dict]:
    db_path = db_path or _cursor_db_path()
    values = _ordered_conversation_values(conversation_id, db_path)
    entries: list[dict] = []
    tool_number = 0
    for value in values:
        content: list[dict] = []
        thinking = value.get("thinking")
        thinking_text = ""
        if isinstance(thinking, dict):
            raw_thinking = thinking.get("text")
            if isinstance(raw_thinking, str):
                thinking_text = raw_thinking.strip()
        if thinking_text and value.get("type") != 1:
            content.append({"type": "thinking", "thinking": thinking_text})
        text = value.get("text")
        if isinstance(text, str) and text:
            content.append({"type": "text", "text": text})
        tool = value.get("toolFormerData")
        if isinstance(tool, dict) and isinstance(tool.get("name"), str):
            tool_number += 1
            tool_id = f"cursor-tool-{tool_number}"
            tool_name = tool["name"]
            content.append(
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": _canonical_tool_name(tool_name),
                    "input": _tool_params(value),
                }
            )
            result = _read_tool_result(value)
            if result is not None:
                output = result.get("output")
                if not isinstance(output, str):
                    output = json.dumps(result, ensure_ascii=False)
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": output,
                    }
                )
        if content:
            entries.append(
                {
                    "role": "user" if value.get("type") == 1 else "assistant",
                    "content": content,
                }
            )
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
        conversation_id = _conversation_id_for_session(_cursor_session_id(), wait=True)
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
        active_file = _run_uid_file(conversation_id)
        mapping_file = _common.persistent_run_uid_file(active_file, ln)
        with _common.session_state_lock(mapping_file):
            run = _common.get_mapped_run(ln, mapping_file, _TRANSFORM_UID)
            if run is None:
                run = ln.Run(transform, status="started", name=name).save()
                mapping_file.write_text(run.uid)
                message = "started tracking"
            else:
                run._status_code = -2
                run.finished_at = None
                run.save()
                message = "resumed tracking"
            active_file.write_text(run.uid)
        _common.info(f"{message} Cursor session: {run.uid}")
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
        conversation_id = _conversation_id_for_session(_cursor_session_id(), wait=True)
        active_file = _run_uid_file(conversation_id)
        if not active_file.exists():
            _common.warn("no active Cursor session found, skipping session finish")
            return
        run = ln.Run.get(uid=active_file.read_text().strip())
        try:
            entries = _common.wait_for_finish_invocation(
                read_fn=lambda: _parse_sqlite_conversation(conversation_id),
                is_done_fn=lambda parsed: _common.contains_finish_invocation(
                    parsed, _SHELL_TOOL_NAMES
                ),
                transcript_path=_cursor_db_path(),
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
                script_tool_names=frozenset({"Write"}),
                script_path_keys=("relativeWorkspacePath", "targetFile", "path"),
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
