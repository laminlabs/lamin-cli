from __future__ import annotations

from typing import Any

import lamindb_setup as ln_setup
from lamin_utils import logger
from lamindb_setup.core._settings_store import settings_dir

from ._click import click
from ._client import module_model_path, request_json
from .branches import create_branch


def _normalize_branch_payload(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                return item
    return None


def _extract_uid_name(payload: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if payload is None:
        return None, None
    uid = payload.get("uid")
    name = payload.get("name")
    return (
        str(uid) if uid is not None and str(uid).strip() else None,
        str(name) if name is not None and str(name).strip() else None,
    )


def _get_branch(target: str) -> dict[str, Any] | None:
    data = request_json(
        "post",
        path=module_model_path("core", "branch"),
        params={"limit": 1, "offset": 0},
        body={
            "select": ["uid", "name"],
            "filter": {
                "or": [
                    {"name": {"eq": target}},
                    {"uid": {"eq": target}},
                ]
            },
        },
    )
    return _normalize_branch_payload(data)


def _branch_settings_path():
    instance = ln_setup.settings.instance
    return settings_dir / f"current-branch--{instance.owner}--{instance.name}.txt"


def _write_current_branch(uid: str, name: str) -> None:
    _branch_settings_path().write_text(f"{uid}\n{name}")
    # Clear cache so current process reloads branch from file on next access.
    ln_setup.settings._branch = None


def switch_branch(target: str | None, *, create: bool = False) -> None:
    if target is None:
        raise click.ClickException(
            "Please pass a branch name or uid. Example: lamin switch main"
        )
    if create:
        created_branch = create_branch(target)
        uid, name = _extract_uid_name(created_branch)
        if uid is None or name is None:
            resolved_branch = _get_branch(target)
            uid, name = _extract_uid_name(resolved_branch)
        if uid is None or name is None:
            raise click.ClickException(
                f"Created branch '{target}' but could not resolve uid/name from hub response."
            )
        logger.important(f"created branch: {name}")
    else:
        resolved_branch = _get_branch(target)
        uid, name = _extract_uid_name(resolved_branch)
        if uid is None or name is None:
            raise click.ClickException(
                f"Branch '{target}', please check on the hub UI whether you have the correct `uid` or `name`."
            )

    _write_current_branch(uid, name)
    logger.important(f"switched to {target}")
