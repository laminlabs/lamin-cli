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

    - `work-dir` → {attr}`~lamindb.setup.core.SetupSettings.work_dir`
    - `private-django-api` → {attr}`~lamindb.setup.core.SetupSettings.private_django_api`
    - `branch` → current branch (use `lamin switch --branch` to change)
    - `space` → current space (use `lamin switch --space` to change)

    Examples for getting a setting:

    ```
    lamin settings get work-dir
    lamin settings get branch
    ```

    Examples for setting the working directory:

    ```
    lamin settings set work-dir .  # set work-dir to current directory
    lamin settings set work-dir ~/my-project  # set work-dir to ~/my-project
    lamin settings set work-dir none  # unset work-dir
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
        ["auto-connect", "private-django-api", "work-dir"], case_sensitive=False
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
    if setting == "work-dir":
        if value.lower() == "none":
            value = None  # type: ignore[assignment]
        settings_.work_dir = value


@settings.command("get")
@click.argument(
    "setting",
    type=click.Choice(
        ["auto-connect", "private-django-api", "space", "branch", "work-dir"],
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
    elif setting == "work-dir":
        value = settings_.work_dir
        if value is None:
            value = "None"
    else:
        value = getattr(settings_, setting.replace("-", "_"))
    click.echo(value)
