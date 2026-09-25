# ruff: noqa: D301
from __future__ import annotations

from typing import Any

from ._click import click
from ._schema.utils import (
    is_hidden_model,
    load_schema,
    module_schema,
    resolve_model_metadata,
)
from ._utils import _module_model_path, _print_json, request_json


def _format_count_value(value: Any) -> str:
    if isinstance(value, (int, float, str)):
        return str(value)
    return repr(value)


def _format_statistics_markdown(data: Any) -> str:
    lines = ["# Statistics"]
    counts = None
    if isinstance(data, dict):
        if "instance_size" in data:
            lines.append(f"- instance size: {data['instance_size']} bytes")
        counts = data.get("counts")

    lines.append("")
    lines.append("## Counts")
    count_lines = []
    if isinstance(counts, dict):
        for module, models in sorted(counts.items()):
            if isinstance(models, dict):
                for model, count in sorted(models.items()):
                    count_lines.append(
                        f"- {module}.{model}: {_format_count_value(count)}"
                    )
            else:
                count_lines.append(f"- {module}: {_format_count_value(models)}")
    lines.extend(count_lines or ["- none"])
    return "\n".join(lines)


def _format_relation_counts_markdown(data: Any) -> str:
    lines = ["# Relation Counts"]
    if isinstance(data, dict) and data:
        for name, count in sorted(data.items()):
            lines.append(f"- {name}: {_format_count_value(count)}")
    else:
        lines.append("- none")
    return "\n".join(lines)


def _print_statistics(
    data: Any,
    *,
    format_: str,
    compact: bool,
    relation_counts: bool,
) -> None:
    if format_ == "json":
        _print_json(data, compact=compact)
    elif relation_counts:
        click.echo(_format_relation_counts_markdown(data))
    else:
        click.echo(_format_statistics_markdown(data))


def _statistics_models_for_scope(
    schema: dict[str, Any],
    module: str,
    model: str | None,
) -> list[str]:
    if model is not None:
        _, metadata = resolve_model_metadata(schema, module, model)
        class_name = metadata.get("class_name") or model
        return [f"{module}.{class_name}"]

    module_schema_ = module_schema(schema, module)
    models = [
        f"{module}.{metadata.get('class_name') or model_key}"
        for model_key, metadata in sorted(module_schema_.items())
        if isinstance(metadata, dict) and not is_hidden_model(model_key, metadata)
    ]
    if not models:
        raise click.ClickException(f"No visible models found in module '{module}'.")
    return models


def _relation_counts_path(module: str, model: str, id: int) -> str:
    schema = load_schema(refresh=False)
    model_key, _ = resolve_model_metadata(schema, module, model)
    return f"{_module_model_path(module, model_key, id)}/counts"


def _statistics_params(
    module: str | None,
    model: str | None,
    legacy_models: tuple[str, ...],
) -> dict[str, Any] | None:
    if legacy_models and (module or model):
        raise click.ClickException(
            "--model cannot be combined with module/model scope."
        )
    if legacy_models:
        return {"q": list(legacy_models)}
    if module is None:
        return None

    schema = load_schema(refresh=False)
    return {"q": _statistics_models_for_scope(schema, module, model)}


@click.command("statistics", short_help="Show size and table counts.")
@click.argument("module", required=False)
@click.argument("model", required=False)
@click.argument("id", required=False, type=int)
@click.option(
    "--model",
    "models",
    multiple=True,
    help=(
        "Model in module.Class format, for example core.ULabel. "
        "Repeat for multiple models."
    ),
)
@click.option(
    "--format",
    "format_",
    type=click.Choice(["markdown", "json"]),
    default="markdown",
    show_default=True,
    help="Output format.",
)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def statistics(
    module: str | None,
    model: str | None,
    id: int | None,
    models: tuple[str, ...],
    format_: str,
    compact: bool,
) -> None:
    """Read table counts or relation counts.

    \b
    Examples:
      lamin hub statistics
      lamin hub statistics core
      lamin hub statistics core ulabel
      lamin hub statistics core artifact 123
      lamin hub statistics core ulabel --format json --compact
      lamin hub statistics --model core.ULabel --model core.Artifact
    """
    if id is not None:
        if models:
            raise click.ClickException("--model cannot be combined with object scope.")
        if module is None or model is None:
            raise click.ClickException(
                "Object relation counts require MODULE MODEL ID."
            )
        data = request_json("get", _relation_counts_path(module, model, id))
        _print_statistics(
            data,
            format_=format_,
            compact=compact,
            relation_counts=True,
        )
        return

    params = _statistics_params(module, model, models)
    data = request_json("get", "statistics", params=params)
    _print_statistics(data, format_=format_, compact=compact, relation_counts=False)


@click.command(
    "relation-counts",
    hidden=True,
    short_help="Count relations for one object.",
)
@click.argument("module", type=str)
@click.argument("model", type=str)
@click.argument("id", type=int)
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def relation_counts(module: str, model: str, id: int, compact: bool) -> None:
    """Read relation counts for one object by numeric database id.

    \b
    Examples:
      lamin hub relation-counts core artifact 123
      lamin hub get core artifact j2qX8G9a --select id --compact
      lamin hub relation-counts core artifact 123 --compact
    """
    data = request_json("get", _relation_counts_path(module, model, id))
    _print_json(data, compact=compact)
