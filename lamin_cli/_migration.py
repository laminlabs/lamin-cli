from __future__ import annotations

import os
from typing import Optional

if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click


@click.group()
def migrate():
    """Manage sequential database schema migrations."""


@migrate.command("create")
def create():
    """Create a new numbered migration file."""
    from lamindb_setup._migrate import migrate

    return migrate.create()


@migrate.command("deploy")
def deploy():
    """Deploy pending migrations to bring database schema up to date."""
    from lamindb_setup._migrate import migrate

    return migrate.deploy()


@migrate.command("squash")
@click.option("--package-name", type=str, default=None)
@click.option("--end-number", type=str, default=None)
@click.option("--start-number", type=str, default=None)
def squash(
    package_name: str | None,
    end_number: str | None,
    start_number: str | None,
):
    """Squash multiple migrations into a single migration file.

    Reduces migration history complexity by consolidating sequential changes.
    """
    from lamindb_setup._migrate import migrate

    return migrate.squash(
        package_name=package_name,
        migration_nr=end_number,
        start_migration_nr=start_number,
    )
