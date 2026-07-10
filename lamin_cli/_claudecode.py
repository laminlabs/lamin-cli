from __future__ import annotations

import html
import json
import os
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

import click

# --- constants ---

_CLAUDE_DIR = Path(".claude")
_RUN_UID_FILE = _CLAUDE_DIR / ".lamindb_run_uid"
_TRANSCRIPT_PATH_FILE = _CLAUDE_DIR / ".lamindb_transcript_path"
_TRANSFORM_KEY = "__claudecode__"
_SKILL_MARKER = "Base directory for this skill:"
_BLOCK_TRUNCATE = 4000

_SUFFIX_TO_KIND: dict[str, str] = {
    ".ipynb": "notebook",
    ".py": "script",
    ".R": "script",
    ".Rmd": "script",
    ".qmd": "script",
}

_SCRIPT_TOOL_NAMES = {"Write", "Edit", "NotebookEdit"}
_SCRIPT_PATH_KEYS = ("file_path", "path", "notebook_path")

_HTML_TEMPLATE = """\
<!doctype html>
<html><head><meta charset="utf-8"><title>Claude Code Session Transcript</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #fff; color: #111; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }}
.block {{ margin-bottom: 1rem; padding: 0.75rem 1rem; border-radius: 8px; }}
.user-text {{ background: #eef4ff; border-left: 4px solid #3b6fd6; }}
.assistant-text {{ background: #f5f5f5; border-left: 4px solid #555; }}
.tool-use {{ background: #fff8e1; border-left: 4px solid #d6a93b; }}
.tool-result {{ background: #f0fff4; border-left: 4px solid #3bd66f; }}
.thinking {{ background: #f9f0ff; border-left: 4px solid #a63bd6; }}
.role-label {{ font-weight: 600; font-size: 0.8rem; text-transform: uppercase; color: #666; margin-bottom: 0.4rem; }}
.content {{ white-space: pre-wrap; word-wrap: break-word; font-size: 0.9rem; }}
pre.content {{ font-family: ui-monospace, monospace; }}
h1 {{ font-size: 1.3rem; }}
</style></head>
<body>
<h1>Claude Code Session Transcript</h1>
{blocks}
</body></html>"""


# --- output helpers ---

def _info(msg: str) -> None:
    click.echo(f"✓ {msg}")


def _warn(msg: str) -> None:
    click.echo(f"! {msg}", err=True)


# --- lamindb helpers ---

def _get_transcript_path() -> Path:
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    project_key = os.getcwd().replace("/", "-")
    return Path.home() / ".claude" / "projects" / project_key / f"{session_id}.jsonl"


def _instance_connected(ln: object) -> bool:
    return bool(ln.setup.settings._instance_exists)  # type: ignore[attr-defined]


# --- session start ---

def track_claudecode_session(description: str | None) -> None:
    try:
        import lamindb as ln
    except Exception as e:
        _warn(f"lamindb not available, skipping session tracking: {e}")
        return

    try:
        if not _instance_connected(ln):
            _warn("no lamindb instance connected, skipping session tracking")
            return

        transform = ln.Transform.filter(key=_TRANSFORM_KEY).first()
        if transform is None:
            transform = ln.Transform(
                key=_TRANSFORM_KEY,
                kind="pipeline",
                description="All Claude Code sessions in this project",
            )
            transform.save()
            _info(f"created transform: {transform.uid}")
        else:
            _info(f"using existing transform: {transform.uid}")

        run = ln.Run(transform)
        run.started_at = datetime.now(timezone.utc)
        if description:
            run.description = description
        run.save()

        _CLAUDE_DIR.mkdir(exist_ok=True)
        _RUN_UID_FILE.write_text(run.uid)
        _TRANSCRIPT_PATH_FILE.write_text(str(_get_transcript_path()))
        _info(f"started tracking Claude Code session: {run.uid}")
    except Exception as e:
        _warn(f"lamindb session tracking failed, continuing without tracking: {e}")


# --- transcript parsing ---

def _content_has_marker(content: object, marker: str) -> bool:
    if isinstance(content, str):
        return marker in content
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") == "text" and marker in b.get("text", "")
            for b in content
        )
    return False


def _is_bookkeeping_bash_cmd(cmd: str) -> bool:
    if "lamin-track-claudecode" in cmd or "lamin-finish-claudecode" in cmd:
        return True
    # legacy: inline python -c form
    return ("ln.Transform(" in cmd and "ln.Run(transform)" in cmd) or (
        "ln.Run.get(uid=" in cmd and "report" in cmd.lower()
    )


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


# --- HTML rendering ---

def _render_block(role: str, btype: str, block: dict) -> str | None:
    if btype == "text":
        text = html.escape(block.get("text", ""))[:_BLOCK_TRUNCATE]
        if not text.strip():
            return None
        return (
            f'<div class="block {role}-text">'
            f'<div class="role-label">{role}</div>'
            f'<div class="content">{text}</div></div>'
        )
    if btype == "thinking":
        thinking = block.get("thinking", "")
        if not thinking.strip():
            return None
        return (
            f'<details class="block thinking">'
            f'<summary>thinking ({role})</summary>'
            f'<div class="content">{html.escape(thinking)[:_BLOCK_TRUNCATE]}</div></details>'
        )
    if btype == "tool_use":
        name = block.get("name", "tool")
        inp = (
            block.get("input", {}).get("command", "")
            if name == "Bash"
            else json.dumps(block.get("input", {}), indent=2)
        )
        return (
            f'<div class="block tool-use">'
            f'<div class="role-label">tool_use: {html.escape(name)}</div>'
            f'<pre class="content">{html.escape(inp)[:_BLOCK_TRUNCATE]}</pre></div>'
        )
    if btype == "tool_result":
        c = block.get("content", "")
        text = (
            "\n".join(b.get("text", "") for b in c if isinstance(b, dict))
            if isinstance(c, list)
            else str(c)
        )
        return (
            f'<div class="block tool-result">'
            f'<div class="role-label">tool_result</div>'
            f'<pre class="content">{html.escape(text)[:_BLOCK_TRUNCATE]}</pre></div>'
        )
    return None


def _render_transcript_html(entries: list[dict]) -> str:
    filtered = [
        msg for msg in entries
        if not _content_has_marker(msg.get("content"), _SKILL_MARKER)
    ]

    bookkeeping_ids: set[str] = {
        block.get("id", "")
        for msg in filtered
        for block in (msg.get("content") or [])
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and block.get("name") == "Bash"
        and _is_bookkeeping_bash_cmd(block.get("input", {}).get("command", ""))
    }

    blocks_html: list[str] = []
    for msg in filtered:
        role = msg.get("role", "")
        content = msg.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue
        for block in content:
            btype = block.get("type")
            if btype == "tool_use" and block.get("id") in bookkeeping_ids:
                continue
            if btype == "tool_result" and block.get("tool_use_id") in bookkeeping_ids:
                continue
            rendered = _render_block(role, btype, block)
            if rendered is not None:
                blocks_html.append(rendered)

    return _HTML_TEMPLATE.format(blocks="\n".join(blocks_html))


# --- transform stamping ---

def _extract_written_script_paths(entries: list[dict]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for msg in entries:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "tool_use" or block.get("name") not in _SCRIPT_TOOL_NAMES:
                continue
            inp = block.get("input", {})
            file_path = next((inp.get(k) for k in _SCRIPT_PATH_KEYS if inp.get(k)), None)
            if not isinstance(file_path, str):
                continue
            p = Path(file_path)
            if p.suffix not in _SUFFIX_TO_KIND:
                continue
            key = str(p)
            if key not in seen:
                seen.add(key)
                paths.append(p)
    return paths


def _stamp_transforms(run: object, entries: list[dict], ln: object) -> None:
    # Primary path: scripts run with LAMIN_INITIATED_BY_RUN_UID create child runs
    already_stamped: set[str] = set()
    for child_run in run.initiated_runs.all():  # type: ignore[attr-defined]
        t = child_run.transform
        already_stamped.add(t.key)
        if t.run_id is None:
            t.run = run
            t.save()

    for path in _extract_written_script_paths(entries):
        if not path.exists() or path.name in already_stamped:
            continue
        kind = _SUFFIX_TO_KIND[path.suffix]
        transform = ln.Transform.filter(key=path.name).one_or_none()  # type: ignore[attr-defined]
        if transform is None:
            transform = ln.Transform(key=path.name, kind=kind)  # type: ignore[attr-defined]
            transform.save()
        if transform.run_id is None:
            transform.run = run
            transform.save()
            _info(f"registered transform: {path.name}")


# --- session finish ---

def finish_claudecode_session() -> None:
    try:
        import lamindb as ln
    except Exception as e:
        _warn(f"lamindb not available, skipping session finish: {e}")
        return

    try:
        if not _instance_connected(ln):
            _warn("no lamindb instance connected, skipping session finish")
            return

        if not _RUN_UID_FILE.exists():
            _warn("no active Claude Code session found, skipping session finish")
            return

        uid = _RUN_UID_FILE.read_text().strip()
        run = ln.Run.get(uid=uid)
        transcript_path = Path(_TRANSCRIPT_PATH_FILE.read_text().strip())

        if not transcript_path.exists():
            _warn(
                f"transcript file not found: {transcript_path} — "
                "closing run without report (is CLAUDE_CODE_SESSION_ID set?)"
            )
            run.finished_at = datetime.now(timezone.utc)
            run.save()
            _RUN_UID_FILE.unlink()
            _TRANSCRIPT_PATH_FILE.unlink()
            return

        entries = _parse_transcript(transcript_path)
        html_doc = _render_transcript_html(entries)

        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False)
        tmp_path = Path(tmp.name)
        try:
            tmp.write(html_doc)
            tmp.close()
            artifact = ln.Artifact(
                tmp.name,
                description="Claude Code session transcript (rendered)",
                run=False,
            ).save()
        finally:
            tmp_path.unlink(missing_ok=True)

        run.report = artifact
        _stamp_transforms(run, entries, ln)

        run.finished_at = datetime.now(timezone.utc)
        run.save()

        _RUN_UID_FILE.unlink()
        _TRANSCRIPT_PATH_FILE.unlink()
        _info(f"finished tracking Claude Code session: {run.uid}")
    except Exception as e:
        _warn(f"lamindb session finish failed, continuing: {e}")
        _warn(traceback.format_exc())



@click.command("lamin-track-claudecode")
@click.option(
    "--description",
    type=str,
    default=None,
    help="One-sentence description of what this session will accomplish.",
)
def _track_main(description: str | None) -> None:
    """Start tracking a Claude Code session in LaminDB."""
    track_claudecode_session(description=description)


@click.command("lamin-finish-claudecode")
def _finish_main() -> None:
    """Finish a tracked Claude Code session and save the transcript report."""
    finish_claudecode_session()
