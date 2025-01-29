from __future__ import annotations

import os

if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click


@click.group(invoke_without_command=True)
@click.pass_context
def settings(ctx):
    """Manage settings.

    Call without subcommands and options to show settings.
    """
    if ctx.invoked_subcommand is None:
        from lamindb_setup import settings as settings_

        click.echo("Configure: see `lamin settings --help`")
        click.echo(settings_)


@settings.command("set")
@click.argument(
    "setting",
    type=click.Choice(["auto-connect", "private-django-api"], case_sensitive=False),
)
@click.argument("value", type=click.BOOL)
def set(setting: str, value: bool):
    """Update settings.

    - `auto-connect` → {attr}`~lamindb.setup.core.SetupSettings.auto_connect`
    - `private-django-api` → {attr}`~lamindb.setup.core.SetupSettings.private_django_api`
    """
    from lamindb_setup import settings as settings_

    if setting == "auto-connect":
        settings_.auto_connect = value
    if setting == "private-django-api":
        settings_.private_django_api = value
