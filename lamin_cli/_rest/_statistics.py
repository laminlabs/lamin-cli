# ruff: noqa: D301
from __future__ import annotations

from typing import Any

from ._click import click
from ._client import _module_model_path, _print_json, request_json
from ._schema import (
    is_hidden_model,
    load_schema,
    module_schema,
    resolve_model_metadata,
)


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
@click.option("--compact", is_flag=True, default=False, help="Print one-line JSON.")
def statistics(
    module: str | None,
    model: str | None,
    id: int | None,
    models: tuple[str, ...],
    compact: bool,
) -> None:
    """Read table counts or relation counts.

    \b
    Examples:
      lamin rest statistics
      lamin rest statistics core
      lamin rest statistics core ulabel
      lamin rest statistics core artifact 123
      lamin rest statistics --model core.ULabel --model core.Artifact
    """
    if id is not None:
        if models:
            raise click.ClickException("--model cannot be combined with object scope.")
        if module is None or model is None:
            raise click.ClickException(
                "Object relation counts require MODULE MODEL ID."
            )
        data = request_json("get", _relation_counts_path(module, model, id))
        _print_json(data, compact=compact)
        return

    params = _statistics_params(module, model, models)
    data = request_json("get", "statistics", params=params)
    _print_json(data, compact=compact)


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
      lamin rest relation-counts core artifact 123
      lamin rest get core artifact j2qX8G9a --select id --compact
      lamin rest relation-counts core artifact 123 --compact
    """
    data = request_json("get", _relation_counts_path(module, model, id))
    _print_json(data, compact=compact)
