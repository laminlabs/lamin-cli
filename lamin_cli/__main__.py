from __future__ import annotations

import inspect
import os
import shutil
import sys
import warnings
from collections import OrderedDict
from functools import wraps
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import lamindb_setup as ln_setup
from lamin_utils import logger
from lamindb_setup._init_instance import (
    DOC_DB,
    DOC_INSTANCE_NAME,
    DOC_MODULES,
    DOC_STORAGE_ARG,
)

from lamin_cli import connect as connect_
from lamin_cli import disconnect as disconnect_
from lamin_cli import init as init_
from lamin_cli import login as login_
from lamin_cli import logout as logout_
from lamin_cli import save as save_

from .urls import decompose_url

if TYPE_CHECKING:
    from collections.abc import Mapping

COMMAND_GROUPS = {
    "lamin": [
        {
            "name": "Manage connections",
            "commands": ["connect", "info", "init", "disconnect"],
        },
        {
            "name": "Load, save, create & delete data",
            "commands": ["load", "save", "create", "delete"],
        },
        {
            "name": "Tracking within shell scripts",
            "commands": ["track", "finish"],
        },
        {
            "name": "Describe, annotate & list data",
            "commands": ["describe", "annotate", "list"],
        },
        {
            "name": "Configure",
            "commands": [
                "checkout",
                "switch",
                "cache",
                "settings",
                "migrate",
            ],
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

# https://github.com/ewels/rich-click/issues/19
# Otherwise rich-click takes over the formatting.
if os.environ.get("NO_RICH"):
    import click as click

    class OrderedGroup(click.Group):
        """Overwrites list_commands to return commands in order of definition."""

        def __init__(
            self,
            name: str | None = None,
            commands: Mapping[str, click.Command] | None = None,
            **kwargs,
        ):
            super().__init__(name, commands, **kwargs)
            self.commands = commands or OrderedDict()

        def list_commands(self, ctx: click.Context) -> Mapping[str, click.Command]:
            return self.commands

    lamin_group_decorator = click.group(cls=OrderedGroup)

else:
    import rich_click as click

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


from lamindb_setup._silence_loggers import silence_loggers

from lamin_cli._cache import cache
from lamin_cli._io import io
from lamin_cli._migration import migrate
from lamin_cli._settings import settings

if TYPE_CHECKING:
    from click import Command, Context

try:
    lamindb_version = version("lamindb")
except PackageNotFoundError:
    lamindb_version = "lamindb installation not found"


@lamin_group_decorator
@click.version_option(version=lamindb_version, prog_name="lamindb")
def main():
    """Manage data with LaminDB instances."""
    silence_loggers()


@main.command()
@click.argument("user", type=str, default=None, required=False)
@click.option("--key", type=str, default=None, hidden=True, help="The legacy API key.")
def login(user: str, key: str | None):
    # note that the docstring needs to be synced with ln.setup.login()
    """Log into LaminHub.

    `lamin login` prompts for your API key unless you set it via environment variable `LAMIN_API_KEY`.

    You can create your API key in your account settings on LaminHub (top right corner).

    After authenticating once, you can re-authenticate and switch between accounts via `lamin login myhandle`.

    See also: Login in a Python session via {func}`~lamindb.setup.login`.
    """
    return login_(user, key=key)


@main.command()
def logout():
    """Log out of LaminHub."""
    return logout_()


def schema_to_modules_callback(ctx, param, value):
    if param.name == "schema" and value is not None:
        warnings.warn(
            "The --schema option is deprecated and will be removed in a future version."
            " Please use --modules instead.",
            DeprecationWarning,
            stacklevel=2,
        )
    return value


# fmt: off
@main.command()
@click.option("--storage", type=str, default = ".", help=DOC_STORAGE_ARG)
@click.option("--name", type=str, default=None, help=DOC_INSTANCE_NAME)
@click.option("--db", type=str, default=None, help=DOC_DB)
@click.option("--modules", type=str, default=None, help=DOC_MODULES)
# fmt: on
def init(
    storage: str,
    name: str | None,
    db: str | None,
    modules: str | None,
):
    """Init a LaminDB instance.

    See also: Init in a Python session via {func}`~lamindb.setup.init`.
    """
    return init_(storage=storage, db=db, modules=modules, name=name)


# fmt: off
@main.command()
@click.argument("instance", type=str)
@click.option("--use_proxy_db", is_flag=True, help="Use proxy database connection.")
# fmt: on
def connect(instance: str, use_proxy_db: bool):
    """Configure default instance for connections.

    Python/R sessions and CLI commands will then auto-connect to the configured instance.

    Pass a slug (`account/name`) or URL (`https://lamin.ai/account/name`).

    See also: Connect in a Python session via {func}`~lamindb.connect`.
    """
    return connect_(instance, use_proxy_db=use_proxy_db)


@main.command()
def disconnect():
    """Clear default instance configuration.

    See also: Disconnect in a Python session via {func}`~lamindb.setup.disconnect`.
    """
    return disconnect_()


# fmt: off
@main.command()
@click.argument("entity", type=str)
@click.option("--name", type=str, default=None, help="A name.")
# fmt: on
def create(entity: Literal["branch"], name: str | None = None):
    """Create a record for an entity.

    Currently only supports creating branches and projects.

    ```
    lamin create branch --name my_branch
    lamin create project --name my_project
    ```
    """
    from lamindb.models import Branch, Project

    if entity == "branch":
        record = Branch(name=name).save()
    elif entity == "project":
        record = Project(name=name).save()
    else:
        raise NotImplementedError(f"Creating {entity} is not implemented.")
    logger.important(f"created {entity}: {record.name}")


# fmt: off
@main.command(name="list")
@click.argument("entity", type=str)
@click.option("--name", type=str, default=None, help="A name.")
# fmt: on
def list_(entity: Literal["branch"], name: str | None = None):
    """List records for an entity.

    ```
    lamin list branch
    lamin list space
    ```
    """
    assert entity in {"branch", "space"}, "Currently only supports listing branches and spaces."

    from lamindb.models import Branch, Space

    if entity == "branch":
        print(Branch.to_dataframe())
    else:
        print(Space.to_dataframe())


# fmt: off
@main.command()
@click.option("--branch", type=str, default=None, help="A valid branch name or uid.")
@click.option("--space", type=str, default=None, help="A valid branch name or uid.")
# fmt: on
def switch(branch: str | None = None, space: str | None = None):
    """Switch between branches or spaces.

    ```
    lamin switch --branch my_branch
    lamin switch --space our_space
    ```
    """
    from lamindb.setup import switch as switch_

    switch_(branch=branch, space=space)


@main.command()
@click.option("--schema", is_flag=True, help="View database schema.")
def info(schema: bool):
    """Show info about the environment, instance, branch, space, and user.

    See also: Print the instance settings in a Python session via {func}`~lamindb.setup.settings`.
    """
    if schema:
        from lamindb_setup._schema import view

        click.echo("Open in browser: http://127.0.0.1:8000/schema/")
        return view()
    else:
        from lamindb_setup import settings as settings_

        click.echo(settings_)


# fmt: off
@main.command()
@click.argument("entity", type=str)
@click.option("--name", type=str, default=None)
@click.option("--uid", type=str, default=None)
@click.option("--slug", type=str, default=None)
@click.option("--permanent", is_flag=True, default=None, help="Permanently delete the entity where applicable, e.g., for artifact, transform, collection.")
@click.option("--force", is_flag=True, default=False, help="Do not ask for confirmation (only relevant for instance).")
# fmt: on
def delete(entity: str, name: str | None = None, uid: str | None = None, slug: str | None = None, permanent: bool | None = None, force: bool = False):
    """Delete an entity.

    Currently supported: `branch`, `artifact`, `transform`, `collection`, and `instance`. For example:

    ```
    lamin delete https://lamin.ai/account/instance/artifact/e2G7k9EVul4JbfsEYAy5
    lamin delete https://lamin.ai/account/instance/artifact/e2G7k9EVul4JbfsEYAy5 --permanent
    lamin delete branch --name my_branch
    lamin delete instance --slug account/name
    ```
    """
    from lamin_cli._delete import delete as delete_

    return delete_(entity=entity, name=name, uid=uid, slug=slug, permanent=permanent, force=force)


@main.command()
@click.argument("entity", type=str, required=False)
@click.option("--uid", help="The uid for the entity.")
@click.option("--key", help="The key for the entity.")
@click.option(
    "--with-env", is_flag=True, help="Also return the environment for a tranform."
)
def load(entity: str | None = None, uid: str | None = None, key: str | None = None, with_env: bool = False):
    """Load a file or folder into the cache or working directory.

    Pass a URL or `--key`. For example:

    ```
    lamin load https://lamin.ai/account/instance/artifact/e2G7k9EVul4JbfsE
    lamin load --key mydatasets/mytable.parquet
    lamin load --key analysis.ipynb
    lamin load --key myanalyses/analysis.ipynb --with-env
    ```

    You can also pass a uid and the entity type:

    ```
    lamin load artifact --uid e2G7k9EVul4JbfsE
    lamin load transform --uid Vul4JbfsEYAy5
    ```
    """
    from lamin_cli._load import load as load_
    if entity is not None:
        is_slug = entity.count("/") == 1
        if is_slug:
            from lamindb_setup._connect_instance import _connect_cli
            # for backward compat
            return _connect_cli(entity)
    return load_(entity, uid=uid, key=key, with_env=with_env)


def _describe(entity: str = "artifact", uid: str | None = None, key: str | None = None):
    if entity.startswith("https://") and "lamin" in entity:
        url = entity
        instance, entity, uid = decompose_url(url)
    elif entity not in {"artifact"}:
        raise SystemExit("Entity has to be a laminhub URL or 'artifact'")
    else:
        instance = ln_setup.settings.instance.slug

    ln_setup.connect(instance)
    import lamindb as ln

    if uid is not None:
        artifact = ln.Artifact.get(uid)
    else:
        artifact = ln.Artifact.get(key=key)
    artifact.describe()


@main.command()
@click.argument("entity", type=str, default="artifact")
@click.option("--uid", help="The uid for the entity.")
@click.option("--key", help="The key for the entity.")
def describe(entity: str = "artifact", uid: str | None = None, key: str | None = None):
    """Describe an artifact.

    Examples:

    ```
    lamin describe --key example_datasets/mini_immuno/dataset1.h5ad
    lamin describe https://lamin.ai/laminlabs/lamin-site-assets/artifact/6sofuDVvTANB0f48
    ```

    See also: Describe an artifact in a Python session via {func}`~lamindb.Artifact.describe`.
    """
    _describe(entity=entity, uid=uid, key=key)


@main.command()
@click.argument("entity", type=str, default="artifact")
@click.option("--uid", help="The uid for the entity.")
@click.option("--key", help="The key for the entity.")
def get(entity: str = "artifact", uid: str | None = None, key: str | None = None):
    """Query metadata about an entity.

    Currently equivalent to `lamin describe`.
    """
    logger.warning("please use `lamin describe` instead of `lamin get` to describe")
    _describe(entity=entity, uid=uid, key=key)


@main.command()
@click.argument("path", type=str)
@click.option("--key", type=str, default=None, help="The key of the artifact or transform.")
@click.option("--description", type=str, default=None, help="A description of the artifact or transform.")
@click.option("--stem-uid", type=str, default=None, help="The stem uid of the artifact or transform.")
@click.option("--project", type=str, default=None, help="A valid project name or uid.")
@click.option("--space", type=str, default=None, help="A valid space name or uid.")
@click.option("--branch", type=str, default=None, help="A valid branch name or uid.")
@click.option("--registry", type=str, default=None, help="Either 'artifact' or 'transform'. If not passed, chooses based on path suffix.")
def save(path: str, key: str, description: str, stem_uid: str, project: str, space: str, branch: str, registry: str):
    """Save a file or folder.

    Example: Given a valid project name "my_project",

    ```
    lamin save my_table.csv --key my_tables/my_table.csv --project my_project
    ```

    By passing a `--project` identifier, the artifact will be labeled with the corresponding project.
    If you pass a `--space` or `--branch` identifier, you save the artifact in the corresponding {class}`~lamindb.Space` or on the corresponding {class}`~lamindb.Branch`.

    Note: Defaults to saving `.py`, `.ipynb`, `.R`, `.Rmd`, and `.qmd` as {class}`~lamindb.Transform` and
    other file types and folders as {class}`~lamindb.Artifact`. You can enforce saving a file as
    an {class}`~lamindb.Artifact` by passing `--registry artifact`.
    """
    if save_(path=path, key=key, description=description, stem_uid=stem_uid, project=project, space=space, branch=branch, registry=registry) is not None:
        sys.exit(1)

@main.command()
def track():
    """Start tracking a run of a shell script.

    This command works like {func}`~lamindb.track()` in a Python session. Here is an example script:

    ```
    # my_script.sh
    set -e         # exit on error
    lamin track    # initiate a tracked shell script run
    lamin load --key raw/file1.txt
    # do something
    lamin save processed_file1.txt --key processed/file1.txt
    lamin finish   # mark the shell script run as finished
    ```

    If you run that script, it will track the run of the script, and save the input and output artifacts:

    ```
    sh my_script.sh
    ```
    """
    from lamin_cli._context import track as track_
    return track_()


@main.command()
def finish():
    """Finish the current tracked run of a shell script.

    This command works like {func}`~lamindb.finish()` in a Python session.
    """
    from lamin_cli._context import finish as finish_
    return finish_()


@main.command()
@click.argument("entity", type=str, default=None, required=False)
@click.option("--key", type=str, default=None, help="The key of an artifact or transform.")
@click.option("--uid", type=str, default=None, help="The uid of an artifact or transform.")
@click.option("--project", type=str, default=None, help="A valid project name or uid.")
@click.option("--features", multiple=True, help="Feature annotations. Supports: feature=value, feature=val1,val2, or feature=\"val1\",\"val2\"")
def annotate(entity: str | None, key: str, uid: str, project: str, features: tuple):
    """Annotate an artifact or transform.

    Entity is either 'artifact' or 'transform'. If not passed, chooses based on key suffix.

    You can annotate with projects and valid features & values. For example,

    ```
    lamin annotate --key raw/sample.fastq --project "My Project"
    lamin annotate --key raw/sample.fastq --features perturbation=IFNG,DMSO cell_line=HEK297
    lamin annotate --key my-notebook.ipynb --project "My Project"
    ```
    """
    import lamindb as ln

    from lamin_cli._annotate import _parse_features_list
    from lamin_cli._save import infer_registry_from_path

    # once we enable passing the URL as entity, then we don't need to throw this error
    if not ln.setup.settings._instance_exists:
        raise click.ClickException("Not connected to an instance. Please run: lamin connect account/name")

    if entity is None:
        if key is not None:
            registry = infer_registry_from_path(key)
        else:
            registry = "artifact"
    else:
        registry = entity
    if registry == "artifact":
        model = ln.Artifact
    else:
        model = ln.Transform

    # Get the artifact
    if key is not None:
        artifact = model.get(key=key)
    elif uid is not None:
        artifact = model.get(uid)  # do not use uid=uid, because then no truncated uids would work
    else:
        raise ln.errors.InvalidArgument("Either --key or --uid must be provided")

    # Handle project annotation
    if project is not None:
        project_record = ln.Project.filter(
            ln.Q(name=project) | ln.Q(uid=project)
        ).one_or_none()
        if project_record is None:
            raise ln.errors.InvalidArgument(
                f"Project '{project}' not found, either create it with `ln.Project(name='...').save()` or fix typos."
            )
        artifact.projects.add(project_record)

    # Handle feature annotations
    if features:
        feature_dict = _parse_features_list(features)
        artifact.features.add_values(feature_dict)

    artifact_rep = artifact.key if artifact.key else artifact.description if artifact.description else artifact.uid
    logger.important(f"annotated {registry}: {artifact_rep}")


@main.command()
@click.argument("filepath", type=str)
@click.option("--project", type=str, default=None, help="A valid project name or uid. When running on Modal, creates an app with the same name.", required=True)
@click.option("--image-url", type=str, default=None, help="A URL to the base docker image to use.")
@click.option("--packages", type=str, default="lamindb", help="A comma-separated list of additional packages to install.")
@click.option("--cpu", type=float, default=None, help="Configuration for the CPU.")
@click.option("--gpu", type=str, default=None, help="The type of GPU to use (only compatible with cuda images).")
def run(filepath: str, project: str, image_url: str, packages: str, cpu: int, gpu: str | None):
    """Run a compute job in the cloud.

    This is an EXPERIMENTAL feature that enables to run a script on Modal.

    Example: Given a valid project name "my_project",

    ```
    lamin run my_script.py --project my_project
    ```
    """
    from lamin_cli.compute.modal import Runner

    default_mount_dir = Path('./modal_mount_dir')
    if not default_mount_dir.is_dir():
        default_mount_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy(filepath, default_mount_dir)

    filepath_in_mount_dir = default_mount_dir / Path(filepath).name

    package_list = []
    if packages:
        package_list = [package.strip() for package in packages.split(',')]

    runner = Runner(
        local_mount_dir=default_mount_dir,
        app_name=project,
        packages=package_list,
        image_url=image_url,
        cpu=cpu,
        gpu=gpu
    )

    runner.run(filepath_in_mount_dir)


main.add_command(settings)
main.add_command(cache)
main.add_command(migrate)
main.add_command(io)

# https://stackoverflow.com/questions/57810659/automatically-generate-all-help-documentation-for-click-commands
# https://claude.ai/chat/73c28487-bec3-4073-8110-50d1a2dd6b84
def _generate_help():
    out: dict[str, dict[str, str | None]] = {}

    def recursive_help(
        cmd: Command, parent: Context | None = None, name: tuple[str, ...] = ()
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
