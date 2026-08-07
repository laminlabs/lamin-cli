from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click


def mount_storage_config_path() -> Path:
    from lamindb_setup import settings as settings_

    return settings_.settings_dir / "exec-mount-storage.txt"


def read_mount_storage_config() -> tuple[str, ...]:
    path = mount_storage_config_path()
    if not path.exists():
        return ()
    return tuple(line.strip() for line in path.read_text().splitlines() if line.strip())


@click.group(invoke_without_command=True)
@click.pass_context
def settings(ctx):
    """Manage development, cache, modules, branch, space, and exec mount-storage settings.

    Get or set a setting by name:

    - `dev-dir` → development directory {attr}`~lamindb.setup.core.SetupSettings.dev_dir`
    - `cache-dir` → cache directory {attr}`~lamindb.setup.core.SetupSettings.cache_dir`
    - `modules` → environment schema modules {attr}`~lamindb.setup.core.SetupSettings.modules`
    - `branch` → branch {attr}`~lamindb.setup.core.SetupSettings.branch`
    - `space` → space {attr}`~lamindb.setup.core.SetupSettings.space`
    - `mount-storage` → machine-local exec mount mappings for `lamin exec`

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
    # modules
    lamin settings modules get
    lamin settings modules set bionty,pertdb
    lamin settings modules unset
    # branch
    lamin settings branch get
    lamin settings branch set main
    # space
    lamin settings space get
    lamin settings space set all
    # exec mount-storage
    lamin settings mount-storage get
    lamin settings mount-storage set s3://bucket/prefix=/mount/root
    lamin settings mount-storage unset
    ```

    → Python/R alternative: {attr}`~lamindb.setup.core.SetupSettings.dev_dir`, {attr}`~lamindb.setup.core.SetupSettings.cache_dir`, {attr}`~lamindb.setup.core.SetupSettings.modules`, {attr}`~lamindb.setup.core.SetupSettings.branch`, and {attr}`~lamindb.setup.core.SetupSettings.space`
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
# modules group (pattern: lamin settings modules get/set)
# -----------------------------------------------------------------------------


@click.group("modules")
def modules_group():
    """Get or set environment schema modules."""


@modules_group.command("get")
def modules_get():
    """Show current environment schema modules."""
    from lamindb_setup import settings as settings_

    modules = sorted(settings_.modules)
    click.echo(",".join(modules) if modules else "None")


@modules_group.command("set")
@click.argument("value", type=str)
def modules_set(value: str):
    """Set environment schema modules as a comma-separated string."""
    from lamindb_setup import settings as settings_

    if value.lower() == "none":
        settings_.modules = None
    else:
        settings_.modules = value


@modules_group.command("unset")
def modules_unset():
    """Unset environment schema modules."""
    from lamindb_setup import settings as settings_

    settings_.modules = None


settings.add_command(modules_group)


# -----------------------------------------------------------------------------
# mount-storage group (pattern: lamin settings mount-storage get/set)
# -----------------------------------------------------------------------------


@click.group("mount-storage")
def mount_storage_group():
    """Get or set machine-local exec mount-storage mappings."""


@mount_storage_group.command("get")
def mount_storage_get():
    """Show current machine-local exec mount-storage mappings."""
    mappings = read_mount_storage_config()
    click.echo("\n".join(mappings) if mappings else "None")


@mount_storage_group.command("set")
@click.argument("values", nargs=-1, type=str)
def mount_storage_set(values: tuple[str, ...]):
    """Set machine-local exec mount-storage mappings."""
    from lamin_cli.__main__ import parse_mount_storage_mappings

    if not values:
        raise click.UsageError(
            "Provide at least one <storage-root>=<mount-root> mapping."
        )
    parse_mount_storage_mappings(values)
    mount_storage_config_path().write_text("\n".join(values) + "\n")


@mount_storage_group.command("unset")
def mount_storage_unset():
    """Unset machine-local exec mount-storage mappings."""
    mount_storage_config_path().unlink(missing_ok=True)


settings.add_command(mount_storage_group)


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
