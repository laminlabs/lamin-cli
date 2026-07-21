from __future__ import annotations

import html
import re
from typing import Callable

import click

# --- constants ---

BLOCK_TRUNCATE = 4000

HTML_TEMPLATE = """\
<!doctype html>
<html><head><meta charset="utf-8"><title>Session Transcript</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#111;max-width:800px;margin:0 auto;padding:1.5rem;font-size:14px;line-height:1.5}}
ul{{list-style:none}}
li.step{{display:flex;align-items:flex-start;gap:10px;padding:3px 0}}
.dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-top:6px}}
.dg{{background:#23d18b}}.dy{{background:#bbb}}.db{{background:#2472c8}}
.bd{{flex:1;min-width:0}}
.tt{{font-weight:600;color:#111}}
.lb{{font-weight:400;color:#888;font-size:.85em;margin-left:6px}}
.tx{{white-space:pre-wrap;word-wrap:break-word;color:#333;margin-top:2px}}
.user-msg{{background:#eef4fd;border-radius:6px;padding:8px 10px;margin:2px 0}}
.user-msg .tt{{color:#2472c8;font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;margin-bottom:2px}}
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
_ANSI_BASE_COLORS = ["#000", "#cd3131", "#0dbc79", "#e5e510", "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5"]
_ANSI_BRIGHT_COLORS = ["#666", "#f14c4c", "#23d18b", "#f5f543", "#3b8eea", "#d670d6", "#29b8db", "#fff"]


def ansi_to_html(text: str) -> str:
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


def info(msg: str) -> None:
    click.echo(f"✓ {msg}")


def warn(msg: str) -> None:
    click.echo(f"! {msg}", err=True)


# --- lamindb helpers ---


def instance_connected(ln: object) -> bool:
    s = ln.setup.settings  # type: ignore[attr-defined]
    if hasattr(s, "is_configured"):
        return bool(s.is_configured)
    return bool(s._instance_exists)


# --- HTML rendering ---
# Shared across agents. Agent-specific quirks (which tool name means "shell",
# which commands are this agent's own bookkeeping) are passed in as arguments
# rather than hardcoded, so the same renderer works for Claude, Copilot, etc.


def render_thinking(thinking: str) -> str:
    return (
        '<li class="step"><div class="dot dy"></div><div class="bd">'
        f'<details><summary>Thinking</summary>'
        f'<div class="thk">{html.escape(thinking[:BLOCK_TRUNCATE])}</div>'
        "</details></div></li>"
    )


def render_text(text: str) -> str:
    return (
        '<li class="step"><div class="dot dg"></div>'
        f'<div class="bd"><div class="tx">{ansi_to_html(text[:BLOCK_TRUNCATE])}</div></div></li>'
    )


def render_user_text(text: str) -> str:
    return (
        '<li class="step"><div class="dot db"></div>'
        '<div class="bd"><div class="user-msg"><div class="tt">User</div>'
        f'<div class="tx">{ansi_to_html(text[:BLOCK_TRUNCATE])}</div></div></div></li>'
    )


def render_tool(tool_use: dict, tool_result: dict | None, shell_tool_names: frozenset[str]) -> str:
    name = tool_use.get("name", "tool")
    inp = tool_use.get("input", {})

    if name in shell_tool_names:
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
                f'<div class="iopre">{ansi_to_html(out[:BLOCK_TRUNCATE])}</div>'
            )
        return (
            '<li class="step"><div class="dot dg"></div><div class="bd">'
            f'<div class="tt">{html.escape(name.capitalize())}<span class="lb">{html.escape(label)}</span></div>'
            '<div class="io">'
            '<div class="iotag">IN</div>'
            f'<div class="iopre">{html.escape(cmd[:BLOCK_TRUNCATE])}</div>'
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


def render_transcript_html(
    entries: list[dict],
    *,
    is_bookkeeping_bash_cmd: Callable[[str], bool],
    skill_marker: str,
    shell_tool_names: frozenset[str] = frozenset({"Bash"}),
) -> str:
    def content_has_marker(content: object, marker: str) -> bool:
        if isinstance(content, str):
            return marker in content
        if isinstance(content, list):
            return any(
                isinstance(b, dict) and b.get("type") == "text" and marker in b.get("text", "")
                for b in content
            )
        return False

    filtered = [msg for msg in entries if not content_has_marker(msg.get("content"), skill_marker)]

    bookkeeping_ids: set[str] = {
        block.get("id", "")
        for msg in filtered
        for block in (msg.get("content") or [])
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and block.get("name") in shell_tool_names
        and is_bookkeeping_bash_cmd(block.get("input", {}).get("command", ""))
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
                steps.append(render_tool(tool_use, block, shell_tool_names))
            elif btype == "thinking" and role == "assistant":
                thinking = block.get("thinking", "").strip()
                if thinking:
                    steps.append(render_thinking(thinking))
            elif btype == "text" and role == "assistant":
                text = block.get("text", "").strip()
                if text:
                    steps.append(render_text(text))
            elif btype == "text" and role == "user":
                text = block.get("text", "").strip()
                if text:
                    steps.append(render_user_text(text))

    # render any tool_use blocks that never got a result (session interrupted)
    for uid, tool_use in tool_use_map.items():
        if uid not in paired_ids and uid not in bookkeeping_ids:
            steps.append(render_tool(tool_use, None, shell_tool_names))

    return HTML_TEMPLATE.format(steps="\n".join(steps))


# --- transform stamping ---
# The primary path (child runs linked via LAMIN_INITIATED_BY_RUN_UID) is fully
# agent-agnostic. The fallback path (scanning the transcript for write/edit
# tool calls) needs each agent's own tool-name/path-key conventions.


def extract_written_script_paths(
    entries: list[dict],
    *,
    script_tool_names: frozenset[str],
    script_path_keys: tuple[str, ...],
    suffix_to_kind: dict[str, str],
):
    from pathlib import Path

    paths: list[Path] = []
    seen: set[str] = set()
    for msg in entries:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "tool_use" or block.get("name") not in script_tool_names:
                continue
            inp = block.get("input", {})
            file_path = next((inp.get(k) for k in script_path_keys if inp.get(k)), None)
            if not isinstance(file_path, str):
                continue
            p = Path(file_path)
            if p.suffix not in suffix_to_kind:
                continue
            key = str(p)
            if key not in seen:
                seen.add(key)
                paths.append(p)
    return paths


def stamp_transforms(
    run: object,
    entries: list[dict],
    ln: object,
    *,
    script_tool_names: frozenset[str] = frozenset(),
    script_path_keys: tuple[str, ...] = (),
    suffix_to_kind: dict[str, str] | None = None,
) -> None:
    # Primary path: scripts run with LAMIN_INITIATED_BY_RUN_UID create child runs.
    already_stamped: set[str] = set()
    for child_run in run.initiated_runs.all():  # type: ignore[attr-defined]
        t = child_run.transform
        already_stamped.add(t.key)
        if t.run_id is None:
            t.run = run
            t.save()

    if not suffix_to_kind:
        return

    for path in extract_written_script_paths(
        entries,
        script_tool_names=script_tool_names,
        script_path_keys=script_path_keys,
        suffix_to_kind=suffix_to_kind,
    ):
        if not path.exists() or path.name in already_stamped:
            continue
        kind = suffix_to_kind[path.suffix]
        transform = ln.Transform.filter(key=path.name).one_or_none()  # type: ignore[attr-defined]
        if transform is None:
            transform = ln.Transform(key=path.name, kind=kind)  # type: ignore[attr-defined]
            transform.save()
        if transform.run_id is None:
            transform.run = run
            transform.save()
            info(f"registered transform: {path.name}")
