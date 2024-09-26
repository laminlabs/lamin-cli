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
                    "delete",
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
                "name": "Schema migration",
                "commands": ["migrate"],
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
@click.argument("user", type=str, default=None, required=False)
@click.option("--key", type=str, default=None, help="The API key.")
@click.option("--logout", is_flag=True, help="Logout instead of logging in.")
def login(user: str, key: Optional[str], logout: bool = False):
    """Log into LaminHub.

    Upon logging in the first time, you need to pass your API key via:

    ```
    lamin login myemail@acme.com --key YOUR_API_KEY
    ```

    You'll find your API key on LaminHub in the top right corner under "Settings".

    After this, you can either use `lamin login myhandle` or `lamin login myemail@acme.com`

    You can also call this without arguments:

    ```
    lamin login
    ```

    You will be prompted for your Beta API key unless you set an environment variable `LAMIN_API_KEY`.
    """
    if logout:
        from lamindb_setup._setup_user import logout as logout_func

        return logout_func()
    else:
        from lamindb_setup._setup_user import login

        if user is None:
            if "LAMIN_API_KEY" in os.environ:
                api_key = os.environ["LAMIN_API_KEY"]
            else:
                api_key = input("Your API key: ")
        else:
            api_key = None

        return login(user, key=key, api_key=api_key)


# fmt: off
@main.command()
@click.option("--storage", type=str, help="Local directory, s3://bucket_name, gs://bucket_name.")  # noqa: E501
@click.option("--db", type=str, default=None, help="Postgres database connection URL, do not pass for SQLite.")  # noqa: E501
@click.option("--schema", type=str, default=None, help="Comma-separated string of schema modules.")  # noqa: E501
@click.option("--name", type=str, default=None, help="The instance name.")
# fmt: on
def init(storage: str, db: Optional[str], schema: Optional[str], name: Optional[str]):
    """Init a LaminDB instance."""
    from lamindb_setup._init_instance import init as init_

    return init_(storage=storage, db=db, schema=schema, name=name)


# fmt: off
@main.command()
@click.argument("instance", type=str, default=None, required=False)
@click.option("--unload", is_flag=True, help="Unload the current instance.")
# fmt: on
def load(instance: Optional[str], unload: bool):
    """Load an instance for auto-connection.

    Pass a slug (`account/name`) or URL
    (`https://lamin.ai/account/name`).
    """
    if unload:
        from lamindb_setup._close import close as close_

        return close_()
    else:
        if instance is None:
            raise click.UsageError("INSTANCE is required when loading an instance.")
        from lamindb_setup import settings, connect

        settings.auto_connect = True
        return connect(slug=instance)


@main.command()
@click.option("--schema", is_flag=True, help="View schema.")
def info(schema: bool):
    """Show info about current instance."""
    if schema:
        from lamindb_setup._schema import view

        print("Open in browser: http://127.0.0.1:8000/schema/")
        return view()
    else:
        import lamindb_setup

        print(lamindb_setup.settings)


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
@click.argument("entity", type=str)
@click.option("--uid", help="The uid for the entity.")
@click.option("--key", help="The key for the entity.")
@click.option(
    "--with-env", is_flag=True, help="Also return the environment for a tranform."
)
def get(entity: str, uid: str = None, key: str = None, with_env: bool = False):
    """Query an entity.

    Pass a URL, `artifact`, or `transform`. For example:

    ```
    lamin get https://lamin.ai/account/instance/artifact/e2G7k9EVul4JbfsEYAy5
    lamin get artifact --key mydatasets/mytable.parquet
    lamin get artifact --uid e2G7k9EVul4JbfsEYAy5
    lamin get transform --key analysis.ipynb
    lamin get transform --uid Vul4JbfsEYAy5
    lamin get transform --uid Vul4JbfsEYAy5 --with-env
    ```
    """
    from lamin_cli._get import get

    return get(entity, uid=uid, key=key, with_env=with_env)


@main.command()
@click.argument(
    "filepath", type=click.Path(exists=True, dir_okay=False, file_okay=True)
)
@click.option("--key", type=str, default=None)
@click.option("--description", type=str, default=None)
@click.option("--registry", type=str, default=None)
def save(filepath: str, key: str, description: str, registry: str):
    """Save file or folder."""
    from lamin_cli._save import save_from_filepath_cli

    if save_from_filepath_cli(filepath, key, description, registry) is not None:
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
