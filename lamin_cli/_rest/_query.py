from __future__ import annotations

from typing import Any

from ._click import click
from ._client import (
    _module_model_path,
    _print_json,
    _query_params,
    _records_body,
    _select_body,
    request_json,
)


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


@click.command("list", short_help="List objects.")
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
    r"""Query multiple LaminDB objects.

    \b
    Examples:
      lamin rest list core ulabel --limit 20
      lamin rest list core artifact --select uid --select key --search training --limit 10
      lamin rest list core artifact --search training --search-in ulabels.name --limit 10
      lamin rest list core artifact --select uid --select key --select 'run(transform(uid,key))'
      lamin rest list core record --filter '{"and":[{"is_type":{"eq":true}},{"name":{"contains":"dataset"}}]}' --select uid --select name
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


@click.command("get", short_help="Get one object.")
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
    r"""Query one LaminDB object by numeric id or uid.

    \b
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


@click.command("schema", short_help="Show instance schema.")
@click.argument("module", required=False)
@click.argument("model", required=False)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def schema(module: str | None, model: str | None, compact: bool) -> None:
    r"""Print raw instance schema JSON, optionally scoped to a module or model.

    \b
    Examples:
      lamin rest schema
      lamin rest schema core
      lamin rest schema core artifact --compact
    """
    data = _scope_schema(request_json("get", "schema"), module, model)
    _print_json(data, compact=compact)


@click.command("statistics", short_help="Show size and table counts.")
@click.option(
    "--model",
    "models",
    multiple=True,
    help=(
        "Model in module.Class format, for example core.ULabel. "
        "Repeat for multiple models."
    ),
)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def statistics(models: tuple[str, ...], compact: bool) -> None:
    r"""Read instance artifact size and table counts.

    \b
    Examples:
      lamin rest statistics
      lamin rest statistics --model core.ULabel --model core.Artifact
      lamin rest statistics --model core.Record --compact
    """
    params = {"q": list(models)} if models else None
    data = request_json("get", "statistics", params=params)
    _print_json(data, compact=compact)


@click.command("relation-counts", short_help="Count relations for one object.")
@click.argument("module", type=str)
@click.argument("model", type=str)
@click.argument("id", type=int)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def relation_counts(module: str, model: str, id: int, compact: bool) -> None:
    r"""Read relation counts for one LaminDB object by numeric database id.

    \b
    Examples:
      lamin rest relation-counts core artifact 123
      lamin rest get core artifact j2qX8G9a --select id --compact
      lamin rest relation-counts core artifact 123 --compact
    """
    data = request_json("get", f"{_module_model_path(module, model, id)}/counts")
    _print_json(data, compact=compact)
