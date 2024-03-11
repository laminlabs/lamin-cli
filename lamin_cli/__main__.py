import os
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Optional

# https://github.com/ewels/rich-click/issues/19
# Otherwise rich-click takes over the formatting.
if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click

from click import Command, Context
from lamindb_setup._silence_loggers import silence_loggers

from lamin_cli._cache import cache
from lamin_cli._migration import migrate

try:
    lamindb_version = version("lamindb")
except PackageNotFoundError:
    lamindb_version = "lamindb installation not found"


@click.group()
@click.version_option(version=lamindb_version, prog_name="lamindb")
def main():
    """Configure LaminDB and perform simple actions."""
    silence_loggers()


@main.command()
def info():
    """Show user & instance info."""
    import lamindb_setup

    print(lamindb_setup.settings)


# fmt: off
@main.command()
@click.option("--storage", type=str, help="local dir, s3://bucket_name, gs://bucket_name")  # noqa: E501
@click.option("--db", type=str, default=None, help="postgres database connection URL, do not pass for SQLite")  # noqa: E501
@click.option("--schema", type=str, default=None, help="comma-separated string of schema modules")  # noqa: E501
@click.option("--name", type=str, default=None, help="instance name")
# fmt: on
def init(storage: str, db: Optional[str], schema: Optional[str], name: Optional[str]):
    """Init a lamindb instance."""
    from lamindb_setup._init_instance import init as init_

    return init_(storage=storage, db=db, schema=schema, name=name)


@main.command()
@click.argument("user", type=str)
@click.option("--key", type=str, default=None, help="API key")
@click.option("--password", type=str, default=None, help="legacy password")
def login(user: str, key: Optional[str], password: Optional[str]):
    """Login using an email or user handle."""
    from lamindb_setup._setup_user import login

    return login(user, key=key, password=password)


@main.command()
def logout():
    """Logout."""
    from lamindb_setup._setup_user import logout

    return logout()


# fmt: off
@main.command()
@click.argument("instance", type=str, default=None)
@click.option("--db", type=str, default=None, help="Postgres database connection URL, do not pass for SQLite")  # noqa: E501
@click.option("--storage", type=str, default=None, help="Update storage while loading")
# fmt: on
def load(instance: str, db: Optional[str], storage: Optional[str]):
    """Load a lamindb instance (deprecated)."""
    from lamindb_setup import load

    return load(slug=instance, db=db, storage=storage)


@main.command()
@click.argument("instance", type=str, default=None)
@click.option(
    "--db",
    type=str,
    default=None,
    help="Postgres database connection URL, do not pass for SQLite",
)  # noqa: E501
@click.option("--storage", type=str, default=None, help="Update storage while loading")
# fmt: on
def connect(instance: str, db: Optional[str], storage: Optional[str]):
    """Connect to a lamindb instance.

    The instance slug is 'handle/name' or the URL: https://lamin.ai/handle/name.

    If the owner is the current user, passing only the instance name suffices.
    """
    from lamindb_setup import connect

    return connect(slug=instance, db=db, storage=storage)


# fmt: off
@main.command()
@click.argument("instance", type=str, default=None)
@click.option("--force", is_flag=True, default=False, help="Do not ask for confirmation")  # noqa: E501
# fmt: on
def delete(instance: str, force: bool = False):
    """Delete instance."""
    from lamindb_setup._delete import delete

    return delete(instance, force=force)


@main.command(name="set")
@click.argument("setting", type=click.Choice(["auto-connect"], case_sensitive=False))
@click.argument("value", type=click.BOOL)
def set_(setting: str, value: bool):
    """Update settings."""
    from lamindb_setup import settings

    if setting == "auto-connect":
        settings.auto_connect = value


@main.command()
def close():
    """Close existing instance."""
    from lamindb_setup._close import close as close_

    return close_()


@main.command()
def register():
    """Register an instance on the hub."""
    from lamindb_setup._register_instance import register as register_

    return register_()


@main.command()
@click.argument("action", type=click.Choice(["view"]))
def schema(action: str):
    """View schema."""
    from lamindb_setup._schema import view

    if action == "view":
        return view()


@main.command()
@click.argument(
    "filepath", type=click.Path(exists=True, dir_okay=False, file_okay=True)
)
def save(filepath: str):
    """Save notebook or script."""
    from lamin_cli._save import save

    if save(filepath) is not None:
        sys.exit(1)


@main.command()
@click.argument("url", type=str)
def stage(url: str):
    """Stage to a lamin.ai url."""
    from lamin_cli._stage import stage

    return stage(url)


main.add_command(cache)
main.add_command(migrate)


# https://stackoverflow.com/questions/57810659/automatically-generate-all-help-documentation-for-click-commands
def _generate_help():
    out: dict[str, str] = {}

    def recursive_help(
        cmd: Command, parent: Optional[Context] = None, name: tuple[str, ...] = ()
    ):
        ctx = click.Context(cmd, info_name=cmd.name, parent=parent)
        assert cmd.name
        name = (*name, cmd.name)
        out[" ".join(name)] = cmd.get_help(ctx)
        for sub in getattr(cmd, "commands", {}).values():
            recursive_help(sub, ctx, name=name)

    recursive_help(main)
    return out


if __name__ == "__main__":
    main()
