from __future__ import annotations

import html
import json
import os
import re
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

import click

# --- constants ---

_CLAUDE_DIR = Path(".claude")
_TRANSFORM_KEY = "__claudecode__"
_TRANSFORM_UID = "SnfuhjObaAKR0000"
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
<html><head><meta charset="utf-8"><title>Session Transcript</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#111;max-width:800px;margin:0 auto;padding:1.5rem;font-size:14px;line-height:1.5}}
ul{{list-style:none}}
li.step{{display:flex;align-items:flex-start;gap:10px;padding:3px 0}}
.dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-top:6px}}
.dg{{background:#23d18b}}.dy{{background:#bbb}}
.bd{{flex:1;min-width:0}}
.tt{{font-weight:600;color:#111}}
.lb{{font-weight:400;color:#888;font-size:.85em;margin-left:6px}}
.tx{{white-space:pre-wrap;word-wrap:break-word;color:#333;margin-top:2px}}
.io{{margin-top:6px;border-radius:4px;border:1px solid #e5e5e5;overflow:hidden;font-family:ui-monospace,monospace;font-size:.82rem}}
.iotag{{padding:2px 8px;background:#f5f5f5;color:#888;font-size:.72rem;font-weight:700;letter-spacing:.08em}}
.iopre{{padding:6px 10px;background:#fafafa;white-space:pre-wrap;word-wrap:break-word;color:#333}}
details summary{{cursor:pointer;color:#888;font-size:.85rem;list-style:none}}
details summary::-webkit-details-marker{{display:none}}
details summary::before{{content:'▶ ';font-size:.7em}}
details[open] summary::before{{content:'▼ '}}
.thk{{margin-top:4px;color:#666;font-size:.85rem;white-space:pre-wrap;word-wrap:break-word;padding-left:8px;border-left:2px solid #e5e5e5}}
.todos{{margin-top:4px}}
.todo{{display:flex;gap:6px;color:#333;font-size:.88rem;padding:1px 0}}
.done{{color:#aaa;text-decoration:line-through}}
</style></head>
<body><ul>
{steps}
</ul></body></html>"""

_ANSI_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_ANSI_BASE_COLORS = ["#000","#cd3131","#0dbc79","#e5e510","#2472c8","#bc3fbc","#11a8cd","#e5e5e5"]
_ANSI_BRIGHT_COLORS = ["#666","#f14c4c","#23d18b","#f5f543","#3b8eea","#d670d6","#29b8db","#fff"]


def _ansi_to_html(text: str) -> str:
    """Convert ANSI SGR color codes to HTML spans; HTML-escape all other text."""
    result: list[str] = []
    style: dict[str, str] = {}
    span_open = False
    cursor = 0

    def css(s: dict[str, str]) -> str:
        parts = []
        if "fg" in s:
            parts.append(f"color:{s['fg']}")
        if "bg" in s:
            parts.append(f"background:{s['bg']}")
        if s.get("bold"):
            parts.append("font-weight:700")
        return ";".join(parts)

    for m in _ANSI_SGR.finditer(text):
        result.append(html.escape(text[cursor:m.start()]))
        cursor = m.end()
        codes = [int(c) for c in m.group(1).split(";") if c] if m.group(1) else [0]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                style = {}
            elif c == 1:
                style["bold"] = "1"
            elif 30 <= c <= 37:
                style["fg"] = _ANSI_BASE_COLORS[c - 30]
            elif 90 <= c <= 97:
                style["fg"] = _ANSI_BRIGHT_COLORS[c - 90]
            elif 40 <= c <= 47:
                style["bg"] = _ANSI_BASE_COLORS[c - 40]
            elif c == 39:
                style.pop("fg", None)
            elif c == 49:
                style.pop("bg", None)
            i += 1
        new_css = css(style)
        if span_open:
            result.append("</span>")
            span_open = False
        if new_css:
            result.append(f'<span style="{new_css}">')
            span_open = True

    result.append(html.escape(text[cursor:]))
    if span_open:
        result.append("</span>")
    return "".join(result)


# --- output helpers ---


def _info(msg: str) -> None:
    click.echo(f"✓ {msg}")


def _warn(msg: str) -> None:
    click.echo(f"! {msg}", err=True)


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


def _instance_connected(ln: object) -> bool:
    s = ln.setup.settings  # type: ignore[attr-defined]
    if hasattr(s, "is_configured"):
        return bool(s.is_configured)
    return bool(s._instance_exists)


# --- session start ---


def track_claudecode_session(name: str | None = None) -> None:
    try:
        import lamindb as ln
    except Exception as e:
        _warn(f"lamindb not available, skipping session tracking: {e}")
        return

    try:
        if not _instance_connected(ln):
            _warn("no lamindb instance connected, skipping session tracking")
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
        _info(f"started tracking Claude Code session: {run.uid}")
    except Exception as e:
        _warn(f"lamindb session tracking failed, continuing without tracking: {e}")


# --- transcript parsing ---


def _content_has_marker(content: object, marker: str) -> bool:
    if isinstance(content, str):
        return marker in content
    if isinstance(content, list):
        return any(
            isinstance(b, dict)
            and b.get("type") == "text"
            and marker in b.get("text", "")
            for b in content
        )
    return False


def _is_bookkeeping_bash_cmd(cmd: str) -> bool:
    if "lamin track claude" in cmd or "lamin track finish" in cmd:
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


def _render_thinking(thinking: str) -> str:
    return (
        '<li class="step"><div class="dot dy"></div><div class="bd">'
        f'<details><summary>Thinking</summary>'
        f'<div class="thk">{html.escape(thinking[:_BLOCK_TRUNCATE])}</div>'
        "</details></div></li>"
    )


def _render_text(text: str) -> str:
    return (
        '<li class="step"><div class="dot dg"></div>'
        f'<div class="bd"><div class="tx">{_ansi_to_html(text[:_BLOCK_TRUNCATE])}</div></div></li>'
    )


def _render_tool(tool_use: dict, tool_result: dict | None) -> str:
    name = tool_use.get("name", "tool")
    inp = tool_use.get("input", {})

    if name == "Bash":
        cmd = inp.get("command", "")
        label = (cmd.split("\n")[0] if cmd else "")[:80]
        out_html = ""
        if tool_result is not None:
            c = tool_result.get("content", "")
            out = (
                "\n".join(b.get("text", "") for b in c if isinstance(b, dict))
                if isinstance(c, list)
                else str(c)
            )
            out_html = (
                '<div class="iotag">OUT</div>'
                f'<div class="iopre">{_ansi_to_html(out[:_BLOCK_TRUNCATE])}</div>'
            )
        return (
            '<li class="step"><div class="dot dg"></div><div class="bd">'
            f'<div class="tt">Bash<span class="lb">{html.escape(label)}</span></div>'
            '<div class="io">'
            '<div class="iotag">IN</div>'
            f'<div class="iopre">{html.escape(cmd[:_BLOCK_TRUNCATE])}</div>'
            f"{out_html}"
            "</div></div></li>"
        )

    if name == "TodoWrite":
        todos = inp.get("todos", [])
        items = []
        for todo in todos[:30]:
            done = todo.get("status") == "completed"
            check = "☑" if done else "☐"
            cls = "todo done" if done else "todo"
            items.append(
                f'<div class="{cls}"><span>{check}</span>{html.escape(todo.get("content", ""))}</div>'
            )
        return (
            '<li class="step"><div class="dot dg"></div><div class="bd">'
            '<div class="tt">Update Todos</div>'
            f'<div class="todos">{"".join(items)}</div>'
            "</div></li>"
        )

    label = inp.get("skill", inp.get("name", "")) if name == "Skill" else ""
    return (
        '<li class="step"><div class="dot dg"></div><div class="bd">'
        f'<div class="tt">{html.escape(name)}'
        + (f'<span class="lb">{html.escape(str(label))}</span>' if label else "")
        + "</div></div></li>"
    )


def _render_transcript_html(entries: list[dict]) -> str:
    filtered = [
        msg
        for msg in entries
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

    tool_use_map: dict[str, dict] = {
        block.get("id", ""): block
        for msg in filtered
        for block in (msg.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
    }

    paired_ids: set[str] = set()
    steps: list[str] = []

    for msg in filtered:
        role = msg.get("role", "")
        content = msg.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue
        for block in content:
            btype = block.get("type")
            if btype == "tool_use":
                continue  # rendered when paired tool_result is seen
            if btype == "tool_result":
                use_id = block.get("tool_use_id", "")
                if use_id in bookkeeping_ids:
                    continue
                tool_use = tool_use_map.get(use_id, {})
                paired_ids.add(use_id)
                steps.append(_render_tool(tool_use, block))
            elif btype == "thinking" and role == "assistant":
                thinking = block.get("thinking", "").strip()
                if thinking:
                    steps.append(_render_thinking(thinking))
            elif btype == "text" and role == "assistant":
                text = block.get("text", "").strip()
                if text:
                    steps.append(_render_text(text))

    # render any tool_use blocks that never got a result (session interrupted)
    for uid, tool_use in tool_use_map.items():
        if uid not in paired_ids and uid not in bookkeeping_ids:
            steps.append(_render_tool(tool_use, None))

    return _HTML_TEMPLATE.format(steps="\n".join(steps))


# --- transform stamping ---


def _extract_written_script_paths(entries: list[dict]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for msg in entries:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                block.get("type") != "tool_use"
                or block.get("name") not in _SCRIPT_TOOL_NAMES
            ):
                continue
            inp = block.get("input", {})
            file_path = next(
                (inp.get(k) for k in _SCRIPT_PATH_KEYS if inp.get(k)), None
            )
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

        run_uid_file = _run_uid_file()
        if not run_uid_file.exists():
            _warn("no active Claude Code session found, skipping session finish")
            return

        uid = run_uid_file.read_text().strip()
        run = ln.Run.get(uid=uid)
        transcript_path = Path(_transcript_path_file().read_text().strip())

        # The path stored at session start can be stale if it was derived from a
        # cwd that differs from Claude Code's launch dir; re-resolve as a fallback.
        if not transcript_path.exists():
            transcript_path = _get_transcript_path()

        if not transcript_path.exists():
            _warn(
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

        run._status_code = 0  # completed
        run.finished_at = datetime.now(timezone.utc)
        run.save()

        run_uid_file.unlink()
        _transcript_path_file().unlink()
        _info(f"finished tracking Claude Code session: {run.uid}")
    except Exception as e:
        _warn(f"lamindb session finish failed, continuing: {e}")
        _warn(traceback.format_exc())
