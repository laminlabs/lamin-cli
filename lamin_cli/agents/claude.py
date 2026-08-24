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

_TRANSFORM_KEY = "__claudecode__"
_TRANSFORM_UID = "SnfuhjObaAKR0000"
_SKILL_MARKER = "Base directory for this skill:"
_SHELL_TOOL_NAMES = frozenset({"Bash"})

_SUFFIX_TO_KIND: dict[str, str] = {
    ".ipynb": "notebook",
    ".py": "script",
    ".R": "script",
    ".Rmd": "script",
    ".qmd": "script",
}

_SCRIPT_TOOL_NAMES = frozenset({"Write", "Edit", "NotebookEdit"})
_SCRIPT_PATH_KEYS = ("file_path", "path", "notebook_path")


# --- lamindb helpers ---


def _session_id() -> str:
    return os.environ.get("CLAUDE_CODE_SESSION_ID", "default")


def _claude_dir() -> Path:
    return _common.resolve_state_dir(".claude")


def _run_uid_file(session_id: str | None = None) -> Path:
    sid = session_id if session_id is not None else _session_id()
    return _claude_dir() / f".lamindb_run_uid_{sid}"


def _transcript_path_file() -> Path:
    return _claude_dir() / f".lamindb_transcript_path_{_session_id()}"


def _get_transcript_path() -> Path:
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    projects_dir = Path.home() / ".claude" / "projects"
    # Fast path: Claude Code slugifies the launch directory into the project key.
    # This usually matches cwd, so try it first.
    project_key = str(Path.cwd()).replace("/", "-")
    candidate = projects_dir / project_key / f"{session_id}.jsonl"
    if candidate.exists() or not session_id:
        return candidate
    # Robust fallback: the user may `cd` into a subdirectory, so the subprocess
    # cwd differs from the directory Claude Code was launched in (which defines
    # the project key). The session_id filename is globally unique, so locate
    # the transcript by globbing across all project dirs.
    matches = sorted(projects_dir.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else candidate


# --- session start ---


def track_claudecode_session(name: str | None = None) -> None:
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
                    "description": "A Claude Code session.",
                },
            )

        run = ln.Run(transform, status="started", name=name).save()

        _claude_dir().mkdir(parents=True, exist_ok=True)
        _run_uid_file().write_text(run.uid)
        _transcript_path_file().write_text(str(_get_transcript_path()))
        _common.info(f"started tracking Claude Code session: {run.uid}")
    except click.ClickException:
        raise
    except Exception as e:
        _common.warn(
            f"lamindb session tracking failed, continuing without tracking: {e}"
        )


# --- transcript parsing ---


def _is_bookkeeping_bash_cmd(cmd: str) -> bool:
    return False


def _parse_transcript(transcript_path: Path) -> list[dict]:
    entries: list[dict] = []
    with transcript_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = entry.get("message")
            if msg and msg.get("role") in ("user", "assistant"):
                entries.append(msg)
    return entries


# --- usage metrics ---
# Claude Code writes one JSONL line per content block (thinking/text/tool_use)
# rather than one line per LLM turn — every block belonging to the same turn
# repeats the same message "id" and the same "usage" totals. Summing "usage"
# per line would therefore overcount tokens; dedup by message id first.
#
# n_tokens is the full billed total (input + output + cache-read +
# cache-write), not just output tokens: in agentic coding sessions the
# growing conversation gets resent as input on every turn, so input/cache
# tokens usually dominate the actual dollar cost by a wide margin.


def _extract_usage_metrics(entries: list[dict]) -> dict:
    seen_steps: set = set()
    seen_usage: set = set()
    n_tokens = n_tool_calls = 0
    for msg in entries:
        if msg.get("role") != "assistant":
            continue
        key = msg.get("id") or id(msg)
        seen_steps.add(key)
        content = msg.get("content")
        if isinstance(content, list):
            n_tool_calls += sum(
                1
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"
            )
        usage = msg.get("usage")
        if usage and key not in seen_usage:
            seen_usage.add(key)
            # full billed total, matching Anthropic/ccusage's convention:
            # input + output + cache-read + cache-write tokens
            n_tokens += (
                (usage.get("input_tokens") or 0)
                + (usage.get("output_tokens") or 0)
                + (usage.get("cache_read_input_tokens") or 0)
                + (usage.get("cache_creation_input_tokens") or 0)
            )
    return {
        "n_tokens": n_tokens,
        "n_steps": len(seen_steps),
        "n_tool_calls": n_tool_calls,
    }


# --- session finish ---


def finish_claudecode_session() -> None:
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

        run_uid_file = _run_uid_file()
        if not run_uid_file.exists():
            _common.warn("no active Claude Code session found, skipping session finish")
            return

        uid = run_uid_file.read_text().strip()
        run = ln.Run.get(uid=uid)
        transcript_path = Path(_transcript_path_file().read_text().strip())

        # The path stored at session start can be stale if it was derived from a
        # cwd that differs from Claude Code's launch dir; re-resolve as a fallback.
        if not transcript_path.exists():
            transcript_path = _get_transcript_path()

        if not transcript_path.exists():
            _common.warn(
                f"transcript file not found: {transcript_path} — "
                "closing run without report (is CLAUDE_CODE_SESSION_ID set?)"
            )
            run._status_code = 0  # completed
            run.finished_at = datetime.now(timezone.utc)
            run.save()
            run_uid_file.unlink()
            _transcript_path_file().unlink()
            return

        entries = _common.wait_for_finish_invocation(
            read_fn=lambda: _parse_transcript(transcript_path),
            is_done_fn=lambda entries: _common.contains_finish_invocation(
                entries, _SHELL_TOOL_NAMES
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
                description="Claude Code session transcript (rendered)",
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

        usage_metrics = _extract_usage_metrics(entries)
        run.extra_data = {**(run.extra_data or {}), **usage_metrics}

        run._status_code = 0  # completed
        run.finished_at = datetime.now(timezone.utc)
        run.save()

        run_uid_file.unlink()
        _transcript_path_file().unlink()
        _common.info(f"finished tracking Claude Code session: {run.uid}")
    except click.ClickException:
        raise
    except Exception as e:
        _common.warn(f"lamindb session finish failed, continuing: {e}")
        _common.warn(traceback.format_exc())
