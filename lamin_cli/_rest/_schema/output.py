from __future__ import annotations

from typing import Any

from lamin_cli._rest._click import click

from .utils import (
    is_hidden_model,
    module_schema,
    resolve_model_metadata,
    visible_field_items,
)


def schema_output(
    schema: dict[str, Any],
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
                module_summary(module_schema_, module, include_hidden=include_hidden)
                for module, module_schema_ in sorted(schema.items())
            ],
            "include_hidden": include_hidden,
        }
    if model:
        if module is None:
            raise click.ClickException("Model scope requires a module.")
        return model_summary(schema, module, model, include_hidden=include_hidden)
    if module:
        return module_summary(
            module_schema(schema, module), module, include_hidden=include_hidden
        )
    return schema_summary(schema, include_hidden=include_hidden)


def schema_summary(schema: dict[str, Any], *, include_hidden: bool) -> dict[str, Any]:
    return {
        "scope": "schema",
        "modules": [
            {
                "module": module,
                "models": len(models),
                "visible_models": sum(
                    1
                    for model, metadata in models.items()
                    if include_hidden or not is_hidden_model(model, metadata)
                ),
            }
            for module, models in sorted(schema.items())
        ],
        "include_hidden": include_hidden,
    }


def module_summary(
    module_schema_: dict[str, Any], module: str, *, include_hidden: bool
) -> dict[str, Any]:
    models = []
    for model, metadata in sorted(module_schema_.items()):
        if not include_hidden and is_hidden_model(model, metadata):
            continue
        fields = metadata.get("fields", {})
        visible = visible_field_items(fields, include_hidden=include_hidden)
        scalar_count = sum(
            1 for _, field in visible if field.get("relation_type") is None
        )
        models.append(
            {
                "module": module,
                "model": model,
                "class": metadata.get("class_name"),
                "fields": len(fields),
                "scalar_fields": scalar_count,
                "relations": len(visible) - scalar_count,
                "hidden_fields": len(fields) - len(visible),
            }
        )
    return {
        "scope": "module",
        "module": module,
        "models": models,
        "hidden_models": len(module_schema_) - len(models),
        "include_hidden": include_hidden,
    }


def model_summary(
    schema: dict[str, Any],
    module: str,
    model: str,
    *,
    include_hidden: bool,
) -> dict[str, Any]:
    model_key, metadata = resolve_model_metadata(schema, module, model)
    fields = metadata.get("fields", {})
    scalars, relations = [], []
    for name, field in visible_field_items(fields, include_hidden=include_hidden):
        if field.get("relation_type") is None:
            scalars.append(scalar_summary(name, field))
        else:
            relations.append(relation_summary(name, field))
    return {
        "scope": "model",
        "module": module,
        "model": model_key,
        "class": metadata.get("class_name"),
        "table": metadata.get("table_name"),
        "name_field": metadata.get("name_field"),
        "ontology_id_field": metadata.get("ontology_id_field"),
        "fields": len(fields),
        "scalar_fields": scalars,
        "relations": relations,
        "hidden_fields": len(fields) - len(scalars) - len(relations),
        "include_hidden": include_hidden,
    }


def scalar_summary(name: str, field: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "type": field.get("type"),
        "column": field.get("column_name"),
        "primary_key": field.get("is_primary_key"),
        "editable": field.get("is_editable"),
    }


def relation_summary(name: str, field: dict[str, Any]) -> dict[str, Any]:
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


def format_schema_markdown(data: dict[str, Any]) -> str:
    match data["scope"]:
        case "modules":
            return "\n\n".join(
                format_schema_markdown(module) for module in data["modules"]
            )
        case "schema":
            return _format_schema_markdown(data)
        case "module":
            return _format_module_markdown(data)
        case _:
            return _format_model_markdown(data)


def _format_schema_markdown(data: dict[str, Any]) -> str:
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
    lines.extend(["", "Use `lamin rest schema MODULE` to list models."])
    if not data["include_hidden"]:
        lines.append("Pass `--include-hidden` for link and generated models.")
    return "\n".join(lines)


def _format_module_markdown(data: dict[str, Any]) -> str:
    lines = [f"# Schema: {data['module']}"]
    lines.extend(
        f"- {model['model']}: {model['fields']} fields "
        f"({model['scalar_fields']} scalar, {model['relations']} relations, "
        f"{model['hidden_fields']} hidden)"
        for model in data["models"]
    )
    lines.extend(["", f"Use `lamin rest schema {data['module']} MODEL` for fields."])
    if data["hidden_models"] and not data["include_hidden"]:
        lines.append(
            f"Hidden models omitted: {data['hidden_models']}. "
            "Pass `--include-hidden` to show them."
        )
    return "\n".join(lines)


def _format_model_markdown(data: dict[str, Any]) -> str:
    lines = [f"# Schema: {data['module']}.{data['model']}"]
    for label, key in [
        ("class", "class"),
        ("table", "table"),
        ("name field", "name_field"),
        ("ontology id field", "ontology_id_field"),
    ]:
        if data.get(key):
            lines.append(f"- {label}: {data[key]}")
    lines.append(
        f"- fields: {len(data['scalar_fields'])} scalar, "
        f"{len(data['relations'])} relations, {data['hidden_fields']} hidden "
        f"({data['fields']} total)"
    )
    lines.extend(["", "## Scalar Fields"])
    lines.extend(
        [f"- {field['name']}: {field['type']}" for field in data["scalar_fields"]]
        or ["- none"]
    )
    lines.extend(["", "## Relations"])
    lines.extend(
        [_relation_markdown_line(relation) for relation in data["relations"]]
        or ["- none"]
    )
    return "\n".join(lines)


def _relation_markdown_line(relation: dict[str, Any]) -> str:
    target = f" -> {relation['target']}" if relation.get("target") else ""
    return f"- {relation['name']}{target} ({relation['relation_type']})"
