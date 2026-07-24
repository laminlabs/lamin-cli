from __future__ import annotations

import json
import os
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

from lamin_cli.agents import _common

# --- constants ---

_CLAUDE_DIR = Path(".claude")
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


def _run_uid_file() -> Path:
    return _CLAUDE_DIR / f".lamindb_run_uid_{_session_id()}"


def _transcript_path_file() -> Path:
    return _CLAUDE_DIR / f".lamindb_transcript_path_{_session_id()}"


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
            _common.warn("no lamindb instance connected, skipping session tracking")
            return

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

        _CLAUDE_DIR.mkdir(exist_ok=True)
        _run_uid_file().write_text(run.uid)
        _transcript_path_file().write_text(str(_get_transcript_path()))
        _common.info(f"started tracking Claude Code session: {run.uid}")
    except Exception as e:
        _common.warn(f"lamindb session tracking failed, continuing without tracking: {e}")


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


# --- session finish ---


def finish_claudecode_session() -> None:
    try:
        import lamindb as ln
    except Exception as e:
        _common.warn(f"lamindb not available, skipping session finish: {e}")
        return

    try:
        if not _common.instance_connected(ln):
            _common.warn("no lamindb instance connected, skipping session finish")
            return

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

        entries = _parse_transcript(transcript_path)
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

        run._status_code = 0  # completed
        run.finished_at = datetime.now(timezone.utc)
        run.save()

        run_uid_file.unlink()
        _transcript_path_file().unlink()
        _common.info(f"finished tracking Claude Code session: {run.uid}")
    except Exception as e:
        _common.warn(f"lamindb session finish failed, continuing: {e}")
        _common.warn(traceback.format_exc())
