from __future__ import annotations

from ._click import click
from ._client import (
    _columns,
    _module_model_path,
    _print_json,
    _read_json_object,
    _read_records,
    request_json,
)


@click.command("insert", short_help="Insert rows.")
@click.argument("module", type=str)
@click.argument("model", type=str)
@click.option(
    "--records",
    required=True,
    help="Record object/list as JSON, @path, or -.",
)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def insert(module: str, model: str, records: str, compact: bool) -> None:
    """Insert one or more simple LaminDB rows.

    
    Examples:
      lamin rest insert core ulabel --records '{"name":"treated"}'
      lamin rest insert core project --records @projects.json
    """
    data = request_json(
        "put",
        path=_module_model_path(module, model),
        body=_read_records(records, "--records", allow_object=True),
    )
    _print_json(data, compact=compact)


@click.command("upsert", short_help="Insert or update rows.")
@click.argument("module", type=str)
@click.argument("model", type=str)
@click.option(
    "--records",
    required=True,
    help="Record object/list as JSON, @path, or -.",
)
@click.option(
    "--conflict-column",
    "--conflict-columns",
    "conflict_columns",
    multiple=True,
    required=True,
    help="Conflict column name. Repeat for multiple columns or pass a JSON list.",
)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def upsert(
    module: str,
    model: str,
    records: str,
    conflict_columns: tuple[str, ...],
    compact: bool,
) -> None:
    """Insert or update one or more rows by conflict columns.

    
    Examples:
      lamin rest upsert core ulabel --conflict-column name --records '[{"name":"treated"}]'
      lamin rest upsert core project --conflict-column uid --records @projects.json
    """
    data = request_json(
        "put",
        path=f"{_module_model_path(module, model)}/upsert",
        params={"conflict_columns": _columns(conflict_columns, "--conflict-column")},
        body=_read_records(records, "--records", allow_object=True),
    )
    _print_json(data, compact=compact)


@click.command("update", short_help="Update rows.")
@click.argument("module", type=str)
@click.argument("model", type=str)
@click.argument("uid", required=False)
@click.option("--values", help="Single partial update object as JSON, @path, or -.")
@click.option("--records", help="List of partial update objects as JSON, @path, or -.")
@click.option(
    "--index-column",
    "--index-columns",
    "index_columns",
    multiple=True,
    help="Identifier column name. Repeat for multiple columns or pass a JSON list.",
)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def update(
    module: str,
    model: str,
    uid: str | None,
    values: str | None,
    records: str | None,
    index_columns: tuple[str, ...],
    compact: bool,
) -> None:
    """Partially update one row or a batch of rows.

    
    Examples:
      lamin rest update core ulabel abc12345 --values '{"description":"updated"}'
      lamin rest update core project --index-column uid --records '[{"uid":"abc12345","description":"updated"}]'
    """
    if records is not None:
        if uid or values is not None:
            raise click.ClickException(
                "update with --records cannot also pass uid or --values"
            )
        data = request_json(
            "patch",
            path=f"{_module_model_path(module, model)}/batch-update",
            body={
                "index_columns": _columns(index_columns, "--index-column"),
                "records": _read_records(records, "--records", allow_object=False),
            },
        )
        _print_json(data, compact=compact)
        return

    if not uid or values is None:
        raise click.ClickException(
            "update requires uid and --values, or --records with --index-column"
        )
    if index_columns:
        raise click.ClickException(
            "update without --records cannot pass --index-column"
        )
    data = request_json(
        "patch",
        path=_module_model_path(module, model, uid),
        body=_read_json_object(values, "--values"),
    )
    _print_json(data, compact=compact)


@click.command("delete", short_help="Delete rows.")
@click.argument("module", type=str)
@click.argument("model", type=str)
@click.argument("uid", required=False)
@click.option("--records", help="List of identifier objects as JSON, @path, or -.")
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def delete(
    module: str,
    model: str,
    uid: str | None,
    records: str | None,
    compact: bool,
) -> None:
    """Delete one row or a batch of rows.

    
    Examples:
      lamin rest delete core ulabel abc12345
      lamin rest delete core recordrecord --records '[{"record_id":1,"feature_id":2,"value_id":3}]'
    """
    if records is not None:
        if uid:
            raise click.ClickException(
                "delete accepts either uid or --records, not both"
            )
        data = request_json(
            "post",
            path=f"{_module_model_path(module, model)}/batch-delete",
            body={"records": _read_records(records, "--records", allow_object=False)},
        )
        _print_json(data, compact=compact)
        return

    if not uid:
        raise click.ClickException("delete requires uid or --records")
    data = request_json("delete", path=_module_model_path(module, model, uid))
    _print_json(data, compact=compact)
