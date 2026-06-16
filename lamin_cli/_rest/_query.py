# ruff: noqa: D301
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ._click import click
from ._client import (
    _current_instance,
    _current_instance_schema_id,
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


def _module_schema(schema: dict[str, Any], module: str) -> dict[str, Any]:
    module_schema = _scope_schema(schema, module, None)
    if not isinstance(module_schema, dict):
        raise click.ClickException(f"Schema module '{module}' must be a JSON object.")
    return module_schema


def _resolve_model_metadata(
    schema: dict[str, Any],
    module: str,
    model: str,
) -> tuple[str, dict[str, Any]]:
    module_schema = _module_schema(schema, module)
    if model in module_schema:
        metadata = module_schema[model]
        if not isinstance(metadata, dict):
            raise click.ClickException(
                f"Schema model '{module}.{model}' must be a JSON object."
            )
        return model, metadata

    normalized_model = model.lower()
    matches = [
        (model_key, metadata)
        for model_key, metadata in module_schema.items()
        if isinstance(metadata, dict)
        and str(metadata.get("class_name", "")).lower() == normalized_model
    ]
    if not matches:
        matches = [
            (model_key, metadata)
            for model_key, metadata in module_schema.items()
            if isinstance(metadata, dict) and model_key.lower() == normalized_model
        ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise click.ClickException(
            f"Ambiguous model '{module}.{model}'. Available models in {module}: "
            f"{sorted(module_schema)}"
        )
    raise click.ClickException(
        f"Unknown model '{module}.{model}'. Available models in {module}: "
        f"{sorted(module_schema)}"
    )


def _load_schema(*, refresh: bool) -> dict[str, Any]:
    cache_path = _current_schema_cache_path()
    if cache_path is not None and not refresh:
        cached = _read_cached_schema(cache_path)
        if cached is not None:
            return cached

    schema = request_json("get", "schema")
    if not isinstance(schema, dict):
        raise click.ClickException("Schema response must be a JSON object.")
    if cache_path is not None:
        _write_cached_schema(cache_path, schema)
    return schema


def _current_schema_cache_path() -> Path | None:
    try:
        schema_id = _current_instance_schema_id()
        if schema_id is None:
            return None
        instance_id, _ = _current_instance()
    except Exception:
        return None
    return _schema_cache_path(instance_id, schema_id)


def _schema_cache_path(instance_id: str, schema_id: str) -> Path:
    cache_root = os.environ.get("LAMIN_REST_SCHEMA_CACHE_DIR")
    if cache_root:
        root = Path(cache_root).expanduser()
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "lamin"
    return (
        root / "rest" / "schemas" / _safe_cache_part(instance_id) / f"{schema_id}.json"
    )


def _safe_cache_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


def _read_cached_schema(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except OSError as error:
        raise click.ClickException(
            f"Could not read schema cache at {path}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise click.ClickException(
            f"Schema cache at {path} is not valid JSON. "
            "Run `lamin rest schema --refresh` to rebuild it."
        ) from error
    if not isinstance(data, dict):
        raise click.ClickException(
            f"Schema cache at {path} has invalid content. "
            "Run `lamin rest schema --refresh` to rebuild it."
        )
    return data


def _write_cached_schema(path: Path, schema: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(schema))
        tmp_path.replace(path)
    except OSError as error:
        raise click.ClickException(
            f"Could not write schema cache at {path}: {error}"
        ) from error


def _is_hidden_field(name: str, field: dict[str, Any]) -> bool:
    return (
        "__" in name
        or name.startswith("_")
        or name.startswith("links_")
        or bool(field.get("is_link_table"))
    )


def _is_hidden_model(name: str, metadata: dict[str, Any]) -> bool:
    return (
        "__" in name
        or bool(metadata.get("is_link_table"))
        or bool(metadata.get("is_auto_created"))
    )


def _visible_field_items(
    fields: dict[str, dict[str, Any]], *, include_hidden: bool
) -> list[tuple[str, dict[str, Any]]]:
    return [
        (name, field)
        for name, field in fields.items()
        if include_hidden or not _is_hidden_field(name, field)
    ]


def _schema_summary(schema: dict[str, Any], *, include_hidden: bool) -> dict[str, Any]:
    return {
        "scope": "schema",
        "modules": [
            {
                "module": module,
                "models": len(models),
                "visible_models": sum(
                    1
                    for model, metadata in models.items()
                    if include_hidden or not _is_hidden_model(model, metadata)
                ),
            }
            for module, models in sorted(schema.items())
        ],
        "include_hidden": include_hidden,
    }


def _module_summary(
    module_schema: dict[str, Any], module: str, *, include_hidden: bool
) -> dict[str, Any]:
    models = []
    for model, metadata in sorted(module_schema.items()):
        if not include_hidden and _is_hidden_model(model, metadata):
            continue
        fields = metadata.get("fields", {})
        visible = _visible_field_items(fields, include_hidden=include_hidden)
        scalar_count = sum(
            1 for _, field in visible if field.get("relation_type") is None
        )
        relation_count = len(visible) - scalar_count
        hidden_count = len(fields) - len(visible)
        models.append(
            {
                "module": module,
                "model": model,
                "class": metadata.get("class_name"),
                "fields": len(fields),
                "scalar_fields": scalar_count,
                "relations": relation_count,
                "hidden_fields": hidden_count,
            }
        )
    return {
        "scope": "module",
        "module": module,
        "models": models,
        "hidden_models": len(module_schema) - len(models),
        "include_hidden": include_hidden,
    }


def _model_summary(
    schema: dict[str, Any],
    module: str,
    model: str,
    *,
    include_hidden: bool,
) -> dict[str, Any]:
    metadata = schema[module][model]
    fields = metadata.get("fields", {})
    visible = _visible_field_items(fields, include_hidden=include_hidden)
    scalars = []
    relations = []

    for name, field in visible:
        if field.get("relation_type") is None:
            scalars.append(_scalar_summary(name, field))
        else:
            relations.append(_relation_summary(name, field))

    return {
        "scope": "model",
        "module": module,
        "model": model,
        "class": metadata.get("class_name"),
        "table": metadata.get("table_name"),
        "name_field": metadata.get("name_field"),
        "ontology_id_field": metadata.get("ontology_id_field"),
        "fields": len(fields),
        "scalar_fields": scalars,
        "relations": relations,
        "hidden_fields": len(fields) - len(visible),
        "include_hidden": include_hidden,
    }


def _scalar_summary(name: str, field: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "type": field.get("type"),
        "column": field.get("column_name"),
        "primary_key": field.get("is_primary_key"),
        "editable": field.get("is_editable"),
    }


def _relation_summary(name: str, field: dict[str, Any]) -> dict[str, Any]:
    related_module = field.get("related_schema_name")
    related_model = field.get("related_model_name")
    target = (
        f"{related_module}.{related_model}"
        if related_module and related_model
        else None
    )
    return {
        "name": name,
        "relation_type": field.get("relation_type"),
        "target": target,
        "through": field.get("through"),
    }


def _schema_output(
    schema: dict[str, Any],
    scoped: Any,
    module: str | None,
    model: str | None,
    *,
    include_hidden: bool,
    all_models: bool,
) -> dict[str, Any]:
    if all_models:
        if module or model:
            raise click.ClickException(
                "--models cannot be combined with module/model scope."
            )
        return {
            "scope": "modules",
            "modules": [
                _module_summary(module_schema, module, include_hidden=include_hidden)
                for module, module_schema in sorted(schema.items())
            ],
            "include_hidden": include_hidden,
        }
    if model:
        if module is None:
            raise click.ClickException("Model scope requires a module.")
        return _model_summary(schema, module, model, include_hidden=include_hidden)
    if module:
        if not isinstance(scoped, dict):
            raise click.ClickException(
                f"Schema module '{module}' must be a JSON object."
            )
        return _module_summary(scoped, module, include_hidden=include_hidden)
    return _schema_summary(schema, include_hidden=include_hidden)


def _format_markdown(data: dict[str, Any]) -> str:
    if data["scope"] == "modules":
        return "\n\n".join(
            _format_markdown(module_summary) for module_summary in data["modules"]
        )

    if data["scope"] == "schema":
        lines = ["# Schema"]
        for module in data["modules"]:
            if data["include_hidden"]:
                lines.append(f"- {module['module']}: {module['models']} models")
            else:
                hidden = module["models"] - module["visible_models"]
                lines.append(
                    f"- {module['module']}: {module['visible_models']} models "
                    f"({hidden} hidden)"
                )
        lines.append("")
        lines.append("Use `lamin rest schema MODULE` to list models.")
        if not data["include_hidden"]:
            lines.append("Pass `--include-hidden` for link and generated models.")
        return "\n".join(lines)

    if data["scope"] == "module":
        lines = [f"# Schema: {data['module']}"]
        for model in data["models"]:
            lines.append(
                f"- {model['model']}: {model['fields']} fields "
                f"({model['scalar_fields']} scalar, {model['relations']} relations, "
                f"{model['hidden_fields']} hidden)"
            )
        lines.append("")
        lines.append(f"Use `lamin rest schema {data['module']} MODEL` for fields.")
        if data["hidden_models"] and not data["include_hidden"]:
            lines.append(
                f"Hidden models omitted: {data['hidden_models']}. "
                "Pass `--include-hidden` to show them."
            )
        return "\n".join(lines)

    lines = [f"# Schema: {data['module']}.{data['model']}"]
    if data.get("class"):
        lines.append(f"- class: {data['class']}")
    if data.get("table"):
        lines.append(f"- table: {data['table']}")
    if data.get("name_field"):
        lines.append(f"- name field: {data['name_field']}")
    if data.get("ontology_id_field"):
        lines.append(f"- ontology id field: {data['ontology_id_field']}")
    lines.append(
        f"- fields: {len(data['scalar_fields'])} scalar, "
        f"{len(data['relations'])} relations, {data['hidden_fields']} hidden "
        f"({data['fields']} total)"
    )

    lines.append("")
    lines.append("## Scalar Fields")
    if data["scalar_fields"]:
        for field in data["scalar_fields"]:
            lines.append(f"- {field['name']}: {field['type']}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("## Relations")
    if data["relations"]:
        for relation in data["relations"]:
            target = f" -> {relation['target']}" if relation.get("target") else ""
            lines.append(f"- {relation['name']}{target} ({relation['relation_type']})")
    else:
        lines.append("- none")

    return "\n".join(lines)


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
    """Query one object by numeric id or uid.

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
      lamin rest schema
      lamin rest schema --models
      lamin rest schema core
      lamin rest schema core artifact
      lamin rest schema core artifact --format json --compact
      lamin rest schema core --include-hidden
      lamin rest schema core artifact --raw --refresh
    """
    if raw and models:
        raise click.ClickException("--models cannot be combined with --raw.")

    schema_response = _load_schema(refresh=refresh)
    scoped = _scope_schema(schema_response, module, model)
    if raw:
        _print_json(scoped, compact=compact)
        return

    data = _schema_output(
        schema_response,
        scoped,
        module,
        model,
        include_hidden=include_hidden,
        all_models=models,
    )
    if format_ == "json":
        _print_json(data, compact=compact)
    else:
        click.echo(_format_markdown(data))


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
    """Read instance artifact size and table counts.

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
    """Read relation counts for one object by numeric database id.

    \b
    Examples:
      lamin rest relation-counts core artifact 123
      lamin rest get core artifact j2qX8G9a --select id --compact
      lamin rest relation-counts core artifact 123 --compact
    """
    data = request_json("get", f"{_module_model_path(module, model, id)}/counts")
    _print_json(data, compact=compact)
