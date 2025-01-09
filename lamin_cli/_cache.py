from __future__ import annotations

import os

if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click


@click.group()
def cache():
    """Manage cache."""


@cache.command("set")
@click.argument(
    "cache_dir",
    type=click.Path(dir_okay=True, file_okay=False),
)
def set_cache(cache_dir: str):
    """Set the cache directory."""
    from lamindb_setup._cache import set_cache_dir

    set_cache_dir(cache_dir)


@cache.command("clear")
def clear_cache():
    """Clear the cache directory."""
    from lamindb_setup._cache import clear_cache_dir

    clear_cache_dir()


@cache.command("get")
def get_cache():
    """Get the cache directory."""
    from lamindb_setup._cache import get_cache_dir

    click.echo(f"The cache directory is {get_cache_dir()}")
