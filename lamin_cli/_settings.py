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
    """Manage settings.

    Call without subcommands and options to show settings:

    ```
    lamin settings
    ```

    Allows to get and set these settings:

    - `dev-dir` → {attr}`~lamindb.setup.core.SetupSettings.dev_dir`
    - `private-django-api` → {attr}`~lamindb.setup.core.SetupSettings.private_django_api`
    - `branch` → current branch (use `lamin switch --branch` to change)
    - `space` → current space (use `lamin switch --space` to change)

    Examples for getting a setting:

    ```
    lamin settings get dev-dir
    lamin settings get branch
    ```

    Examples for setting the working directory:

    ```
    lamin settings set dev-dir .  # set dev-dir to current directory
    lamin settings set dev-dir ~/my-project  # set dev-dir to ~/my-project
    lamin settings set dev-dir none  # unset dev-dir
    ```
    """
    if ctx.invoked_subcommand is None:
        from lamindb_setup import settings as settings_

        click.echo("Configure: see `lamin settings --help`")
        click.echo(settings_)


@settings.command("set")
@click.argument(
    "setting",
    type=click.Choice(
        ["auto-connect", "private-django-api", "dev-dir"], case_sensitive=False
    ),
)
@click.argument("value")  # No explicit type - let Click handle it
def set(setting: str, value: str):
    """Set a setting."""
    from lamindb_setup import settings as settings_

    if setting == "auto-connect":
        settings_.auto_connect = click.BOOL(value)
    if setting == "private-django-api":
        settings_.private_django_api = click.BOOL(value)
    if setting == "dev-dir":
        if value.lower() == "none":
            value = None  # type: ignore[assignment]
        settings_.dev_dir = value


@settings.command("get")
@click.argument(
    "setting",
    type=click.Choice(
        ["auto-connect", "private-django-api", "space", "branch", "dev-dir"],
        case_sensitive=False,
    ),
)
def get(setting: str):
    """Get a setting."""
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
