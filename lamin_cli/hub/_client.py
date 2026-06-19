from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

import click


def _current_instance() -> tuple[str, str]:
    import lamindb_setup as ln_setup

    instance = ln_setup.settings.instance
    instance_id = getattr(instance, "_id", None)
    api_url = getattr(instance, "api_url", None)
    if instance_id is None:
        raise click.ClickException(
            "No current LaminDB instance id found. Run `lamin connect account/name`."
        )
    if api_url is None:
        raise click.ClickException(
            "No API URL found for the current LaminDB instance. "
            "Run `lamin connect account/name`."
        )
    return str(instance_id), str(api_url).rstrip("/")


def _access_token() -> tuple[str | None, bool]:
    import lamindb_setup as ln_setup

    user = ln_setup.settings.user
    if getattr(user, "handle", None) == "anonymous":
        return None, False
    token = getattr(user, "access_token", None)
    return token, token is not None


def instance_url(path: str) -> str:
    instance_id, api_url = _current_instance()
    return f"{api_url}/instances/{quote(instance_id, safe='')}/{path}"


def module_model_path(
    module: str,
    model: str,
    id_or_uid: str | int | None = None,
) -> str:
    path = f"modules/{quote(module, safe='')}/{quote(model, safe='')}"
    if id_or_uid is not None:
        path += f"/{quote(str(id_or_uid), safe='')}"
    return path


def request_json(
    method: Literal["delete", "get", "patch", "post", "put"],
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: Any | None = None,
) -> Any:
    from lamindb_setup.core._hub_client import request_with_auth

    url = instance_url(path)
    token, renew_token = _access_token()
    kwargs: dict[str, Any] = {"params": params or {}}
    if body is not None:
        kwargs["json"] = body
    try:
        response = request_with_auth(url, method, token, renew_token, **kwargs)
    except Exception as error:
        raise click.ClickException(f"{method.upper()} {url} failed: {error}") from error

    if not 200 <= response.status_code < 300:
        msg = f"{method.upper()} {url} failed: {response.status_code} {response.text}"
        raise click.ClickException(msg)

    response_text = str(getattr(response, "text", "") or "")
    response_content = getattr(response, "content", None)
    if (
        response.status_code == 204
        or (response_content is not None and len(response_content) == 0)
        or not response_text.strip()
    ):
        return None

    try:
        return response.json()
    except ValueError as error:
        snippet = response_text[:500]
        raise click.ClickException(
            f"{method.upper()} {url} returned invalid JSON: {snippet}"
        ) from error
