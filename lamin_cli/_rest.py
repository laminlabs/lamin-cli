from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import click


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
    method: Literal["get", "post"],
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


def _scope_schema(
    schema: Any,
    module: str | None,
    model: str | None,
) -> Any:
    if module is None:
        return schema
    if not isinstance(schema, dict):
        raise click.ClickException("Schema response must be a JSON object.")
    if module not in schema:
        raise click.ClickException(
            f"Unknown module '{module}'. Available modules: {sorted(schema)}"
        )
    module_schema = schema[module]
    if model is None:
        return module_schema
    if not isinstance(module_schema, dict):
        raise click.ClickException(f"Schema module '{module}' must be a JSON object.")
    if model not in module_schema:
        raise click.ClickException(
            f"Unknown model '{module}.{model}'. "
            f"Available models in {module}: {sorted(module_schema)}"
        )
    return module_schema[model]


@click.group()
def rest():
    """Query the LaminDB REST API."""


@rest.command("list")
@click.argument("module", type=str)
@click.argument("model", type=str)
@click.option("--body", help="Full request body as JSON, @path, or -.")
@click.option(
    "--select",
    "select",
    multiple=True,
    help="Field or relation select. Repeat for multiple fields or pass a JSON list.",
)
@click.option("--filter", "filter_", help="Filter JSON object.")
@click.option(
    "--order-by",
    help='order_by JSON list, e.g. [{"field":"created_at","descending":true}].',
)
@click.option("--search", help="Search term.")
@click.option(
    "--search-in",
    multiple=True,
    help="Search field. Repeat for multiple fields or pass a JSON list.",
)
@click.option(
    "--limit",
    type=int,
    default=50,
    show_default=True,
    help="Maximum rows to return.",
)
@click.option("--offset", type=int, default=0, show_default=True, help="Rows to skip.")
@click.option(
    "--limit-to-many",
    type=int,
    default=10,
    show_default=True,
    help="Maximum related rows returned for each to-many relation.",
)
@click.option(
    "--include-foreign-keys",
    is_flag=True,
    default=False,
    help="Include foreign key fields in the response.",
)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def list_records(
    module: str,
    model: str,
    body: str | None,
    select: tuple[str, ...],
    filter_: str | None,
    order_by: str | None,
    search: str | None,
    search_in: tuple[str, ...],
    limit: int,
    offset: int,
    limit_to_many: int,
    include_foreign_keys: bool,
    compact: bool,
) -> None:
    """Query multiple LaminDB objects.

    
    Examples:
      lamin rest list core ulabel --limit 20
      lamin rest list core artifact --select uid --select key --search training --limit 10
      lamin rest list core artifact --search training --search-in ulabels.name --limit 10
      lamin rest list core artifact --select uid --select key --select 'run(transform(uid,key))'
      lamin rest list core record --filter '{"is_type":{"eq":true}}' --select uid --select name
    """
    data = request_json(
        "post",
        path=_module_model_path(module, model),
        params=_query_params(
            limit=limit,
            offset=offset,
            limit_to_many=limit_to_many,
            include_foreign_keys=include_foreign_keys,
        ),
        body=_records_body(body, select, filter_, order_by, search, search_in),
    )
    _print_json(data, compact=compact)


@rest.command("get")
@click.argument("module", type=str)
@click.argument("model", type=str)
@click.argument("id_or_uid", type=str)
@click.option("--body", help="Full request body as JSON, @path, or -.")
@click.option(
    "--select",
    "select",
    multiple=True,
    help="Field or relation select. Repeat for multiple fields or pass a JSON list.",
)
@click.option(
    "--limit-to-many",
    type=int,
    default=10,
    show_default=True,
    help="Maximum related rows returned for each to-many relation.",
)
@click.option(
    "--include-foreign-keys",
    is_flag=True,
    default=False,
    help="Include foreign key fields in the response.",
)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def get_record(
    module: str,
    model: str,
    id_or_uid: str,
    body: str | None,
    select: tuple[str, ...],
    limit_to_many: int,
    include_foreign_keys: bool,
    compact: bool,
) -> None:
    """Query one LaminDB object by numeric id or uid.

    
    Examples:
      lamin rest get core artifact j2qX8G9a
      lamin rest get core artifact j2qX8G9a --select uid --select key --select description
      lamin rest get core record abc12345 --select uid --select name --select 'schema(uid,name)'
    """
    data = request_json(
        "post",
        path=_module_model_path(module, model, id_or_uid),
        params=_query_params(
            limit_to_many=limit_to_many,
            include_foreign_keys=include_foreign_keys,
        ),
        body=_select_body(body, select),
    )
    _print_json(data, compact=compact)


@rest.command("schema")
@click.argument("module", required=False)
@click.argument("model", required=False)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def schema(module: str | None, model: str | None, compact: bool) -> None:
    """Print raw instance schema JSON, optionally scoped to a module or model.

    
    Examples:
      lamin rest schema
      lamin rest schema core
      lamin rest schema core artifact --compact
    """
    data = _scope_schema(request_json("get", "schema"), module, model)
    _print_json(data, compact=compact)


@rest.command("statistics")
@click.option(
    "--model",
    "models",
    multiple=True,
    help="Model in module.Class format, for example core.ULabel. Repeat for multiple models.",
)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def statistics(models: tuple[str, ...], compact: bool) -> None:
    """Read instance artifact size and table counts.

    
    Examples:
      lamin rest statistics
      lamin rest statistics --model core.ULabel --model core.Artifact
      lamin rest statistics --model core.Record --compact
    """
    params = {"q": list(models)} if models else None
    data = request_json("get", "statistics", params=params)
    _print_json(data, compact=compact)


@rest.command("relation-counts")
@click.argument("module", type=str)
@click.argument("model", type=str)
@click.argument("id", type=int)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def relation_counts(module: str, model: str, id: int, compact: bool) -> None:
    """Read relation counts for one LaminDB object by numeric database id.

    
    Examples:
      lamin rest relation-counts core artifact 123
      lamin rest get core artifact j2qX8G9a --select id --compact
      lamin rest relation-counts core artifact 123 --compact
    """
    data = request_json("get", f"{_module_model_path(module, model, id)}/counts")
    _print_json(data, compact=compact)
