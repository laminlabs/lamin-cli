from __future__ import annotations
import os
import sys
from collections import OrderedDict
import inspect
from importlib.metadata import PackageNotFoundError, version
from typing import Optional, Mapping
from functools import wraps

# https://github.com/ewels/rich-click/issues/19
# Otherwise rich-click takes over the formatting.
if os.environ.get("NO_RICH"):
    import click as click

    class OrderedGroup(click.Group):
        """Overwrites list_commands to return commands in order of definition."""

        def __init__(
            self,
            name: Optional[str] = None,
            commands: Optional[Mapping[str, click.Command]] = None,
            **kwargs,
        ):
            super(OrderedGroup, self).__init__(name, commands, **kwargs)
            self.commands = commands or OrderedDict()

        def list_commands(self, ctx: click.Context) -> Mapping[str, click.Command]:
            return self.commands

    lamin_group_decorator = click.group(cls=OrderedGroup)

else:
    import rich_click as click

    COMMAND_GROUPS = {
        "lamin": [
            {
                "name": "Main commands",
                "commands": [
                    "login",
                    "init",
                    "load",
                    "info",
                    "close",
                    "delete",
                    "logout",
                ],
            },
            {
                "name": "Data commands",
                "commands": ["get", "save"],
            },
            {
                "name": "Configuration commands",
                "commands": ["cache", "set"],
            },
            {
                "name": "Schema commands",
                "commands": ["migrate", "schema"],
            },
        ]
    }

    def lamin_group_decorator(f):
        @click.rich_config(
            help_config=click.RichHelpConfiguration(
                command_groups=COMMAND_GROUPS,
                style_commands_table_column_width_ratio=(1, 13),
            )
        )
        @click.group()
        @wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)

        return wrapper


from click import Command, Context
from lamindb_setup._silence_loggers import silence_loggers

from lamin_cli._cache import cache
from lamin_cli._migration import migrate

try:
    lamindb_version = version("lamindb")
except PackageNotFoundError:
    lamindb_version = "lamindb installation not found"


@lamin_group_decorator
@click.version_option(version=lamindb_version, prog_name="lamindb")
def main():
    """Configure LaminDB and perform simple actions."""
    silence_loggers()


@main.command()
@click.argument("user", type=str)
@click.option("--key", type=str, default=None, help="API key")
def login(user: str, key: Optional[str]):
    """Log into LaminHub.

    Upon logging in the first time, you need to pass your API key via

    ```
    lamin login myemail@acme.com --key YOUR_API_KEY
    ```

    You'll find your API key in the top right corner under "Settings".

    After this, you can either use `lamin login myhandle` or `lamin login myemail@acme.com`
    """
    from lamindb_setup._setup_user import login

    return login(user, key=key)


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


# fmt: off
@main.command()
@click.argument("identifier", type=str, default=None)
@click.option("--db", type=str, default=None, help="Update database URL.")  # noqa: E501
@click.option("--storage", type=str, default=None, help="Update storage while loading.")
# fmt: on
def load(identifier: str, db: Optional[str], storage: Optional[str]):
    """Load an instance for auto-connection.

    `IDENTIFIER` is either a slug (`account/instance`) or a `URL`
    (`https://lamin.ai/account/instance`).
    """
    from lamindb_setup import settings, connect

    settings.auto_connect = True
    return connect(slug=identifier, db=db, storage=storage)


@main.command()
def info():
    """Show user, settings & instance info."""
    import lamindb_setup

    print(lamindb_setup.settings)


@main.command()
def close():
    """Close an existing instance.

    Is the opposite of loading an instance.
    """
    from lamindb_setup._close import close as close_

    return close_()


# fmt: off
@main.command()
@click.argument("instance", type=str, default=None)
@click.option("--force", is_flag=True, default=False, help="Do not ask for confirmation.")  # noqa: E501
# fmt: on
def delete(instance: str, force: bool = False):
    """Delete an instance."""
    from lamindb_setup._delete import delete

    return delete(instance, force=force)


@main.command()
def logout():
    """Logout."""
    from lamindb_setup._setup_user import logout

    return logout()


@main.command()
@click.argument("url", type=str)
def get(url: str):
    """Get an object from a lamin.ai URL."""
    from lamin_cli._get import get

    return get(url)


@main.command()
@click.argument(
    "filepath", type=click.Path(exists=True, dir_okay=False, file_okay=True)
)
@click.option("--key", type=str, default=None)
@click.option("--description", type=str, default=None)
def save(filepath: str, key: str, description: str):
    """Save file or folder."""
    from lamin_cli._save import save_from_filepath_cli

    if save_from_filepath_cli(filepath, key, description) is not None:
        sys.exit(1)


main.add_command(cache)


@main.command(name="set")
@click.argument(
    "setting",
    type=click.Choice(["auto-connect", "private-django-api"], case_sensitive=False),
)
@click.argument("value", type=click.BOOL)
def set_(setting: str, value: bool):
    """Update settings.

    - `auto-connect` → {attr}`~lamindb.setup.core.SetupSettings.auto_connect`
    - `private-django-api` → {attr}`~lamindb.setup.core.SetupSettings.private_django_api`
    """
    from lamindb_setup import settings

    if setting == "auto-connect":
        settings.auto_connect = value
    if setting == "private-django-api":
        settings.private_django_api = value


main.add_command(migrate)


@main.command()
@click.argument("action", type=click.Choice(["view"]))
def schema(action: str):
    """View schema."""
    from lamindb_setup._schema import view

    if action == "view":
        return view()


# https://stackoverflow.com/questions/57810659/automatically-generate-all-help-documentation-for-click-commands
# https://claude.ai/chat/73c28487-bec3-4073-8110-50d1a2dd6b84
def _generate_help():
    out: dict[str, dict[str, str | None]] = {}

    def recursive_help(
        cmd: Command, parent: Optional[Context] = None, name: tuple[str, ...] = ()
    ):
        ctx = click.Context(cmd, info_name=cmd.name, parent=parent)
        assert cmd.name
        name = (*name, cmd.name)
        command_name = " ".join(name)

        docstring = inspect.getdoc(cmd.callback)
        usage = cmd.get_help(ctx).split("\n")[0]
        options = cmd.get_help(ctx).split("Options:")[1]
        out[command_name] = {
            "help": usage + "\n\nOptions:" + options,
            "docstring": docstring,
        }

        for sub in getattr(cmd, "commands", {}).values():
            recursive_help(sub, ctx, name=name)

    recursive_help(main)
    return out


if __name__ == "__main__":
    main()
