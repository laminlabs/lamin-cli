from __future__ import annotations

import re
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from pathlib import Path

LAMINDB_KEY = "lamindb"
LAMINDB_UID_FIELD = "uid"
_LAMINDB_UID_BLOCK = f"{LAMINDB_KEY}:\n  {LAMINDB_UID_FIELD}: {{uid}}"

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


def _scalar(line: str, label: str) -> str:
    value = _unquote_scalar(line.partition(":")[2].strip())
    if value == "":
        raise click.ClickException(f"{label} in front matter is empty.")
    return value


def _nested_key(line: str) -> str | None:
    if line == "" or not line[0].isspace() or line.lstrip().startswith("#"):
        return None
    stripped = line.strip()
    key, separator, _value = stripped.partition(":")
    if separator != ":" or key == "" or " " in key:
        return None
    return key


def parse_lamindb_record_uid(content: str) -> str | None:
    """Return `lamindb.uid` from leading YAML front matter.

    Returns None when the file has no front matter or the key is absent.
    Raises ClickException when the key is present with an empty value.
    """
    split = _split_front_matter(content)
    if split is None:
        return None
    block, _rest = split
    in_lamindb = False
    for line in block.splitlines():
        key = _front_matter_key(line)
        if key is not None:
            in_lamindb = key == LAMINDB_KEY and line.partition(":")[2].strip() == ""
            continue
        if in_lamindb and _nested_key(line) == LAMINDB_UID_FIELD:
            return _scalar(line, f"{LAMINDB_KEY}.{LAMINDB_UID_FIELD}")
    return None


def _with_blank_line_before_body(front_matter: str, body: str) -> str:
    if body == "" or body.startswith(("\n", "\r")):
        return f"---\n{front_matter}\n---\n{body}"
    return f"---\n{front_matter}\n---\n\n{body}"


def _uid_line(uid: str) -> str:
    return f"  {LAMINDB_UID_FIELD}: {uid}"


def _upsert_front_matter_lines(lines: list[str], uid: str) -> list[str]:
    """Write `lamindb.uid`, leaving other front matter fields in place."""
    new_lines: list[str] = []
    wrote_uid = False
    i = 0
    while i < len(lines):
        line = lines[i]
        key = _front_matter_key(line)
        if key == LAMINDB_KEY and line.partition(":")[2].strip() == "":
            new_lines.append(f"{LAMINDB_KEY}:")
            i += 1
            replaced = False
            while i < len(lines) and _front_matter_key(lines[i]) is None:
                if _nested_key(lines[i]) == LAMINDB_UID_FIELD:
                    if not replaced:
                        new_lines.append(_uid_line(uid))
                        replaced = True
                        wrote_uid = True
                    i += 1
                    continue
                new_lines.append(lines[i])
                i += 1
            if not replaced:
                new_lines.append(_uid_line(uid))
                wrote_uid = True
            continue
        new_lines.append(line)
        i += 1
    if not wrote_uid:
        new_lines.extend((f"{LAMINDB_KEY}:", _uid_line(uid)))
    return new_lines


def upsert_lamindb_record_uid(content: str, uid: str) -> str:
    """Insert or replace `lamindb.uid` in leading YAML front matter."""
    block_text = _LAMINDB_UID_BLOCK.format(uid=uid)
    split = _split_front_matter(content)
    if split is None:
        if content == "":
            return f"---\n{block_text}\n---\n"
        return _with_blank_line_before_body(block_text, content)

    block, rest = split
    new_lines = _upsert_front_matter_lines(block.splitlines(), uid)
    # The matcher consumes the newline that closes the fence. `rest` is whatever
    # followed that newline, including a blank line before the body.
    return _with_blank_line_before_body("\n".join(new_lines), rest)


def _strip_front_matter_lines(lines: list[str]) -> list[str]:
    """Drop `lamindb.uid`. Keep every other field."""
    kept: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        key = _front_matter_key(line)
        if key == LAMINDB_KEY and line.partition(":")[2].strip() == "":
            i += 1
            children: list[str] = []
            while i < len(lines) and _front_matter_key(lines[i]) is None:
                if _nested_key(lines[i]) != LAMINDB_UID_FIELD:
                    children.append(lines[i])
                i += 1
            if any(child.strip() for child in children):
                kept.append(f"{LAMINDB_KEY}:")
                kept.extend(children)
            continue
        kept.append(line)
        i += 1
    return kept


def strip_lamindb_record_uid(content: str) -> str:
    """Remove `lamindb.uid` from leading front matter before storing a note.

    Other front matter, including other `lamindb` fields, stays. When that
    leaves the front matter empty, the whole block is removed.
    """
    split = _split_front_matter(content)
    if split is None:
        return content
    block, rest = split
    kept = _strip_front_matter_lines(block.splitlines())
    if not any(line.strip() for line in kept):
        if rest.startswith("\r\n"):
            return rest[2:]
        if rest.startswith(("\n", "\r")):
            return rest[1:]
        return rest
    return "---\n" + "\n".join(kept) + "\n---\n" + rest


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


PROJECTS_FOLDER = "Projects"


def is_projects_folder(name: str) -> bool:
    return name.casefold() == PROJECTS_FOLDER.casefold()


def is_project_note_target(type_chain: list[str]) -> bool:
    """True when the note lives under the dev-dir `Projects` folder."""
    return bool(type_chain) and is_projects_folder(type_chain[0])


def project_type_chain(type_chain: list[str]) -> list[str]:
    """Project type path with the leading `Projects` folder removed."""
    if is_project_note_target(type_chain):
        return type_chain[1:]
    return list(type_chain)


def _resolve_type_parent(model, type_chain: list[str], *, kind: str):
    parent_type = None
    resolved_path: list[str] = []
    for type_name in type_chain:
        type_record = model.filter(
            name__iexact=type_name,
            is_type=True,
            type=parent_type,
        ).one_or_none()
        if type_record is None:
            parent_label = "/".join(resolved_path) if resolved_path else "<root>"
            expected_path = "/".join([*resolved_path, type_name])
            raise click.ClickException(
                f"{kind} type '{type_name}' not found under '{parent_label}'. "
                f"Expected hierarchy segment '{expected_path}'. Create it first."
            )
        parent_type = type_record
        resolved_path.append(type_name)
    return parent_type


def resolve_note_type_parent(ln, type_chain: list[str]):
    return _resolve_type_parent(ln.Record, type_chain, kind="Record")


def resolve_project_type_parent(ln, type_chain: list[str]):
    return _resolve_type_parent(ln.Project, type_chain, kind="Project")


def resolve_note_project(
    *,
    ln,
    type_chain: list[str],
    note_name: str,
    create_if_missing: bool = False,
):
    parent_type = resolve_project_type_parent(ln, type_chain)
    project = ln.Project.filter(name=note_name, type=parent_type).one_or_none()
    if project is None and create_if_missing:
        project = ln.Project(name=note_name, type=parent_type).save()
    return project


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
        # Root notes and notes under a page stay in the type hierarchy.
        # A record frame only accepts data records as rows.
        note_record = ln.Record(
            name=note_name,
            type=parent_type,
            is_type=parent_type is None or not parent_type.is_frame,
        ).save()
    return note_record


def is_path_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
