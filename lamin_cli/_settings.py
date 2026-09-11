from __future__ import annotations

import os
from pathlib import Path

if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click


_WORKTREE_ROOT_ENTRIES = {".agents", ".claude", ".lamin", ".vscode"}


def _enable_worktree(settings_) -> None:
    """Enable worktree mode and migrate an existing manual dev-dir."""
    if settings_.worktree:
        return

    dev_dir = settings_.dev_dir
    if dev_dir is None:
        raise click.ClickException(
            "worktree mode requires a configured dev-dir. "
            "Run: lamin settings dev-dir set <path>"
        )

    from lamindb_setup.core._settings_store import local_current_branch_file

    dev_dir = Path(dev_dir).resolve()
    dev_dir.mkdir(parents=True, exist_ok=True)
    entries = [
        entry for entry in dev_dir.iterdir() if entry.name not in _WORKTREE_ROOT_ENTRIES
    ]
    if not entries:
        settings_.worktree = True
        return

    branch_idlike, branch_name = settings_._read_branch_idlike_name()
    branch_dir = dev_dir / branch_name
    if branch_dir.exists():
        raise click.ClickException(
            f"Cannot enable worktree mode because '{branch_dir}' already exists."
        )

    click.echo(
        f"Existing files in '{dev_dir}' will be moved to branch directory "
        f"'{branch_dir}'."
    )
    if not click.confirm("Continue?", default=True):
        raise click.Abort()

    branch_dir.mkdir()
    moved_entries: list[tuple[Path, Path]] = []
    try:
        for source in entries:
            destination = branch_dir / source.name
            source.replace(destination)
            moved_entries.append((source, destination))

        branch_marker = local_current_branch_file(branch_dir)
        branch_marker.parent.mkdir(parents=True, exist_ok=True)
        branch_marker.write_text(f"{branch_idlike}\n{branch_name}")
        settings_.worktree = True
    except Exception:
        settings_.worktree = False
        for source, destination in reversed(moved_entries):
            if destination.exists() and not source.exists():
                destination.replace(source)
        branch_marker = local_current_branch_file(branch_dir)
        branch_marker.unlink(missing_ok=True)
        if branch_marker.parent.exists() and not any(branch_marker.parent.iterdir()):
            branch_marker.parent.rmdir()
        if branch_dir.exists() and not any(branch_dir.iterdir()):
            branch_dir.rmdir()
        raise

    click.echo(f"Moved existing files to '{branch_dir}'.")


def _set_worktree(settings_, enabled: bool) -> None:
    if enabled:
        _enable_worktree(settings_)
    else:
        _disable_worktree(settings_)


def _disable_worktree(settings_) -> None:
    """Disable worktree mode and restore a single manual dev-dir."""
    if not settings_.worktree:
        return

    dev_dir = settings_.dev_dir
    if dev_dir is None:
        settings_.worktree = False
        return

    from lamindb_setup.core._settings_store import local_current_branch_file

    dev_dir = Path(dev_dir).resolve()
    branch_dirs = [
        path
        for path in dev_dir.iterdir()
        if path.is_dir() and local_current_branch_file(path).exists()
    ]
    if not branch_dirs:
        settings_.worktree = False
        return
    if len(branch_dirs) > 1:
        names = ", ".join(sorted(path.name for path in branch_dirs))
        raise click.ClickException(
            "Cannot disable worktree mode while multiple branch directories exist: "
            f"{names}. Merge or remove the other branch directories first."
        )

    branch_dir = branch_dirs[0]
    entries = [entry for entry in branch_dir.iterdir() if entry.name != ".lamin"]
    collisions = [entry.name for entry in entries if (dev_dir / entry.name).exists()]
    if collisions:
        raise click.ClickException(
            "Cannot disable worktree mode because these paths already exist in the "
            f"dev-dir: {', '.join(sorted(collisions))}."
        )

    click.echo(
        f"Files in branch directory '{branch_dir}' will be moved back to '{dev_dir}'."
    )
    if not click.confirm("Continue?", default=True):
        raise click.Abort()

    moved_entries: list[tuple[Path, Path]] = []
    try:
        for source in entries:
            destination = dev_dir / source.name
            source.replace(destination)
            moved_entries.append((source, destination))
        settings_.worktree = False
    except Exception:
        settings_.worktree = True
        for source, destination in reversed(moved_entries):
            if destination.exists() and not source.exists():
                destination.replace(source)
        raise

    branch_marker = local_current_branch_file(branch_dir)
    branch_marker.unlink(missing_ok=True)
    branch_lamin_dir = branch_marker.parent
    if branch_lamin_dir.exists() and not any(branch_lamin_dir.iterdir()):
        branch_lamin_dir.rmdir()
    if branch_dir.exists() and not any(branch_dir.iterdir()):
        branch_dir.rmdir()
    click.echo(f"Restored files from '{branch_dir}' to '{dev_dir}'.")


@click.group(invoke_without_command=True)
@click.pass_context
def settings(ctx):
    """Manage development, cache, modules, branch, space, and mount settings.

    Get or set a setting by name:

    - `dev-dir` → development directory {attr}`~lamindb.setup.core.SetupSettings.dev_dir`
    - `cache-dir` → cache directory {attr}`~lamindb.setup.core.SetupSettings.cache_dir`
    - `modules` → environment schema modules {attr}`~lamindb.setup.core.SetupSettings.modules`
    - `branch` → branch {attr}`~lamindb.setup.core.SetupSettings.branch`
    - `space` → space {attr}`~lamindb.setup.core.SetupSettings.space`
    - `worktree` → whether dev-dir is a worktree parent

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
    # worktree
    lamin settings worktree get
    lamin settings worktree set true  # moves the content of dev-dir into the directory for the main branch
    lamin settings worktree unset
    # mount
    lamin settings mount storage ./mnt
    lamin settings mount unset ./mnt
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
# worktree group (pattern: lamin settings worktree get/set)
# -----------------------------------------------------------------------------


@click.group("worktree")
def worktree_group():
    """Get or set whether dev-dir is interpreted as a worktree parent."""


@worktree_group.command("get")
def worktree_get():
    """Show whether worktree mode is enabled."""
    from lamindb_setup import settings as settings_

    click.echo("true" if settings_.worktree else "false")


@worktree_group.command("set")
@click.argument("value", type=str)
def worktree_set(value: str):
    """Enable or disable worktree mode, migrating the existing dev-dir safely."""
    from lamindb_setup import settings as settings_

    value_normalized = value.strip().lower()
    if value_normalized in {"1", "true", "yes"}:
        _set_worktree(settings_, True)
        return
    if value_normalized in {"0", "false", "no"}:
        _set_worktree(settings_, False)
        return
    raise click.ClickException("Invalid value for worktree. Pass one of: true, false.")


@worktree_group.command("unset")
def worktree_unset():
    """Unset worktree mode (equivalent to false)."""
    from lamindb_setup import settings as settings_

    _set_worktree(settings_, False)


settings.add_command(worktree_group)


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
# Legacy get/set (hidden, backward compatibility)
# -----------------------------------------------------------------------------


@settings.command("set", hidden=True)
@click.argument(
    "setting",
    type=click.Choice(
        ["auto-connect", "private-django-api", "dev-dir", "worktree"],
        case_sensitive=False,
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
    if setting == "worktree":
        _set_worktree(settings_, click.BOOL(value))


@settings.command("get", hidden=True)
@click.argument(
    "setting",
    type=click.Choice(
        [
            "auto-connect",
            "private-django-api",
            "space",
            "branch",
            "dev-dir",
            "worktree",
        ],
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
    elif setting == "worktree":
        value = "true" if settings_.worktree else "false"
    else:
        value = getattr(settings_, setting.replace("-", "_"))
    click.echo(value)


# -----------------------------------------------------------------------------
# cache-dir (already uses lamin settings cache-dir get/set/clear)
# -----------------------------------------------------------------------------

from lamin_cli._cache import cache
from lamin_cli.mount import mount

settings.add_command(cache, "cache-dir")
settings.add_command(mount)
