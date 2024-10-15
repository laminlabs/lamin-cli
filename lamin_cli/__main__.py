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
                "name": "Connect to an instance",
                "commands": [
                    "connect",
                    "disconnect",
                    "info",
                    "init",
                ],
            },
            {
                "name": "Read & write data",
                "commands": ["load", "save", "get", "delete"],
            },
            {
                "name": "Configure",
                "commands": ["cache", "settings", "migrate"],
            },
            {
                "name": "Auth",
                "commands": [
                    "login",
                    "logout",
                ],
            },
        ]
    }

    def lamin_group_decorator(f):
        @click.rich_config(
            help_config=click.RichHelpConfiguration(
                command_groups=COMMAND_GROUPS,
                style_commands_table_column_width_ratio=(1, 10),
            )
        )
        @click.group()
        @wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)

        return wrapper


from click import Command, Context
from lamindb_setup._silence_loggers import silence_loggers

from lamin_cli._settings import settings
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
@click.option("--key", type=str, default=None, help="The legacy API key.")
def login(user: str, key: Optional[str]):
    """Log into LaminHub.

    `lamin login` prompts for your API key unless you set it via environment variable `LAMIN_API_KEY`.

    You can create your API key in your account settings on LaminHub (top right corner).

    After authenticating once, you can re-authenticate and switch between accounts via `lamin login myhandle`.
    """
    from lamindb_setup._setup_user import login as login_

    if user is None:
        if "LAMIN_API_KEY" in os.environ:
            api_key = os.environ["LAMIN_API_KEY"]
        else:
            api_key = input("Your API key: ")
    else:
        api_key = None

    if key is not None:
        click.echo(
            "--key is deprecated and will be removed in the future, "
            "use `lamin login` and enter your API key."
        )

    return login_(user, key=key, api_key=api_key)


@main.command()
def logout():
    """Log out of LaminHub."""
    from lamindb_setup import logout as logout_

    return logout_()


# fmt: off
@main.command()
@click.option("--storage", type=str, help="Local directory, s3://bucket_name, gs://bucket_name.")  # noqa: E501
@click.option("--db", type=str, default=None, help="Postgres database connection URL, do not pass for SQLite.")  # noqa: E501
@click.option("--schema", type=str, default=None, help="Comma-separated string of schema modules.")  # noqa: E501
@click.option("--name", type=str, default=None, help="The instance name.")
# fmt: on
def init(storage: str, db: Optional[str], schema: Optional[str], name: Optional[str]):
    """Init an instance."""
    from lamindb_setup._init_instance import init as init_

    return init_(storage=storage, db=db, schema=schema, name=name)


# fmt: off
@main.command()
@click.argument("instance", type=str)
# fmt: on
def connect(instance: str):
    """Connect to an instance.

    Pass a slug (`account/name`) or URL (`https://lamin.ai/account/name`).

    `lamin connect` switches
    {attr}`~lamindb.setup.core.SetupSettings.auto_connect` to `True` so that you
    auto-connect in a Python session upon importing `lamindb`.
    """
    from lamindb_setup import settings as settings_, connect as connect_

    settings_.auto_connect = True
    return connect_(slug=instance)


@main.command()
def disconnect():
    """Disconnect from an instance.

    Is the opposite of connecting to an instance.
    """
    from lamindb_setup import close as close_

    return close_()


@main.command()
@click.option("--schema", is_flag=True, help="View schema.")
def info(schema: bool):
    """Show info about current instance."""
    if schema:
        from lamindb_setup._schema import view

        click.echo("Open in browser: http://127.0.0.1:8000/schema/")
        return view()
    else:
        from lamindb_setup import settings as settings_

        click.echo(settings_)


# fmt: off
@main.command()
@click.argument("instance", type=str, default=None)
@click.option("--force", is_flag=True, default=False, help="Do not ask for confirmation.")  # noqa: E501
# fmt: on
def delete(instance: str, force: bool = False):
    """Delete an entity.

    Currently only supports instance deletion.
    """
    from lamindb_setup._delete import delete

    return delete(instance, force=force)


@main.command()
@click.argument("entity", type=str)
@click.option("--uid", help="The uid for the entity.")
@click.option("--key", help="The key for the entity.")
@click.option(
    "--with-env", is_flag=True, help="Also return the environment for a tranform."
)
def load(entity: str, uid: str = None, key: str = None, with_env: bool = False):
    """Load a file or folder.

    Pass a URL, `artifact`, or `transform`. For example:

    ```
    lamin load https://lamin.ai/account/instance/artifact/e2G7k9EVul4JbfsEYAy5
    lamin load artifact --key mydatasets/mytable.parquet
    lamin load artifact --uid e2G7k9EVul4JbfsEYAy5
    lamin load transform --key analysis.ipynb
    lamin load transform --uid Vul4JbfsEYAy5
    lamin load transform --uid Vul4JbfsEYAy5 --with-env
    ```
    """
    is_slug = entity.count("/") == 1
    if is_slug:
        from lamindb_setup import settings as settings_, connect

        # can decide whether we want to actually deprecate
        # click.echo(
        #     f"! please use: lamin connect {entity}"
        # )
        settings_.auto_connect = True
        return connect(slug=entity)
    else:
        from lamin_cli._load import load as load_

        return load_(entity, uid=uid, key=key, with_env=with_env)


@main.command()
@click.argument("entity", type=str)
@click.option("--uid", help="The uid for the entity.")
@click.option("--key", help="The key for the entity.")
@click.option(
    "--with-env", is_flag=True, help="Also return the environment for a tranform."
)
def get(entity: str, uid: str = None, key: str = None, with_env: bool = False):
    """Query metadata about an entity.

    Currently only works for artifact & transform and behaves like `lamin load`.
    """
    from lamin_cli._load import load as load_

    click.echo(f"! to load a file or folder, please use: lamin load {entity}")
    return load_(entity, uid=uid, key=key, with_env=with_env)


@main.command()
@click.argument(
    "filepath", type=click.Path(exists=True, dir_okay=False, file_okay=True)
)
@click.option("--key", type=str, default=None)
@click.option("--description", type=str, default=None)
@click.option("--registry", type=str, default=None)
def save(filepath: str, key: str, description: str, registry: str):
    """Save a file or folder.

    Defaults to saving `.py` and `.ipynb` as {class}`~lamindb.Transform` and
    other file types and folders as {class}`~lamindb.Artifact`.

    You can save a `.py` or `.ipynb` file as an {class}`~lamindb.Artifact` by
    passing `--registry artifact`.
    """
    from lamin_cli._save import save_from_filepath_cli

    if save_from_filepath_cli(filepath, key, description, registry) is not None:
        sys.exit(1)


main.add_command(settings)
main.add_command(cache)
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
