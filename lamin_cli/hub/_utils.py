from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ._click import click
from ._client import _current_instance, request_json
from ._client import module_model_path as _module_model_path


def _read_text_value(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "-":
        return sys.stdin.read()
    if value.startswith("@"):
        return Path(value[1:]).read_text()
    return value


def _read_json(value: str | None, label: str) -> Any:
    text = _read_text_value(value)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise click.ClickException(f"{label} must be valid JSON: {error}") from error


def _read_json_object(value: str | None, label: str) -> dict[str, Any] | None:
    parsed = _read_json(value, label)
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        raise click.ClickException(f"{label} must be a JSON object")
    return parsed


def _read_json_list(value: str | None, label: str) -> list[Any] | None:
    parsed = _read_json(value, label)
    if parsed is None:
        return None
    if not isinstance(parsed, list):
        raise click.ClickException(f"{label} must be a JSON list")
    return parsed


def _parse_string_list(values: tuple[str, ...], label: str) -> list[str] | None:
    if not values:
        return None
    if len(values) == 1 and values[0].lstrip().startswith("["):
        parsed = _read_json_list(values[0], label)
        if parsed is None:
            return None
        if not all(isinstance(value, str) for value in parsed):
            raise click.ClickException(f"{label} JSON list must only contain strings")
        return parsed
    return list(values)


def _print_json(data: Any, *, compact: bool) -> None:
    indent = None if compact else 2
    click.echo(json.dumps(data, indent=indent, default=str))


def _current_instance_schema_id() -> str | None:
    import lamindb_setup as ln_setup

    instance = ln_setup.settings.instance
    schema_id = getattr(instance, "schema_id", None) or getattr(
        instance, "_schema_id", None
    )
    return None if schema_id is None else str(schema_id)


def _select_body(body: str | None, select: tuple[str, ...]) -> dict[str, Any]:
    request_body = _read_json_object(body, "--body") or {}
    select_list = _parse_string_list(select, "--select")
    if select_list:
        request_body["select"] = select_list
    return request_body


def _records_body(
    body: str | None,
    select: tuple[str, ...],
    filter_: str | None,
    order_by: str | None,
    search: str | None,
    search_in: tuple[str, ...],
) -> dict[str, Any]:
    request_body = _select_body(body, select)
    search_in_list = _parse_string_list(search_in, "--search-in")
    if filter_ is not None:
        request_body["filter"] = _read_json_object(filter_, "--filter")
    if order_by is not None:
        request_body["order_by"] = _read_json_list(order_by, "--order-by")
    if search is not None:
        request_body["search"] = search
    if search_in_list:
        request_body["search_in"] = search_in_list
    return request_body


def _read_objects(
    value: str,
    label: str,
    *,
    allow_object: bool,
) -> list[dict[str, Any]] | dict[str, Any]:
    objects = _read_json(value, label)
    if isinstance(objects, dict):
        if allow_object:
            return objects
        raise click.ClickException(f"{label} must be a JSON list of objects")
    if not isinstance(objects, list):
        expected = "a JSON object or list of objects" if allow_object else "a JSON list"
        raise click.ClickException(f"{label} must be {expected}")
    if not all(isinstance(obj, dict) for obj in objects):
        raise click.ClickException(f"{label} must contain only JSON objects")
    return objects


def _columns(values: tuple[str, ...], label: str) -> list[str]:
    columns = _parse_string_list(values, label)
    if not columns:
        raise click.ClickException(f"{label} requires at least one column")
    return columns


def _query_params(
    *,
    limit_to_many: int,
    include_foreign_keys: bool,
    limit: int | None = None,
    offset: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit_to_many": limit_to_many}
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    if include_foreign_keys:
        params["include_foreign_keys"] = "true"
    return params
