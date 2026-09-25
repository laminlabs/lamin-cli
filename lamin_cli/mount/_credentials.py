"""Bridge LaminDB credentials into the environment of an external mounting tool.

LaminDB may hand out short-lived, hub-federated credentials that external tools cannot
discover on their own. We extract them from the fsspec filesystem behind the storage
path and pass them through the child process environment, never through argv (which
would be visible to other users via ``ps``).
"""

from __future__ import annotations

import hashlib
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from upath import UPath


def _storage_options(path: UPath) -> dict[str, Any]:
    options: dict[str, Any] = {}
    raw = getattr(path, "storage_options", None)
    if isinstance(raw, dict):
        options.update(raw)
    fs = getattr(path, "fs", None)
    if fs is not None:
        fs_options = getattr(fs, "storage_options", None)
        if isinstance(fs_options, dict):
            for key, value in fs_options.items():
                options.setdefault(key, value)
        for attr in ("key", "secret", "token", "anon"):
            value = getattr(fs, attr, None)
            if value is not None and options.get(attr) is None:
                options[attr] = value
    return options


def is_anonymous(path: UPath) -> bool:
    return bool(_storage_options(path).get("anon", False))


def endpoint_url(path: UPath) -> str | None:
    options = _storage_options(path)
    client_kwargs = options.get("client_kwargs") or {}
    if isinstance(client_kwargs, dict) and client_kwargs.get("endpoint_url"):
        return client_kwargs["endpoint_url"]
    return options.get("endpoint_url")


def credential_env(path: UPath, protocol: str) -> dict[str, str]:
    """Environment variables carrying credentials for an external mounting tool."""
    env: dict[str, str] = {}
    options = _storage_options(path)
    if protocol == "s3":
        if options.get("anon"):
            return env
        key = options.get("key") or options.get("aws_access_key_id")
        secret = options.get("secret") or options.get("aws_secret_access_key")
        token = options.get("token") or options.get("aws_session_token")
        if key and secret:
            env["AWS_ACCESS_KEY_ID"] = str(key)
            env["AWS_SECRET_ACCESS_KEY"] = str(secret)
            if token:
                env["AWS_SESSION_TOKEN"] = str(token)
    return env


def has_temporary_credentials(env: dict[str, str]) -> bool:
    """Whether the credentials are session credentials that will expire."""
    return "AWS_SESSION_TOKEN" in env


def fetch_aws_credentials(storage_root: str) -> dict | None:
    """Ask LaminHub for fresh federated credentials for a storage location."""
    from lamindb_setup.core._hub_core import access_aws

    try:
        info = access_aws(storage_root)
    except Exception:
        return None
    credentials = info.get("credentials") or None
    if not credentials or not credentials.get("key"):
        return None
    return credentials


def credential_process_payload(
    credentials: dict,
    not_after: datetime | None = None,
    reauth_seconds: int | None = None,
) -> str:
    """Render credentials in the AWS ``credential_process`` format.

    The AWS SDKs rerun the command before ``Expiration`` is reached, which is what
    keeps a long-lived mount working after the federated token would have expired.
    Because every rerun re-authorizes against LaminHub, reporting an earlier expiry
    shortens the window in which a revoked user can still read from the mount.
    """
    import json

    payload = {
        "Version": 1,
        "AccessKeyId": credentials["key"],
        "SecretAccessKey": credentials["secret"],
    }
    if credentials.get("token"):
        payload["SessionToken"] = credentials["token"]

    expiry = _to_datetime(credentials.get("expiry_time"))
    deadlines = [moment for moment in (expiry, not_after) if moment is not None]
    if reauth_seconds:
        deadlines.append(datetime.now(timezone.utc) + timedelta(seconds=reauth_seconds))
    if deadlines:
        payload["Expiration"] = min(deadlines).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps(payload)


def _to_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def parse_duration(text: str) -> timedelta:
    """Parse a duration such as ``30m``, ``12h`` or ``7d``."""
    import re

    match = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", text.lower())
    if match is None:
        raise ValueError(
            f"Could not parse duration {text!r}, use e.g. '30m', '12h' or '7d'."
        )
    amount = int(match.group(1))
    unit = match.group(2)
    return timedelta(
        **{{"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[unit]: amount}
    )


PROFILE_NAME = "lamin"


def write_profile_config(
    storage_root: str,
    endpoint: str | None = None,
    not_after: datetime | None = None,
    reauth_seconds: int | None = None,
) -> Path:
    """Write an AWS config whose profile refreshes credentials through lamin.

    The file holds no secrets, only the command to obtain them, and is written with
    owner-only permissions.
    """
    from lamindb_setup.core._settings_store import settings_dir

    directory = Path(settings_dir) / "mount-aws"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    command = (
        f"{shlex.quote(sys.executable)} -m lamin_cli settings mount credentials"
        f" --root {shlex.quote(storage_root)}"
    )
    if not_after is not None:
        command += (
            f" --not-after {shlex.quote(not_after.strftime('%Y-%m-%dT%H:%M:%SZ'))}"
        )
    if reauth_seconds:
        command += f" --reauth-seconds {reauth_seconds}"
    lines = [f"[profile {PROFILE_NAME}]", f"credential_process = {command}"]
    if endpoint:
        lines.append(f"endpoint_url = {endpoint}")

    digest = hashlib.sha256(
        f"{storage_root}|{not_after}|{reauth_seconds}".encode()
    ).hexdigest()[:16]
    config_path = directory / f"{digest}.conf"
    config_path.write_text("\n".join(lines) + "\n")
    config_path.chmod(0o600)
    return config_path
