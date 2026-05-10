from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from pathlib import Path


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
