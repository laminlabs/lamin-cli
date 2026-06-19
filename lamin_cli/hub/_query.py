# ruff: noqa: D301
from __future__ import annotations

from ._click import click
from ._utils import (
    _module_model_path,
    _print_json,
    _query_params,
    _records_body,
    _select_body,
    request_json,
)


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
    """Query multiple objects.

    \b
    Examples:
      lamin hub list core ulabel --limit 20
      lamin hub list core artifact --select uid --select key --search training --limit 10
      lamin hub list core artifact --search training --search-in ulabels.name --limit 10
      lamin hub list core artifact --select uid --select key --select 'run(transform(uid,key))'
      lamin hub list core record --filter '{"and":[{"is_type":{"eq":true}},{"name":{"contains":"dataset"}}]}' --select uid --select name
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
    """Query one object by numeric id or uid.

    \b
    Examples:
      lamin hub get core artifact j2qX8G9a
      lamin hub get core artifact j2qX8G9a --select uid --select key --select description
      lamin hub get core record abc12345 --select uid --select name --select 'schema(uid,name)'
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
