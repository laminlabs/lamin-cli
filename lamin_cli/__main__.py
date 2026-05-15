from __future__ import annotations

import inspect
import os
import shlex
import shutil
import subprocess
import sys
import warnings
from datetime import datetime, timezone
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
            "name": "Configure your environment",
            "commands": ["connect", "info", "init", "disconnect"],
        },
        {
            "name": "Execute programs",
            "commands": ["exec"],
        },
        {
            "name": "Save, load, create & delete",
            "commands": ["save", "load", "create", "delete"],
        },
        {
            "name": "Describe, update, annotate & list",
            "commands": ["describe", "annotate", "update", "get", "list"],
        },
        {
            "name": "Manage changes",
            "commands": ["switch", "merge"],
        },
        {
            "name": "Track within shell scripts",
            "commands": ["track", "finish"],
        },
        {
            "name": "Manage settings and migrations",
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
    lamindb_version = version("lamindb-core")
except PackageNotFoundError:
    lamindb_version = "lamindb-core installation not found"


def classify_exec_target(target: str) -> Literal["script", "executable"]:
    """Classify an exec target as a local script or an opaque executable."""
    return "script" if Path(target).suffix in {".py", ".pyw", ".sh", ".bash", ".zsh", ".r", ".R", ".Rmd", ".qmd"} else "executable"


def _probe_exec_version(executable: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None

    version_output = (result.stdout or result.stderr).strip()
    if not version_output:
        return None
    return version_output.splitlines()[0]


def _prepare_exec_transform(target: str, target_kind: Literal["script", "executable"]):
    import lamindb as ln

    target_path = Path(target)
    key = target_path.name
    if target_kind == "script":
        return ln.Transform(
            key=key,
            source_code=target_path.read_text(),
            kind="script",
            branch=ln_setup.settings.branch,
            space=ln_setup.settings.space,
        ).save()

    description = key
    version_output = _probe_exec_version(target)
    if version_output is not None:
        description = f"{key} ({version_output})"
    return ln.Transform(
        key=key,
        kind="pipeline",
        description=description,
        branch=ln_setup.settings.branch,
        space=ln_setup.settings.space,
    ).save()


def parse_lamin_exec_uri(uri: str) -> tuple[str, str, Path | None]:
    if not uri.startswith("lamin://"):
        raise click.BadParameter("Expected a lamin:// URI.")

    parts = uri.removeprefix("lamin://").split("/")
    if len(parts) < 4:
        raise click.BadParameter(
            "Expected lamin://<owner>/<instance>/artifact/<uid>[/<subpath>]."
        )

    owner, instance, entity, uid, *subpath_parts = parts
    if not owner or not instance or entity != "artifact":
        raise click.BadParameter(
            "Expected lamin://<owner>/<instance>/artifact/<uid>[/<subpath>]."
        )

    if len(uid) not in {16, 20}:
        raise click.BadParameter(
            f"Artifact uid must be 16 or 20 characters, got {len(uid)}."
        )

    if any(part == "" for part in subpath_parts):
        raise click.BadParameter(
            "Expected lamin://<owner>/<instance>/artifact/<uid>[/<subpath>]."
        )

    subpath = Path(*subpath_parts) if subpath_parts else None
    return f"{owner}/{instance}", uid, subpath


def _load_exec_artifact(instance_slug: str, uid: str):
    ln_setup.connect(instance_slug)
    import lamindb as ln

    return ln.Artifact.get(uid)


def resolve_lamin_exec_arg(arg: str) -> str:
    if not arg.startswith("lamin://"):
        return arg

    instance_slug, uid, subpath = parse_lamin_exec_uri(arg)
    cache_path = _load_exec_artifact(instance_slug, uid).cache()
    if subpath is not None:
        cache_path = cache_path / subpath
    return str(cache_path)


def rewrite_exec_argv(argv: list[str]) -> list[str]:
    return [resolve_lamin_exec_arg(arg) for arg in argv]


def _collect_exec_output_paths(
    argv: list[str], register_outputs: tuple[str, ...]
) -> list[Path]:
    output_paths = [Path(output) for output in register_outputs]
    passthrough_argv = argv[1:]
    i = 0
    while i < len(passthrough_argv):
        arg = passthrough_argv[i]
        if arg in {"--out", "--output"}:
            if i + 1 < len(passthrough_argv):
                value = passthrough_argv[i + 1]
                if not value.startswith("-"):
                    output_paths.append(Path(value))
                    i += 2
                    continue
        elif arg.startswith("--out="):
            output_paths.append(Path(arg.partition("=")[2]))
        elif arg.startswith("--output="):
            output_paths.append(Path(arg.partition("=")[2]))
        i += 1
    return output_paths


def _register_exec_outputs(run, output_paths: list[Path]) -> None:
    import lamindb as ln

    seen_paths: set[Path] = set()
    for output_path in output_paths:
        if output_path in seen_paths or not output_path.exists():
            continue
        seen_paths.add(output_path)
        ln.Artifact(output_path, key=output_path.name, run=run).save()


@lamin_group_decorator
@click.version_option(version=lamindb_version, prog_name="lamindb-core")
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
    """Initialize a database instance.

    Examples:

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
@click.option("--here", is_flag=True, default=False, help="Connect in the current directory without changing the global default instance.")
# fmt: on
def connect(instance: str, here: bool):
    """Set the default database instance for this environment or directory.

    This command updates your local configuration to target the specified instance:
    all subsequent CLI commands and Python/R sessions will auto-connect to this instance.

    You can pass a slug (`account/name`) or URL (`https://lamin.ai/account/name`).

    ```
    # set a default instance for the current environment
    lamin connect laminlabs/cellxgene
    # set a default instance for the current directory
    lamin connect laminlabs/cellxgene --here
    # use a URL instead of a slug
    lamin connect https://lamin.ai/laminlabs/cellxgene
    ```

    → Python/R alternative: create a database object via {class}`~lamindb.DB` or set the default database of your Python/R session via {func}`~lamindb.connect`
    """
    return connect_(instance, here=here)


@main.command()
@click.option("--here", is_flag=True, default=False, help="Disconnect local directory context without changing the global default instance.")
def disconnect(here: bool):
    """Unset the default database instance for this environment or directory.

    - Without `--here`, it clears the global default instance.
    - With `--here`, it removes the nearest local marker from the current
      directory hierarchy and unsets `dev-dir` for that instance.

    For example:

    ```
    lamin disconnect
    lamin disconnect --here
    ```

    → Python/R alternative: {func}`~lamindb.setup.disconnect`
    """
    return disconnect_(here=here)


@main.command("exec", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.argument("target", type=str)
@click.option(
    "--register-output",
    "register_outputs",
    multiple=True,
    type=str,
    help="Register an output artifact path after execution.",
)
@click.pass_context
def exec_(ctx: click.Context, target: str, register_outputs: tuple[str, ...]):
    """Execute a local script or opaque executable.

    The target is launched directly and all remaining argv tokens are passed to the
    child process unchanged.
    """
    import lamindb as ln
    from lamindb._finish import save_run_logs

    resolved_target = resolve_lamin_exec_arg(target)
    target_kind = classify_exec_target(resolved_target)
    transform = _prepare_exec_transform(resolved_target, target_kind)
    run = ln.Run(transform=transform)
    run.started_at = datetime.now(timezone.utc)
    run._status_code = -1
    run.save()

    previous_run = ln.context.run
    ln.context._run = run
    ln.context._stream_tracker.start(run)
    returncode = 1
    try:
        child_argv = rewrite_exec_argv([resolved_target, *ctx.args])
        run.cli_args = shlex.join(child_argv)
        run.save()
        result = subprocess.run(child_argv, check=False)
        returncode = result.returncode
        _register_exec_outputs(
            run, _collect_exec_output_paths(child_argv, register_outputs)
        )
    finally:
        run._status_code = returncode
        run.finished_at = datetime.now(timezone.utc)
        run.save()
        ln.context._stream_tracker.finish()
        ln.context._run = previous_run
        save_run_logs(run, save_run=True)
    raise SystemExit(returncode)


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
@click.option("--branch", is_flag=True, default=False, hidden=True)  # backward compat, no effect
@click.option("-c", "--create", is_flag=True, default=False, help="Create branch if it does not exist.")
# fmt: on
def switch(
    target: tuple[str, ...],
    space: bool = False,
    branch: bool = False,  # backward compat, no effect
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

    To annotate the current branch with a `README.md`, run:

    ```
    lamin annotate branch --readme README.md
    ```

    To comment on the current branch, run:

    ```
    lamin annotate branch --comment "I think we should revisit this, tomorrow, WDYT?"
    ```

    To switch to a target space, pass `--space`:

    ```
    lamin switch --space my_space
    ```

    Find more info in the {class}`~lamindb.Branch` and {class}`~lamindb.Space` documents.

    → Python/R alternative: {attr}`~lamindb.setup.core.SetupSettings.branch` and {attr}`~lamindb.setup.core.SetupSettings.space`
    """
    from lamindb.errors import BranchAlreadyExists, ObjectDoesNotExist
    from lamindb.setup import switch as switch_

    # Backward compatibility: lamin switch branch X / lamin switch space Y (deprecated, hidden from help)
    if len(target) == 2 and target[0] in ("branch", "space"):
        kind, name = target[0], target[1]
        logger.warn(
            f"'lamin switch {kind} <name>' is deprecated and will be removed in a future version. "
            f"Use 'lamin switch {name}' for branches or 'lamin switch --space {name}' for spaces instead.",        )
        try:
            switch_(name, space=(kind == "space"), create=create)
        except (ObjectDoesNotExist, BranchAlreadyExists) as e:
            raise click.ClickException(str(e)) from e
        return

    # Normal usage: single target (or none)
    if len(target) > 1:
        raise click.ClickException("Too many arguments. Use 'lamin switch <target>' or 'lamin switch --space <space>'.")
    target_str = target[0] if len(target) == 1 else None
    try:
        switch_(target_str, space=space, create=create)
    except (ObjectDoesNotExist, BranchAlreadyExists) as e:
        raise click.ClickException(str(e)) from e


# fmt: off
@main.command()
@click.argument("branch", type=str, required=True)
# fmt: on
def merge(branch: str):
    """Merge a branch into the current branch.

    Pass the `name` or `uid` of the source branch to merge into the current branch.

    All `SQLRecord` objects that have `branch_id` equal to the source branch's id
    are updated to the current branch's id. Example:

    ```
    lamin switch main  # switch to the main branch
    lamin merge my_branch  # after this all objects on my_branch will be on main
    ```

    Find more info in the {class}`~lamindb.Branch` document.

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

    Pass an entity or a `--key`. For example:

    ```
    # artifacts & transforms via --key
    lamin load --key mydatasets/mytable.parquet
    lamin load --key analysis.ipynb
    lamin load --key myanalyses/analysis.ipynb --with-env
    # notes via name and topic/type hierarchy
    lamin load README.md
    lamin load my-topic/my-note.md
    # anything via URL
    lamin load https://lamin.ai/account/instance/artifact/e2G7k9EVul4JbfsE
    # anything via registry and --uid
    lamin load artifact --uid e2G7k9EVul4JbfsE
    lamin load transform --uid Vul4JbfsEYAy5
    ```

    → Python/R alternative: {func}`~lamindb.Artifact.load`, no equivalent for transforms
    """
    from lamin_cli._load import load as load_
    from lamin_cli._notes import parse_note_target
    if entity is not None:
        if uid is None and key is None and entity == "README.md":
            return load_(entity=None, uid=uid, key="README.md", with_env=with_env)
        if uid is None and key is None and parse_note_target(entity) is not None:
            return load_(entity, uid=uid, key=key, with_env=with_env)
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
UPDATE_ENTITIES_KEY = {"artifact", "transform", "collection"}
UPDATE_ENTITIES_NAME = {"project", "branch"}
UPDATE_ENTITIES = UPDATE_ENTITIES_KEY | UPDATE_ENTITIES_NAME
STATUS_FIELD_ENTITIES = {"branch"}
DESCRIPTION_FIELD_ENTITIES = UPDATE_ENTITIES_KEY | {"project"}
BRANCH_STATUSES = ["standalone", "draft", "review", "merged", "closed"]


def _resolve_entity_for_get_update(
    entity: str,
    uid: str | None = None,
    key: str | None = None,
    name: str | None = None,
):
    import lamindb as ln

    from lamin_cli._annotate import _get_obj

    try:
        return _get_obj(entity, key=key, uid=uid, name=name)
    except ln.errors.InvalidArgument as e:
        raise click.ClickException(str(e)) from None


def _describe(
    entity: str = "artifact",
    uid: str | None = None,
    key: str | None = None,
    name: str | None = None,
    include: str | None = None,
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
        elif entity == "branch" and name is None:
            # Default to current branch (like lamin annotate)
            record = ln_setup.settings.branch
        elif name is None:
            raise SystemExit(
                f"For entity '{entity}' you must pass --uid or --name"
            )
        else:
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

    record.describe(include=include if include == "comments" else None)


@main.command()
# entity can be a registry or an object in the registry
@click.argument("entity", type=str, default="artifact")
@click.option("--uid", help="The uid for the entity.")
@click.option("--key", help="The key for the entity (artifact, transform, collection).")
@click.option("--name", help="The name for the entity (record, project, ulabel, branch).")
@click.option(
    "--include",
    type=click.Choice(["comments"]),
    default=None,
    help="Include additional content (e.g. 'comments' for readme and comment blocks).",
)
def describe(
    entity: str = "artifact",
    uid: str | None = None,
    key: str | None = None,
    name: str | None = None,
    include: str | None = None,
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
    lamin describe branch  # defaults to current branch
    lamin describe branch --include comments
    lamin describe branch --name main
    ```

    → Python/R alternative: {meth}`~lamindb.Artifact.describe`
    """
    _describe(entity=entity, uid=uid, key=key, name=name, include=include)


@main.command()
# entity can be a registry or an object in the registry
@click.argument("entity", type=str, default="artifact")
@click.option("--uid", help="The uid for the entity.")
@click.option("--key", help="The key for the entity (artifact, transform, collection).")
@click.option("--name", help="The name for the entity (record, project, ulabel, branch).")
@click.option(
    "--include",
    type=click.Choice(["comments"]),
    default=None,
    help="Include additional content (e.g. 'comments' for readme and comment blocks).",
)
@click.option(
    "--status",
    "status_field",
    is_flag=True,
    default=False,
    help="Read branch status.",
)
@click.option(
    "--description",
    "description_field",
    is_flag=True,
    default=False,
    help="Read the description field.",
)
def get(
    entity: str | None = None,
    uid: str | None = None,
    key: str | None = None,
    name: str | None = None,
    include: str | None = None,
    status_field: bool = False,
    description_field: bool = False,
):
    """Get a field value or describe an object.

    If no field flag is passed, this behaves like `lamin describe`.
    If a field flag is passed, it reads that field from the resolved entity.

    Examples:

    ```
    lamin get branch --status                # current branch status
    lamin get branch --name my_branch --status
    lamin get artifact --key my_file.parquet --description
    ```
    """
    if status_field and description_field:
        raise click.ClickException("Pass only one of --status or --description.")
    if include is not None and (status_field or description_field):
        raise click.ClickException(
            "--include can only be used in describe mode (without --status/--description)."
        )

    if status_field:
        if entity is None:
            raise click.ClickException(
                "Pass an entity when reading a field, e.g. 'lamin get branch --status'."
            )
        if entity not in STATUS_FIELD_ENTITIES:
            raise click.ClickException("--status is only supported for entity 'branch'.")
        branch = _resolve_entity_for_get_update(entity, uid=uid, key=key, name=name)
        click.echo(branch.status)
        return

    if description_field:
        if entity is None:
            raise click.ClickException(
                "Pass an entity when reading a field, e.g. 'lamin get artifact --description'."
            )
        if entity not in DESCRIPTION_FIELD_ENTITIES:
            raise click.ClickException(
                "--description is only supported for: artifact, transform, collection, project."
            )
        record = _resolve_entity_for_get_update(entity, uid=uid, key=key, name=name)
        click.echo(record.description)
        return

    _describe(entity=entity or "artifact", uid=uid, key=key, name=name, include=include)


@main.command()
@click.argument(
    "entity", type=click.Choice(["artifact", "transform", "collection", "project", "branch"])
)
@click.option("--uid", help="The uid for the entity.")
@click.option("--key", help="The key for the entity (artifact, transform, collection).")
@click.option("--name", help="The name for the entity (project, branch).")
@click.option(
    "--status",
    type=click.Choice(BRANCH_STATUSES),
    default=None,
    help="Set branch status (branch only).",
)
@click.option(
    "--description",
    type=str,
    default=None,
    help="Set description (artifact, transform, collection, project).",
)
def update(
    entity: Literal["artifact", "transform", "collection", "project", "branch"],
    uid: str | None = None,
    key: str | None = None,
    name: str | None = None,
    status: str | None = None,
    description: str | None = None,
):
    """Update mutable fields of an entity.

    Examples:

    ```
    lamin update branch --status review                  # current branch
    lamin update branch --name my_branch --status draft
    lamin update artifact --key my_file.parquet --description "new description"
    lamin update project --name my_project --description "updated project notes"
    ```
    """
    if status is not None and description is not None:
        raise click.ClickException("Pass only one of --status or --description.")
    if status is None and description is None:
        raise click.ClickException("Pass one of: --status or --description.")

    if status is not None and entity != "branch":
        raise click.ClickException("--status is only supported for entity 'branch'.")

    if description is not None and entity not in DESCRIPTION_FIELD_ENTITIES:
        raise click.ClickException(
            "--description is only supported for: artifact, transform, collection, project."
        )

    record = _resolve_entity_for_get_update(entity, uid=uid, key=key, name=name)

    if status is not None:
        record.status = status
        record.save()
        logger.important(f"updated {entity}: status='{status}'")
        return

    record.description = description
    record.save()
    logger.important(f"updated {entity}: description")


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
    type=click.Choice(["artifact", "transform", "record"]),
    default=None,
    help="Either 'artifact', 'transform', or 'record'. If not passed, chooses based on path suffix.",
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
    registry: Literal["artifact", "transform", "record"] | None,
):
    """Save a file or folder as an `artifact`, `transform`, or `record`.

    Save a **dataset** or **model** as {class}`~lamindb.Artifact`:

    ```
    lamin save my_table.csv --key my_tables/my_table.csv
    ```

    Save **source code** as {class}`~lamindb.Transform`:

    ```
    lamin save my_script.py --key my_scripts/my_script.py
    ```

    Save a **markdown note** as {class}`~lamindb.Record`:

    ```
    lamin save my-topic/my-note.md  # resolves `my-topic` as a record type
    ```

    Save a **README** for the entire database instance:

    ```
    lamin save README.md
    ```

    The `save` command defaults to saving
    `.py`, `.ipynb`, `.R`, `.Rmd`, and `.qmd` files as {class}`~lamindb.Transform`
    and - if ommitting `--key` - `.md` files as {class}`~lamindb.Record`.
    You can enforce saving a file as an {class}`~lamindb.Artifact` by passing `--registry artifact`.

    You can pass a project to `--project` to label the artifact by project.
    If you pass a `--space` or `--branch` identifier, you save the artifact in the corresponding {class}`~lamindb.Space` or on the corresponding {class}`~lamindb.Branch`.

    Save an **agent plan** as {class}`~lamindb.Artifact`:

    ```
    lamin save /path/to/.cursor/plans/my_task.plan.md
    lamin save /path/to/.claude/plans/my_task.md
    ```

    ```{dropdown} How are agent plans handled?

    Plan files are detected by suffix `.plan.md` (Cursor) or by being under `.claude/plans/`
    (Claude Code). For such paths, the `key` defaults to `.plans/<filename>`, the artifact `kind`
    is set to `plan`, and the description is taken from the markdown front matter (`name:` and
    `overview:`). The stored artifact contains only the body (the YAML front matter is stripped).

    ```

    **git:** When saving scripts, files will be synced with a git repo if you set:

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
@click.option("--uid", type=str, default=None, help="The uid of the entity.")
@click.option("--name", type=str, default=None, help="The name of the entity (record, project, ulabel, branch, feature, schema, space).")
@click.option("--project", type=str, default=None, help="A valid project name or uid.")
@click.option("--ulabel", type=str, default=None, help="A valid ulabel name or uid.")
@click.option("--record", type=str, default=None, help="A valid record name or uid.")
@click.option("--version", type=str, default=None, help="A version tag for the artifact, transform, or collection.")
@click.option("--features", multiple=True, help="Feature annotations (artifact/transform only). Supports: feature=value, feature=val1,val2, or feature=\"val1\",\"val2\"")
@click.option("--readme", "readme_path", type=click.Path(exists=True, path_type=Path), default=None, help="Path to a README file to attach as a readme block to the entity.")
@click.option("--comment", type=str, default=None, help="Comment text to attach as a comment block to the entity.")
def annotate(entity: str | None, key: str, uid: str, name: str, project: str, ulabel: str, record: str, version: str, features: tuple, readme_path: Path | None, comment: str | None):
    r"""Annotate an artifact, transform, or collection.

    You can annotate with projects, labels, records, version tags, a readme, a comment, and, for artifacts, with features. For example,

    ```
    # via registry and --uid for any registry
    lamin annotate artifact --uid e2G7k9EVul4JbfsE --project "My Project"
    lamin annotate collection --uid abc123 --version "1.0"
    # via registry and --name for any registry that has a name field
    lamin annotate schema --name my_schema --readme README.md
    # via registry and --key for any registry that as a key field
    lamin annotate collection --key my_collection --version "1.0"
    # via URL for any registry
    lamin annotate https://lamin.ai/account/instance/artifact/e2G7k9EVul4JbfsE --project "My Project"
    lamin annotate https://lamin.ai/account/instance/schema/123456ABCDEF --readme README.md
    ```

    Annotating artifacts and transforms works via `--key` alone:

    ```
    lamin annotate --key raw/sample.fastq --project "My Project"
    lamin annotate --key raw/sample.fastq --ulabel "My ULabel" --record "Experiment 1"
    lamin annotate --key raw/sample.fastq --version "1.0"
    lamin annotate --key raw/sample.fastq --features perturbation=IFNG,DMSO cell_line=HEK297
    lamin annotate --key raw/sample.fastq --readme README.md  # adds a readme to the artifact
    lamin annotate --key raw/sample.fastq --comment "I think we should revisit this, tomorrow, WDYT?"
    lamin annotate --key my-notebook.ipynb --project "My Project"
    ```

    Branch defaults to the current branch:

    ```
    lamin annotate branch --readme README.md  # current branch; or --name my_branch
    ```

    → Python/R alternative: `artifact.features.add_values()` via {meth}`~lamindb.models.FeatureManager.add_values`, `artifact.projects.add()`, `artifact.ulabels.add()`, `artifact.records.add()`, ... via {meth}`~lamindb.models.RelatedManager.add`, and `artifact.version_tag = \"1.0\"; artifact.save()` for version tags.
    """
    from lamin_cli._annotate import (
        ANNOTATE_REGISTRIES,
        REGISTRIES_WITH_FEATURES,
        REGISTRIES_WITH_PROJECT_ULABEL_RECORD,
        REGISTRIES_WITH_VERSION,
        _add_block,
        _get_obj,
        _parse_features_list,
    )
    from lamin_cli._save import infer_registry_from_path

    # Handle URL: decompose and connect (same pattern as load/delete)
    if entity is not None and entity.startswith("https://"):
        url = entity
        instance, registry, uid = decompose_url(url)
        if registry not in ANNOTATE_REGISTRIES:
            raise click.ClickException(
                f"Annotate does not support {registry}. "
                f"Use: {', '.join(sorted(ANNOTATE_REGISTRIES))}"
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
        if registry not in ANNOTATE_REGISTRIES:
            raise click.ClickException(
                f"Annotate does not support {registry}. "
                f"Use: {', '.join(sorted(ANNOTATE_REGISTRIES))}"
            )

    # import lamindb after connect went through
    import lamindb as ln

    try:
        obj = _get_obj(registry, key, uid, name)

        # Handle project annotation (artifact, transform, collection only)
        if project is not None and registry in REGISTRIES_WITH_PROJECT_ULABEL_RECORD:
            project_record = ln.Project.filter(
                ln.Q(name=project) | ln.Q(uid=project)
            ).one_or_none()
            if project_record is None:
                raise ln.errors.InvalidArgument(
                    f"Project '{project}' not found, either create it with `ln.Project(name='...').save()` or fix typos."
                )
            obj.projects.add(project_record)

        # Handle ulabel annotation (artifact, transform, collection only)
        if ulabel is not None and registry in REGISTRIES_WITH_PROJECT_ULABEL_RECORD:
            ulabel_record = ln.ULabel.filter(
                ln.Q(name=ulabel) | ln.Q(uid=ulabel)
            ).one_or_none()
            if ulabel_record is None:
                raise ln.errors.InvalidArgument(
                    f"ULabel '{ulabel}' not found, either create it with `ln.ULabel(name='...').save()` or fix typos."
                )
            obj.ulabels.add(ulabel_record)

        # Handle record annotation (artifact, transform, collection only)
        if record is not None and registry in REGISTRIES_WITH_PROJECT_ULABEL_RECORD:
            record_record = ln.Record.filter(
                ln.Q(name=record) | ln.Q(uid=record)
            ).one_or_none()
            if record_record is None:
                raise ln.errors.InvalidArgument(
                    f"Record '{record}' not found, either create it with `ln.Record(name='...').save()` or fix typos."
                )
            obj.records.add(record_record)

        # Handle version tag annotation (artifact, transform, collection only)
        if version is not None and registry in REGISTRIES_WITH_VERSION:
            obj.__class__.filter(uid=obj.uid).update(version_tag=version)
            obj.refresh_from_db()

        # Handle feature annotations (artifact and transform only)
        if features and registry in REGISTRIES_WITH_FEATURES:
            feature_dict = _parse_features_list(features)
            obj.features.add_values(feature_dict)
    except ln.errors.InvalidArgument as e:
        raise click.ClickException(str(e)) from None

    if features and registry not in REGISTRIES_WITH_FEATURES:
        raise click.ClickException(
            "Feature annotations are only supported for artifact and transform."
        )

    # Handle readme annotation
    if readme_path is not None:
        readme_content = readme_path.read_text(encoding="utf-8")
        _add_block(obj, registry, readme_content, kind="readme")

    # Handle comment annotation
    if comment is not None:
        _add_block(obj, registry, comment, kind="comment")

    obj_rep = (
        obj.key
        if hasattr(obj, "key") and obj.key
        else obj.description
        if hasattr(obj, "description") and obj.description
        else obj.name
        if hasattr(obj, "name") and obj.name
        else obj.uid
    )
    logger.important(f"annotated {registry}: {obj_rep}")


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
