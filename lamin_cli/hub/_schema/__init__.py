# ruff: noqa: D301
from __future__ import annotations

from lamin_cli.hub._click import click
from lamin_cli.hub._utils import _print_json

from .output import format_schema_markdown, schema_output
from .utils import load_schema, scope_schema


@click.command("schema", short_help="Show instance schema.")
@click.argument("module", required=False)
@click.argument("model", required=False)
@click.option(
    "--format",
    "format_",
    type=click.Choice(["markdown", "json"]),
    default="markdown",
    show_default=True,
    help="Output format for summarized schema metadata.",
)
@click.option(
    "--include-hidden",
    is_flag=True,
    default=False,
    help="Include internal, generated, and link-table fields.",
)
@click.option(
    "--models",
    is_flag=True,
    default=False,
    help="Show model summaries for every module.",
)
@click.option(
    "--raw",
    is_flag=True,
    default=False,
    help="Print the raw schema endpoint response for the requested scope.",
)
@click.option(
    "--refresh",
    is_flag=True,
    default=False,
    help="Refetch schema and replace the local schema cache entry.",
)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def schema(
    module: str | None,
    model: str | None,
    format_: str,
    include_hidden: bool,
    models: bool,
    raw: bool,
    refresh: bool,
    compact: bool,
) -> None:
    """Inspect schema metadata, optionally scoped to a module or model.

    \b
    Examples:
      lamin hub schema
      lamin hub schema --models
      lamin hub schema core
      lamin hub schema core artifact
      lamin hub schema core artifact --format json --compact
      lamin hub schema core --include-hidden
      lamin hub schema core artifact --raw --refresh
    """
    if raw and models:
        raise click.ClickException("--models cannot be combined with --raw.")

    schema_response = load_schema(refresh=refresh)
    scoped = scope_schema(schema_response, module, model)
    if raw:
        _print_json(scoped, compact=compact)
        return

    data = schema_output(
        schema_response,
        module,
        model,
        include_hidden=include_hidden,
        all_models=models,
    )
    if format_ == "json":
        _print_json(data, compact=compact)
    else:
        click.echo(format_schema_markdown(data))
