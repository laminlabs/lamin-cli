from __future__ import annotations

import os

if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click


@click.group()
def cache():
    """Manage local file caching for lamindb."""


@cache.command("set")
@click.argument(
    "cache_dir",
    type=click.Path(dir_okay=True, file_okay=False),
)
def set_cache(cache_dir: str):
    """Set directory for caching downloaded files."""
    from lamindb_setup._cache import set_cache_dir

    set_cache_dir(cache_dir)


@cache.command("clear")
def clear_cache():
    """Delete all cached files and reset cache directory.

    Warning: Cannot be undone. Downloaded files will need to be re-downloaded.
    """
    from lamindb_setup._cache import clear_cache_dir

    clear_cache_dir()


@cache.command("get")
def get_cache():
    """Show current cache directory location."""
    from lamindb_setup._cache import get_cache_dir

    click.echo(f"The cache directory is {get_cache_dir()}")
