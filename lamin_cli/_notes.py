from __future__ import annotations

import re
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from pathlib import Path

LAMINDB_RECORD_UID_KEY = "lamindb_record_uid"
_LEGACY_RECORD_UID_KEY = "lamin_record_uid"
_RECORD_UID_KEYS = {LAMINDB_RECORD_UID_KEY, _LEGACY_RECORD_UID_KEY}

# Closing fence must start a line so a `---` inside a value is not the end.
_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n((?:.*\r?\n)*?)---[ \t]*(?:\r?\n|$)")


def _split_front_matter(content: str) -> tuple[str, str] | None:
    match = _FRONT_MATTER_RE.match(content)
    if match is None:
        return None
    return match.group(1), content[match.end() :]


def _unquote_scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _front_matter_key(line: str) -> str | None:
    if line == "" or line[0].isspace() or line.startswith("#"):
        return None
    key, separator, _value = line.partition(":")
    if separator != ":":
        return None
    return key.strip()


def _record_uid_value(line: str) -> str:
    value = _unquote_scalar(line.partition(":")[2].strip())
    if value == "":
        raise click.ClickException(
            f"{_front_matter_key(line)} in front matter is empty."
        )
    return value


def parse_lamindb_record_uid(content: str) -> str | None:
    """Return the record uid from leading YAML front matter.

    Reads `lamindb_record_uid`, and `lamin_record_uid` when that is the only key.
    Returns None when the file has no front matter or the key is absent.
    Raises ClickException when the key is present with an empty value.
    """
    split = _split_front_matter(content)
    if split is None:
        return None
    block, _rest = split
    legacy_value: str | None = None
    for line in block.splitlines():
        key = _front_matter_key(line)
        if key == LAMINDB_RECORD_UID_KEY:
            return _record_uid_value(line)
        if key == _LEGACY_RECORD_UID_KEY and legacy_value is None:
            legacy_value = _record_uid_value(line)
    return legacy_value


def _with_blank_line_before_body(front_matter: str, body: str) -> str:
    if body == "" or body.startswith(("\n", "\r")):
        return f"---\n{front_matter}\n---\n{body}"
    return f"---\n{front_matter}\n---\n\n{body}"


def upsert_lamindb_record_uid(content: str, uid: str) -> str:
    """Insert or replace `lamindb_record_uid` in leading YAML front matter."""
    line = f"{LAMINDB_RECORD_UID_KEY}: {uid}"
    split = _split_front_matter(content)
    if split is None:
        if content == "":
            return f"---\n{line}\n---\n"
        return _with_blank_line_before_body(line, content)

    block, rest = split
    replaced = False
    new_lines: list[str] = []
    for existing in block.splitlines():
        if _front_matter_key(existing) in _RECORD_UID_KEYS:
            if not replaced:
                new_lines.append(line)
                replaced = True
            continue
        new_lines.append(existing)
    if not replaced:
        new_lines.append(line)
    # The matcher consumes the newline that closes the fence. `rest` is whatever
    # followed that newline, including a blank line before the body.
    return _with_blank_line_before_body("\n".join(new_lines), rest)


def parse_note_target(
    target: str, *, allow_extensionless_single: bool = False
) -> tuple[list[str], str] | None:
    normalized = target.strip().replace("\\", "/")
    if normalized == "" or normalized.endswith("/"):
        return None

    raw_parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        return None

    has_slash = len(raw_parts) > 1
    final_segment = raw_parts[-1]
    if final_segment == "README.md":
        return None

    if final_segment.endswith(".md"):
        note_name = final_segment[: -len(".md")]
    elif has_slash or allow_extensionless_single:
        note_name = final_segment
    else:
        return None

    if note_name == "":
        return None

    return raw_parts[:-1], note_name


def extract_note_target_from_path(
    path: Path, *, dev_dir: Path | None
) -> tuple[list[str], str] | None:
    if path.suffix != ".md" or dev_dir is None:
        return None
    try:
        relative_path = path.resolve().relative_to(dev_dir.resolve())
    except ValueError:
        return None
    return parse_note_target(relative_path.as_posix(), allow_extensionless_single=True)


def resolve_note_type_parent(ln, type_chain: list[str]):
    parent_type = None
    resolved_path: list[str] = []
    for type_name in type_chain:
        type_record = ln.Record.filter(
            name__iexact=type_name,
            is_type=True,
            type=parent_type,
        ).one_or_none()
        if type_record is None:
            parent_label = "/".join(resolved_path) if resolved_path else "<root>"
            expected_path = "/".join([*resolved_path, type_name])
            raise click.ClickException(
                f"Record type '{type_name}' not found under '{parent_label}'. "
                f"Expected hierarchy segment '{expected_path}'. Create it first."
            )
        parent_type = type_record
        resolved_path.append(type_name)
    return parent_type


def resolve_note_record(
    *,
    ln,
    type_chain: list[str],
    note_name: str,
    create_if_missing: bool = False,
):
    parent_type = resolve_note_type_parent(ln, type_chain)
    note_record = ln.Record.filter(name=note_name, type=parent_type).one_or_none()
    if note_record is None and create_if_missing:
        note_record = ln.Record(
            name=note_name,
            type=parent_type,
            is_type=True,  # notes should appear in the type hierarchy
        ).save()
    return note_record


def is_path_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
