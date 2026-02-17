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
            "name": "Change management",
            "commands": ["switch", "merge"],
        },
        {
            "name": "Configure",
            "commands": ["settings", "migrate"],
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

    → Python/R alternative: {func}`~lamindb.setup.login`
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
    """Initialize an instance.

    This initializes a LaminDB instance, for example:

    ```
    lamin init --storage ./mydata
    lamin init --storage s3://my-bucket
    lamin init --storage gs://my-bucket
    lamin init --storage ./mydata --modules bionty
    lamin init --storage ./mydata --modules bionty,pertdb
    ```

    → Python/R alternative: {func}`~lamindb.setup.init`
    """
    return init_(storage=storage, db=db, modules=modules, name=name)


# fmt: off
@main.command()
@click.argument("instance", type=str)
# fmt: on
def connect(instance: str):
    """Set the default database instance for this environment.

    This command updates your local configuration to target the specified instance:
    all subsequent Python/R sessions and CLI commands will auto-connect to this instance.

    Pass a slug (`account/name`) or URL (`https://lamin.ai/account/name`), for example:

    ```
    lamin connect laminlabs/cellxgene
    lamin connect https://lamin.ai/laminlabs/cellxgene
    ```

    → Python/R alternative: create a database object via {class}`~lamindb.DB` or set the default database of your Python/R session via {func}`~lamindb.connect`
    """
    return connect_(instance)


@main.command()
def disconnect():
    """Unset the default instance for auto-connection.

    Python/R sessions and CLI commands will no longer auto-connect to a LaminDB instance.

    For example:

    ```
    lamin disconnect
    ```

    → Python/R alternative: {func}`~lamindb.setup.disconnect`
    """
    return disconnect_()


# fmt: off
@main.command()
@click.argument("registry", type=click.Choice(["branch", "project"]))
@click.argument("name", type=str, required=False)
# below is deprecated, for backward compatibility
@click.option("--name", "name_opt", type=str, default=None, hidden=True, help="A name.")
# fmt: on
def create(
    registry: Literal["branch", "project"],
    name: str | None,
    name_opt: str | None,
):
    """Create an object.

    Currently only supports creating branches and projects.

    ```
    lamin create branch my_branch
    lamin create project my_project
    ```

    → Python/R alternative: {class}`~lamindb.Branch` and {class}`~lamindb.Project`.
    """
    resolved_name = name if name is not None else name_opt
    if resolved_name is None:
        raise click.UsageError(
            "Specify a name. Examples: lamin create branch my_branch, lamin create project my_project"
        )
    if name_opt is not None:
        warnings.warn(
            "lamin create --name is deprecated; use 'lamin create <registry> <name>' instead, e.g. lamin create branch my_branch.",
            DeprecationWarning,
            stacklevel=2,
        )

    from lamindb.models import Branch, Project

    if registry == "branch":
        record = Branch(name=resolved_name).save()
    elif registry == "project":
        record = Project(name=resolved_name).save()
    else:
        raise NotImplementedError(f"Creating {registry} object is not implemented.")
    logger.important(f"created {registry}: {record.name}")


# fmt: off
@main.command(name="list")
@click.argument("registry", type=str)
# fmt: on
def list_(registry: Literal["branch", "space"]):
    """List objects.

    For example:

    ```
    lamin list branch
    lamin list space
    ```

    → Python/R alternative: {meth}`~lamindb.Branch.to_dataframe()`
    """
    assert registry in {"branch", "space"}, "Currently only supports listing branches and spaces."

    from lamindb.models import Branch, Space

    if registry == "branch":
        print(Branch.to_dataframe())
    else:
        print(Space.to_dataframe())


# fmt: off
@main.command()
@click.argument("target", type=str, nargs=-1, required=False)  # TODO: remove nargs=-1 once deprecated form is removed
@click.option("--space", is_flag=True, default=False, help="Switch space instead of branch.")
@click.option("-c", "--create", is_flag=True, default=False, help="Create branch if it does not exist.")
# fmt: on
def switch(
    target: tuple[str, ...],
    space: bool = False,
    create: bool = False,
):
    """Switch between branches.

    Python/R sessions and CLI commands use the current default branch. Switch it:

    ```
    lamin switch my_branch  # pass a name or uid of the target branch
    ```

    To create and switch in one step, pass `-c` or `--create`:

    ```
    lamin switch -c my_branch
    ```

    To switch to a target space, pass `--space`:

    ```
    lamin switch --space my_space
    ```

    → Python/R alternative: {attr}`~lamindb.setup.core.SetupSettings.branch` and {attr}`~lamindb.setup.core.SetupSettings.space`
    """
    from lamindb.errors import ObjectDoesNotExist
    from lamindb.setup import switch as switch_

    # Backward compatibility: lamin switch branch X / lamin switch space Y (deprecated, hidden from help)
    if len(target) == 2 and target[0] in ("branch", "space"):
        kind, name = target[0], target[1]
        logger.warn(
            f"'lamin switch {kind} <name>' is deprecated and will be removed in a future version. "
            f"Use 'lamin switch {name}' for branches or 'lamin switch --space {name}' for spaces instead.",        )
        try:
            switch_(name, space=(kind == "space"), create=create)
        except ObjectDoesNotExist as e:
            raise click.ClickException(str(e)) from e
        return

    # Normal usage: single target (or none)
    if len(target) > 1:
        raise click.ClickException("Too many arguments. Use 'lamin switch <target>' or 'lamin switch --space <space>'.")
    target_str = target[0] if len(target) == 1 else None
    try:
        switch_(target_str, space=space, create=create)
    except ObjectDoesNotExist as e:
        raise click.ClickException(str(e)) from e


# fmt: off
@main.command()
@click.argument("branch", type=str, required=True)
# fmt: on
def merge(branch: str):
    """Merge a branch into the current branch.

    Pass the `name` or `uid` of the branch to merge into the current branch.

    Everything that was on the given branch will then be on the current branch.
    Run this on the branch that should receive the objects (e.g. `main`):

    ```
    lamin switch main  # swich to the main branch
    lamin merge my_branch  # after this all objects on my_branch will be on main
    ```

    → Python/R alternative: {func}`~lamindb.setup.merge`
    """
    from lamindb.errors import ObjectDoesNotExist
    from lamindb.setup import merge as merge_

    try:
        merge_(branch)
    except ObjectDoesNotExist as e:
        raise click.ClickException(str(e)) from e


@main.command()
@click.option("--schema", is_flag=True, help="View database schema via Django plugin.")
def info(schema: bool):
    """Show info about the instance, development & cache directories, branch, space, and user.

    Manage settings via [lamin settings](https://docs.lamin.ai/cli#settings).

    → Python/R alternative: {func}`~lamindb.setup.settings`
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
# entity can be a registry or an object in the registry
@click.argument("entity", type=str)
@click.option("--name", type=str, default=None)
@click.option("--uid", type=str, default=None)
@click.option("--key", type=str, default=None, help="The key for the entity (artifact, transform).")
@click.option("--permanent", is_flag=True, default=None, help="Permanently delete the entity where applicable, e.g., for artifact, transform, collection.")
@click.option("--force", is_flag=True, default=False, help="Do not ask for confirmation (only relevant for instance).")
# fmt: on
def delete(entity: str, name: str | None = None, uid: str | None = None, key: str | None = None, slug: str | None = None, permanent: bool | None = None, force: bool = False):
    """Delete an object.

    Currently supported: `branch`, `artifact`, `transform`, `collection`, and `instance`. For example:

    ```
    # via --key or --name
    lamin delete artifact --key mydatasets/mytable.parquet
    lamin delete transform --key myanalyses/analysis.ipynb
    lamin delete branch --name my_branch
    lamin delete instance --slug account/name
    # via registry and --uid
    lamin delete artifact --uid e2G7k9EVul4JbfsE
    lamin delete transform --uid Vul4JbfsEYAy5
    # via URL
    lamin delete https://lamin.ai/account/instance/artifact/e2G7k9EVul4JbfsEYAy5
    lamin delete https://lamin.ai/account/instance/artifact/e2G7k9EVul4JbfsEYAy5 --permanent
    ```

    → Python/R alternative: {meth}`~lamindb.SQLRecord.delete` and {func}`~lamindb.setup.delete`
    """
    from lamin_cli._delete import delete as delete_

    return delete_(entity=entity, name=name, uid=uid, key=key, permanent=permanent, force=force)


@main.command()
# entity can be a registry or an object in the registry
@click.argument("entity", type=str, required=False)
@click.option("--uid", help="The uid for the entity.")
@click.option("--key", help="The key for the entity.")
@click.option(
    "--with-env", is_flag=True, help="Also return the environment for a tranform."
)
def load(entity: str | None = None, uid: str | None = None, key: str | None = None, with_env: bool = False):
    """Sync a file/folder into a local cache (artifacts) or development directory (transforms).

    Pass a URL or `--key`. For example:

    ```
    # via key
    lamin load --key mydatasets/mytable.parquet
    lamin load --key analysis.ipynb
    lamin load --key myanalyses/analysis.ipynb --with-env
    # via registry and --uid
    lamin load artifact --uid e2G7k9EVul4JbfsE
    lamin load transform --uid Vul4JbfsEYAy5
    # via URL
    lamin load https://lamin.ai/account/instance/artifact/e2G7k9EVul4JbfsE
    ```

    → Python/R alternative: {func}`~lamindb.Artifact.load`, no equivalent for transforms
    """
    from lamin_cli._load import load as load_
    if entity is not None:
        is_slug = entity.count("/") == 1
        if is_slug:
            from lamindb_setup._connect_instance import _connect_cli
            # for backward compat
            return _connect_cli(entity)
    return load_(entity, uid=uid, key=key, with_env=with_env)


DESCRIBE_ENTITIES_KEY = {"artifact", "transform", "collection"}
DESCRIBE_ENTITIES_NAME = {"record", "project", "ulabel", "branch"}
DESCRIBE_ENTITIES_UID_ONLY = {"run"}
DESCRIBE_ENTITIES = (
    DESCRIBE_ENTITIES_KEY | DESCRIBE_ENTITIES_NAME | DESCRIBE_ENTITIES_UID_ONLY
)


def _describe(
    entity: str = "artifact",
    uid: str | None = None,
    key: str | None = None,
    name: str | None = None,
):
    if entity.startswith("https://") and "lamin" in entity:
        url = entity
        instance, entity, uid = decompose_url(url)
    elif entity not in DESCRIBE_ENTITIES:
        raise SystemExit(
            f"Entity must be a laminhub URL or one of: "
            f"{', '.join(sorted(DESCRIBE_ENTITIES))}"
        )
    else:
        instance = ln_setup.settings.instance.slug

    ln_setup.connect(instance)
    import lamindb as ln

    if entity in DESCRIBE_ENTITIES_KEY:
        if uid is None and key is None:
            raise SystemExit(
                f"For entity '{entity}' you must pass --uid or --key"
            )
        if uid is not None:
            record = (
                ln.Artifact.get(uid)
                if entity == "artifact"
                else ln.Transform.get(uid)
                if entity == "transform"
                else ln.Collection.get(uid)
            )
        else:
            record = (
                ln.Artifact.get(key=key)
                if entity == "artifact"
                else ln.Transform.get(key=key)
                if entity == "transform"
                else ln.Collection.get(key=key)
            )
    elif entity in DESCRIBE_ENTITIES_NAME:
        if uid is not None:
            record = (
                ln.Record.get(uid)
                if entity == "record"
                else ln.Project.get(uid)
                if entity == "project"
                else ln.ULabel.get(uid)
                if entity == "ulabel"
                else ln.Branch.get(uid)
            )
        else:
            if name is None:
                raise SystemExit(
                    f"For entity '{entity}' you must pass --uid or --name"
                )
            record = (
                ln.Record.filter(name=name).one()
                if entity == "record"
                else ln.Project.filter(name=name).one()
                if entity == "project"
                else ln.ULabel.filter(name=name).one()
                if entity == "ulabel"
                else ln.Branch.get(name=name)
            )
    else:  # uid-only (run)
        if uid is None:
            raise SystemExit(f"For entity '{entity}' you must pass --uid")
        record = ln.Run.get(uid)

    record.describe()


@main.command()
# entity can be a registry or an object in the registry
@click.argument("entity", type=str, default="artifact")
@click.option("--uid", help="The uid for the entity.")
@click.option("--key", help="The key for the entity (artifact, transform, collection).")
@click.option("--name", help="The name for the entity (record, project, ulabel, branch).")
def describe(
    entity: str = "artifact",
    uid: str | None = None,
    key: str | None = None,
    name: str | None = None,
):
    """Describe an object.

    Examples:

    ```
    # via URL
    lamin describe https://lamin.ai/laminlabs/lamin-site-assets/artifact/6sofuDVvTANB0f48
    lamin describe https://lamin.ai/laminlabs/lamin-site-assets/transform/uDVvTANB0f48
    # via --key for artifacts
    lamin describe --key example_datasets/mini_immuno/dataset1.h5ad
    # via registry and one of --uid / --name / --key
    lamin describe artifact --uid e2G7k9EVul4JbfsE
    lamin describe transform --uid Vul4JbfsEYAy5
    lamin describe run --uid 6sofuDVvTANB0f48
    lamin describe record --name "Experiment 1"
    lamin describe project --name "My Project"
    lamin describe ulabel --name "My ULabel"
    lamin describe branch --name main
    ```

    → Python/R alternative: {meth}`~lamindb.Artifact.describe`
    """
    _describe(entity=entity, uid=uid, key=key, name=name)


@main.command()
# entity can be a registry or an object in the registry
@click.argument("entity", type=str, default="artifact")
@click.option("--uid", help="The uid for the entity.")
@click.option("--key", help="The key for the entity (artifact, transform, collection).")
@click.option("--name", help="The name for the entity (record, project, ulabel, branch).")
def get(
    entity: str = "artifact",
    uid: str | None = None,
    key: str | None = None,
    name: str | None = None,
):
    """Query metadata about an object.

    Currently equivalent to `lamin describe`.
    """
    logger.warning("please use `lamin describe` instead of `lamin get` to describe")
    _describe(entity=entity, uid=uid, key=key, name=name)


@main.command()
@click.argument("path", type=str)
@click.option("--key", type=str, default=None, help="The key of the artifact or transform.")
@click.option("--description", type=str, default=None, help="A description of the artifact or transform.")
@click.option("--kind", type=str, default=None, help="Artifact kind (e.g. 'plan', 'dataset', 'model'). Overrides auto-inferred kind for plan files.")
@click.option("--stem-uid", type=str, default=None, help="The stem uid of the artifact or transform.")
@click.option("--project", type=str, default=None, help="A valid project name or uid.")
@click.option("--space", type=str, default=None, help="A valid space name or uid.")
@click.option("--branch", type=str, default=None, help="A valid branch name or uid.")
@click.option(
    "--registry",
    type=click.Choice(["artifact", "transform"]),
    default=None,
    help="Either 'artifact' or 'transform'. If not passed, chooses based on path suffix.",
)
def save(
    path: str,
    key: str,
    description: str,
    kind: str,
    stem_uid: str,
    project: str,
    space: str,
    branch: str,
    registry: Literal["artifact", "transform"] | None,
):
    """Save a file or folder as an artifact or transform.

    Example:

    ```
    lamin save my_table.csv --key my_tables/my_table.csv --project my_project
    ```

    By passing a `--project` identifier, the artifact will be labeled with the corresponding project.
    If you pass a `--space` or `--branch` identifier, you save the artifact in the corresponding {class}`~lamindb.Space` or on the corresponding {class}`~lamindb.Branch`.

    Transforms: Defaults to saving `.py`, `.ipynb`, `.R`, `.Rmd`, and `.qmd` as {class}`~lamindb.Transform` and
    other file types and folders as {class}`~lamindb.Artifact`. You can enforce saving a file as
    an {class}`~lamindb.Artifact` by passing `--registry artifact`.

    Plans: Saves agent plans as artifacts with inferred `key`, `kind`, and `description`, e.g.:

    ```
    lamin save /path/to/.cursor/plans/my_task.plan.md
    lamin save /path/to/.claude/plans/my_task.md
    ```

    ```{dropdown} How are plans handled?

    Plan files are detected by suffix `.plan.md` (Cursor) or by being under `.claude/plans/`
    (Claude Code). For such paths, the `key` defaults to `.plans/<filename>`, the artifact `kind`
    is set to `plan`, and the description is taken from the markdown front matter (`name:` and
    `overview:`). The stored artifact contains only the body (the YAML front matter is stripped).

    ```

    git: When saving scripts, files will be synced with a git repo if you set:

    ```
    export LAMINDB_SYNC_GIT_REPO=https://github.com/org/repo
    ```

    Also see: {ref}`sync-code-with-git`

    → Python/R alternative: {class}`~lamindb.Artifact` and {class}`~lamindb.Transform`
    """
    if save_(path=path, key=key, description=description, kind=kind, stem_uid=stem_uid, project=project, space=space, branch=branch, registry=registry) is not None:
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

    → Python/R alternative: {func}`~lamindb.track` and {func}`~lamindb.finish` for (non-shell) scripts or notebooks
    """
    from lamin_cli._context import track as track_
    return track_()


@main.command()
def finish():
    """Finish a currently tracked run of a shell script.

    → Python/R alternative: {func}`~lamindb.finish()`
    """
    from lamin_cli._context import finish as finish_
    return finish_()


@main.command()
# entity can be a registry or an object in the registry
@click.argument("entity", type=str, default=None, required=False)
@click.option("--key", type=str, default=None, help="The key of an artifact, transform, or collection.")
@click.option("--uid", type=str, default=None, help="The uid of an artifact, transform, or collection.")
@click.option("--project", type=str, default=None, help="A valid project name or uid.")
@click.option("--ulabel", type=str, default=None, help="A valid ulabel name or uid.")
@click.option("--record", type=str, default=None, help="A valid record name or uid.")
@click.option("--version", type=str, default=None, help="A version tag for the artifact, transform, or collection.")
@click.option("--features", multiple=True, help="Feature annotations (artifact/transform only). Supports: feature=value, feature=val1,val2, or feature=\"val1\",\"val2\"")
def annotate(entity: str | None, key: str, uid: str, project: str, ulabel: str, record: str, version: str, features: tuple):
    r"""Annotate an artifact, transform, or collection.

    You can annotate with projects, ulabels, records, version tags, and (for artifacts/transforms) valid features & values. For example,

    ```
    # via --key
    lamin annotate --key raw/sample.fastq --project "My Project"
    lamin annotate --key raw/sample.fastq --ulabel "My ULabel" --record "Experiment 1"
    lamin annotate --key raw/sample.fastq --version "1.0"
    lamin annotate --key raw/sample.fastq --features perturbation=IFNG,DMSO cell_line=HEK297
    lamin annotate --key my-notebook.ipynb --project "My Project"
    # via registry and --uid
    lamin annotate artifact --uid e2G7k9EVul4JbfsE --project "My Project"
    lamin annotate collection --uid abc123 --version "1.0"
    # via URL
    lamin annotate https://lamin.ai/account/instance/artifact/e2G7k9EVul4JbfsE --project "My Project"
    ```

    → Python/R alternative: `artifact.features.add_values()` via {meth}`~lamindb.models.FeatureManager.add_values`, `artifact.projects.add()`, `artifact.ulabels.add()`, `artifact.records.add()`, ... via {meth}`~lamindb.models.RelatedManager.add`, and `artifact.version_tag = \"1.0\"; artifact.save()` for version tags.
    """
    from lamin_cli._annotate import _parse_features_list
    from lamin_cli._save import infer_registry_from_path

    # Handle URL: decompose and connect (same pattern as load/delete)
    if entity is not None and entity.startswith("https://"):
        url = entity
        instance, registry, uid = decompose_url(url)
        if registry not in {"artifact", "transform", "collection"}:
            raise click.ClickException(
                f"Annotate does not support {registry}. Use artifact, transform, or collection URLs."
            )
        ln_setup.connect(instance)
    else:
        if not ln_setup.settings._instance_exists:
            raise click.ClickException(
                "Not connected to an instance. Please run: lamin connect account/name"
            )
        if entity is None:
            registry = infer_registry_from_path(key) if key is not None else "artifact"
        else:
            registry = entity
        if registry not in {"artifact", "transform", "collection"}:
            raise click.ClickException(
                f"Annotate does not support {registry}. Use artifact, transform, or collection."
            )

    # import lamindb after connect went through
    import lamindb as ln

    if registry == "artifact":
        model = ln.Artifact
    elif registry == "transform":
        model = ln.Transform
    else:
        model = ln.Collection

    # Get the artifact, transform, or collection
    if key is not None:
        artifact = model.get(key=key)
    elif uid is not None:
        artifact = model.get(uid)  # do not use uid=uid, because then no truncated uids would work
    else:
        raise ln.errors.InvalidArgument(
            "Either pass a URL as entity or provide --key or --uid"
        )

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

    # Handle ulabel annotation
    if ulabel is not None:
        ulabel_record = ln.ULabel.filter(
            ln.Q(name=ulabel) | ln.Q(uid=ulabel)
        ).one_or_none()
        if ulabel_record is None:
            raise ln.errors.InvalidArgument(
                f"ULabel '{ulabel}' not found, either create it with `ln.ULabel(name='...').save()` or fix typos."
            )
        artifact.ulabels.add(ulabel_record)

    # Handle record annotation
    if record is not None:
        record_record = ln.Record.filter(
            ln.Q(name=record) | ln.Q(uid=record)
        ).one_or_none()
        if record_record is None:
            raise ln.errors.InvalidArgument(
                f"Record '{record}' not found, either create it with `ln.Record(name='...').save()` or fix typos."
            )
        artifact.records.add(record_record)

    # Handle version tag annotation (artifact, transform, and collection all have version_tag)
    if version is not None:
        model.filter(uid=artifact.uid).update(version_tag=version)
        artifact.refresh_from_db()

    # Handle feature annotations (artifact and transform only; collection has no features)
    if features:
        if registry == "collection":
            raise click.ClickException(
                "Feature annotations are not supported for collections. Use artifact or transform."
            )
        feature_dict = _parse_features_list(features)
        artifact.features.add_values(feature_dict)

    artifact_rep = artifact.key if artifact.key else artifact.description if artifact.description else artifact.uid
    logger.important(f"annotated {registry}: {artifact_rep}")


@main.command()
@click.argument("filepath", type=str)
@click.option("--project", type=str, default=None, help="A valid project name or uid. When running on Modal, creates an app with the same name.", required=True)
@click.option("--image-url", type=str, default=None, help="A URL to the base docker image to use.")
@click.option("--packages", type=str, default=None, help="A comma-separated list of additional packages to install.")
@click.option("--cpu", type=float, default=None, help="Configuration for the CPU.")
@click.option("--gpu", type=str, default=None, help="The type of GPU to use (only compatible with cuda images).")
def run(filepath: str, project: str, image_url: str, packages: str, cpu: int, gpu: str | None):
    """Run a compute job in the cloud.

    This is an EXPERIMENTAL feature that enables to run a script on Modal.

    Example: Given a valid project name "my_project",

    ```
    lamin run my_script.py --project my_project
    ```

    → Python/R alternative: no equivalent
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
main.add_command(migrate)
main.add_command(io)


def _deprecated_cache_set(cache_dir: str) -> None:
    logger.warning("'lamin cache' is deprecated. Use 'lamin settings cache-dir' instead.")
    from lamindb_setup._cache import set_cache_dir

    set_cache_dir(cache_dir)


def _deprecated_cache_clear() -> None:
    logger.warning("'lamin cache' is deprecated. Use 'lamin settings cache-dir' instead.")
    from lamindb_setup._cache import clear_cache_dir

    clear_cache_dir()


def _deprecated_cache_get() -> None:
    logger.warning("'lamin cache' is deprecated. Use 'lamin settings cache-dir' instead.")
    from lamindb_setup._cache import get_cache_dir

    click.echo(f"The cache directory is {get_cache_dir()}")


@main.group("cache", hidden=True)
def deprecated_cache():
    """Deprecated. Use 'lamin settings cache-dir' instead."""


@deprecated_cache.command("set")
@click.argument(
    "cache_dir",
    type=click.Path(dir_okay=True, file_okay=False),
)
def _deprecated_cache_set_cmd(cache_dir: str) -> None:
    _deprecated_cache_set(cache_dir)


@deprecated_cache.command("clear")
def _deprecated_cache_clear_cmd() -> None:
    _deprecated_cache_clear()


@deprecated_cache.command("get")
def _deprecated_cache_get_cmd() -> None:
    _deprecated_cache_get()

# https://stackoverflow.com/questions/57810659/automatically-generate-all-help-documentation-for-click-commands
# https://claude.ai/chat/73c28487-bec3-4073-8110-50d1a2dd6b84
def _generate_help():
    out: dict[str, dict[str, str | None]] = {}

    def recursive_help(
        cmd: Command, parent: Context | None = None, name: tuple[str, ...] = ()
    ):
        if getattr(cmd, "hidden", False):
            return
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
            if getattr(sub, "hidden", False):
                continue
            recursive_help(sub, ctx, name=name)

    recursive_help(main)
    return out


if __name__ == "__main__":
    main()
