from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from ._click import click


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


def _current_instance() -> tuple[str, str]:
    import lamindb_setup as ln_setup

    instance = ln_setup.settings.instance
    instance_id = getattr(instance, "_id", None)
    api_url = getattr(instance, "api_url", None)
    if instance_id is None:
        raise click.ClickException(
            "No current LaminDB instance id found. Run `lamin connect account/name`."
        )
    if api_url is None:
        raise click.ClickException(
            "No API URL found for the current LaminDB instance. "
            "Run `lamin connect account/name`."
        )
    return str(instance_id), str(api_url).rstrip("/")


def _current_instance_schema_id() -> str | None:
    import lamindb_setup as ln_setup

    instance = ln_setup.settings.instance
    schema_id = getattr(instance, "schema_id", None) or getattr(
        instance, "_schema_id", None
    )
    return None if schema_id is None else str(schema_id)


def _access_token() -> tuple[str | None, bool]:
    import lamindb_setup as ln_setup

    user = ln_setup.settings.user
    if getattr(user, "handle", None) == "anonymous":
        return None, False
    token = getattr(user, "access_token", None)
    return token, token is not None


def _instance_url(path: str) -> str:
    instance_id, api_url = _current_instance()
    return f"{api_url}/instances/{quote(instance_id, safe='')}/{path}"


def _module_model_path(
    module: str,
    model: str,
    id_or_uid: str | int | None = None,
) -> str:
    path = f"modules/{quote(module, safe='')}/{quote(model, safe='')}"
    if id_or_uid is not None:
        path += f"/{quote(str(id_or_uid), safe='')}"
    return path


def request_json(
    method: Literal["delete", "get", "patch", "post", "put"],
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: Any | None = None,
) -> Any:
    from lamindb_setup.core._hub_client import request_with_auth

    url = _instance_url(path)
    token, renew_token = _access_token()
    kwargs: dict[str, Any] = {"params": params or {}}
    if body is not None:
        kwargs["json"] = body
    try:
        response = request_with_auth(url, method, token, renew_token, **kwargs)
    except Exception as error:
        raise click.ClickException(f"{method.upper()} {url} failed: {error}") from error

    if not 200 <= response.status_code < 300:
        msg = f"{method.upper()} {url} failed: {response.status_code} {response.text}"
        raise click.ClickException(msg)

    response_text = str(getattr(response, "text", "") or "")
    response_content = getattr(response, "content", None)
    if (
        response.status_code == 204
        or (response_content is not None and len(response_content) == 0)
        or not response_text.strip()
    ):
        return None

    try:
        return response.json()
    except ValueError as error:
        snippet = response_text[:500]
        raise click.ClickException(
            f"{method.upper()} {url} returned invalid JSON: {snippet}"
        ) from error


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


def _read_records(
    value: str,
    label: str,
    *,
    allow_object: bool,
) -> list[dict[str, Any]] | dict[str, Any]:
    records = _read_json(value, label)
    if isinstance(records, dict):
        if allow_object:
            return records
        raise click.ClickException(f"{label} must be a JSON list of objects")
    if not isinstance(records, list):
        expected = "a JSON object or list of objects" if allow_object else "a JSON list"
        raise click.ClickException(f"{label} must be {expected}")
    if not all(isinstance(record, dict) for record in records):
        raise click.ClickException(f"{label} must contain only JSON objects")
    return records


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
