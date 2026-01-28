from __future__ import annotations

import os
from pathlib import Path

if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click


@click.group(invoke_without_command=True)
@click.pass_context
def settings(ctx):
    """Manage development & cache directories, branch, and space settings.

    Get or set a setting by name:

    - `dev-dir` → development directory {attr}`~lamindb.setup.core.SetupSettings.dev_dir`
    - `cache-dir` → cache directory {attr}`~lamindb.setup.core.SetupSettings.cache_dir`
    - `branch` → branch {attr}`~lamindb.setup.core.SetupSettings.branch`
    - `space` → space {attr}`~lamindb.setup.core.SetupSettings.space`

    Display via [lamin info](https://docs.lamin.ai/cli#info)

    Examples:

    ```
    # dev-dir
    lamin settings dev-dir get
    lamin settings dev-dir set .  # set to current directory
    lamin settings dev-dir set ~/my-project
    lamin settings dev-dir unset
    # cache-dir
    lamin settings cache-dir get
    lamin settings cache-dir set /path/to/cache
    lamin settings cache-dir clear
    # branch
    lamin settings branch get
    lamin settings branch set main
    # space
    lamin settings space get
    lamin settings space set all
    ```

    💡 Python/R alternative: {attr}`~lamindb.setup.core.SetupSettings.dev_dir`, {attr}`~lamindb.setup.core.SetupSettings.cache_dir`, {attr}`~lamindb.setup.core.SetupSettings.branch`, and {attr}`~lamindb.setup.core.SetupSettings.space`
    """
    if ctx.invoked_subcommand is None:
        from lamindb_setup import settings as settings_

        click.echo("Configure: see `lamin settings --help`")
        click.echo(settings_)


# -----------------------------------------------------------------------------
# dev-dir group (pattern: lamin settings dev-dir get/set)
# -----------------------------------------------------------------------------


@click.group("dev-dir")
def dev_dir_group():
    """Get or set the development directory."""


@dev_dir_group.command("get")
def dev_dir_get():
    """Show the current development directory."""
    from lamindb_setup import settings as settings_

    value = settings_.dev_dir
    click.echo(value if value is not None else "None")


@dev_dir_group.command("set")
@click.argument("value", type=str)
def dev_dir_set(value: str):
    """Set the development directory."""
    from lamindb_setup import settings as settings_

    if value.lower() == "none":
        value = None  # type: ignore[assignment]
    settings_.dev_dir = value


@dev_dir_group.command("unset")
def dev_dir_unset():
    """Unset the development directory."""
    from lamindb_setup import settings as settings_

    settings_.dev_dir = None


settings.add_command(dev_dir_group)


# -----------------------------------------------------------------------------
# Legacy get/set (hidden, backward compatibility)
# -----------------------------------------------------------------------------


@settings.command("set", hidden=True)
@click.argument(
    "setting",
    type=click.Choice(
        ["auto-connect", "private-django-api", "dev-dir"], case_sensitive=False
    ),
)
@click.argument("value")  # No explicit type - let Click handle it
def set_legacy(setting: str, value: str):
    """Set a setting (legacy). Use lamin settings <name> set <value> instead."""
    from lamindb_setup import settings as settings_

    if setting == "auto-connect":
        settings_.auto_connect = click.BOOL(value)
    if setting == "private-django-api":
        settings_.private_django_api = click.BOOL(value)
    if setting == "dev-dir":
        if value.lower() == "none":
            value = None  # type: ignore[assignment]
        settings_.dev_dir = value


@settings.command("get", hidden=True)
@click.argument(
    "setting",
    type=click.Choice(
        ["auto-connect", "private-django-api", "space", "branch", "dev-dir"],
        case_sensitive=False,
    ),
)
def get_legacy(setting: str):
    """Get a setting (legacy). Use lamin settings <name> get instead."""
    from lamindb_setup import settings as settings_

    if setting == "branch":
        _, value = settings_._read_branch_idlike_name()
    elif setting == "space":
        _, value = settings_._read_space_idlike_name()
    elif setting == "dev-dir":
        value = settings_.dev_dir
        if value is None:
            value = "None"
    else:
        value = getattr(settings_, setting.replace("-", "_"))
    click.echo(value)


# -----------------------------------------------------------------------------
# cache-dir (already uses lamin settings cache-dir get/set/clear)
# -----------------------------------------------------------------------------

from lamin_cli._cache import cache

settings.add_command(cache, "cache-dir")
