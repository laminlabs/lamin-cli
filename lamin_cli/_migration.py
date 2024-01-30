import os
from typing import Optional

if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click


@click.group()
def migrate():
    """Manage migrations."""


@migrate.command("create")
def create():
    """Create a new migration."""
    from lamindb_setup._migrate import migrate

    return migrate.create()


@migrate.command("deploy")
def deploy():
    """Deploy migrations."""
    from lamindb_setup._migrate import migrate

    return migrate.deploy()


@migrate.command("squash")
@click.option("--package-name", type=str, default=None)
@click.option("--end-number", type=str, default=None)
@click.option("--start-number", type=str, default=None)
def squash(
    package_name: Optional[str],
    end_number: Optional[str],
    start_number: Optional[str],
):
    """Squash migrations."""
    from lamindb_setup._migrate import migrate

    return migrate.squash(
        package_name=package_name,
        migration_nr=end_number,
        start_migration_nr=start_number,
    )
