from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from lamin_cli.hub._click import click
from lamin_cli.hub._utils import (
    _current_instance,
    _current_instance_schema_id,
    request_json,
)


def scope_schema(schema: Any, module: str | None, model: str | None) -> Any:
    if module is None:
        return schema
    if not isinstance(schema, dict):
        raise click.ClickException("Schema response must be a JSON object.")
    module_schema_ = module_schema(schema, module)
    if model is None:
        return module_schema_
    model_key, _ = resolve_model_metadata(schema, module, model)
    return module_schema_[model_key]


def module_schema(schema: dict[str, Any], module: str) -> dict[str, Any]:
    if module not in schema:
        raise click.ClickException(
            f"Unknown module '{module}'. Available modules: {sorted(schema)}"
        )
    module_schema_ = schema[module]
    if not isinstance(module_schema_, dict):
        raise click.ClickException(f"Schema module '{module}' must be a JSON object.")
    return module_schema_


def resolve_model_metadata(
    schema: dict[str, Any],
    module: str,
    model: str,
) -> tuple[str, dict[str, Any]]:
    module_schema_ = module_schema(schema, module)
    if model in module_schema_:
        metadata = module_schema_[model]
        if not isinstance(metadata, dict):
            raise click.ClickException(
                f"Schema model '{module}.{model}' must be a JSON object."
            )
        return model, metadata

    normalized_model = model.lower()
    matches = [
        (model_key, metadata)
        for model_key, metadata in module_schema_.items()
        if isinstance(metadata, dict)
        and str(metadata.get("class_name", "")).lower() == normalized_model
    ]
    if not matches:
        matches = [
            (model_key, metadata)
            for model_key, metadata in module_schema_.items()
            if isinstance(metadata, dict) and model_key.lower() == normalized_model
        ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise click.ClickException(
            f"Ambiguous model '{module}.{model}'. Available models in {module}: "
            f"{sorted(module_schema_)}"
        )
    raise click.ClickException(
        f"Unknown model '{module}.{model}'. Available models in {module}: "
        f"{sorted(module_schema_)}"
    )


def load_schema(*, refresh: bool) -> dict[str, Any]:
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
    root = Path(cache_root).expanduser() if cache_root else _default_cache_root()
    return (
        root / "rest" / "schemas" / _safe_cache_part(instance_id) / f"{schema_id}.json"
    )


def _default_cache_root() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "lamin"


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


def is_hidden_field(name: str, field: dict[str, Any]) -> bool:
    return (
        "__" in name
        or name.startswith("_")
        or name.startswith("links_")
        or bool(field.get("is_link_table"))
    )


def is_hidden_model(name: str, metadata: dict[str, Any]) -> bool:
    return (
        "__" in name
        or bool(metadata.get("is_link_table"))
        or bool(metadata.get("is_auto_created"))
    )


def visible_field_items(
    fields: dict[str, dict[str, Any]], *, include_hidden: bool
) -> list[tuple[str, dict[str, Any]]]:
    return [
        (name, field)
        for name, field in fields.items()
        if include_hidden or not is_hidden_field(name, field)
    ]
