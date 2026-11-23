# Source behavior retained; host-repository lint exceptions.
# ruff: noqa: D402
"""Command-line interface for managing schema plans.

Exposes `lamin evolve` with these terraform-like subcommands:

- `init`: scaffold a new plan file.
- `validate`: finalize and render a plan offline (no instance connection).
- `plan`: show a read-only diff of what applying the plan would create.
- `apply`: create the plan's objects in the instance and write the lock file.
- `destroy`: tear down the plan's objects, refusing if they are referenced.
- `introspect`: reverse-engineer an existing instance into a plan file.
- `repair dangling`: trash live objects that still reference deleted objects.
- `repair duplicates`: trash duplicate features/schemas, keeping the newest.
- `repair dtypes`: repair features whose stored dtype string is broken.
- `repair stranded-values`: delete values a deleted feature left behind.
- `repair sheet-schemas`: detach record types whose schema is a schema type.
- `repair align-uids`: rewrite plan-managed uids on other instances to a
  source instance's.
- `utils add-uid`: add a transform stem uid to an existing plan file.
- `utils generate-stem-uid`: print fresh transform stem uids for plans.
- `utils fingerprint`: fingerprint plans to prove a refactor is drift-free.
- `ci plan`: validate and diff every plan a `lamin_schemas.yml` configures.
- `ci apply`: apply the configured plans to their instances.
- `ci drift`: detect objects no configured plan tracks, for a scheduled run.
- `ci missing-exports`: report the objects each plan must export.
- `ci verify-config`: check every committed plan is referenced or ignored.

The `prune-dangling`, `remove-duplicates` and `verify-dtypes` commands are
kept as deprecated aliases for their `repair` counterparts.

`validate`, `plan` and `apply` require the plan to declare a
`PLAN_TRANSFORM_STEM_UID` (see
:func:`~lamindb_setup.evolve.engine._loader.require_stem_uid`); `init` is the way
to obtain one.

A failure the tool expects -- drift, a refused migration, an unreadable lock --
is reported as `Error: <message>` with exit code 1 rather than a traceback;
see :class:`~lamindb_setup.evolve.core.LaminSchemasError` and
:meth:`_OrderedGroup.invoke`.  An unusable argument -- a plan target naming no
plan -- is reported as a usage error with exit code 2 instead, the way an
invalid option is (see :class:`_Command`).  The engine underneath raises both
and never exits or prompts itself: those are decided here.

A command body imports what it calls itself, lazily where that keeps
`lamindb`, `ci` or the repair commands out of a run that does not need
them.  There is no injection seam at this layer: the tests drive these commands
end to end, as a child process against a real instance (see `tests/evolve/conftest.py`).
"""

from __future__ import annotations

import enum
import os
from contextlib import nullcontext
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from click.exceptions import Exit
from lamindb_setup.evolve.console import (
    OutputFormat,
    configure_query_logging,
    echo_query_log_summary,
    join_sections,
    write_and_echo,
)
from lamindb_setup.evolve.engine import (
    co_applied_prerequisite_keys,
    load_all_plans,
    load_plans_in_file,
    no_default_instance,
    parse_target,
    project_root_override,
    require_stem_uid,
    reset_user_announcement,
    safe_connect,
    sibling_claimed_keys,
)

from ._click import (
    Argument as _Argument,
)
from ._click import (
    Command,
    Group,
    click,
    configure_help,
)
from ._click import (
    EnumChoice as _EnumChoice,
)
from ._click import (
    as_list as _as_list,
)
from ._click import (
    as_optional_list as _as_optional_list,
)

if TYPE_CHECKING:
    from collections import Counter
    from collections.abc import Iterable
    from types import ModuleType

    from lamindb_setup.evolve.authoring import Plan
    from lamindb_setup.evolve.ci import CIConfig
    from lamindb_setup.evolve.engine import (
        LockFile,
        ManagingPlan,
        PlanReference,
        SheetExportError,
        TrackConfig,
    )

__all__ = ["app"]

#: Environment variable that turns a handled `LaminSchemasError` back into a
#: traceback (see :meth:`_OrderedGroup.invoke`).
TRACEBACK_ENV_VAR = "LAMIN_SCHEMA_EVOLUTION_TRACEBACK"

# Explicit top-level command order per help panel. Click registers plain
# commands before sub-command groups, so without this a plain command (e.g.
# `introspect`) would sort ahead of the `repair`/`utils`/`ci` groups
# within a panel. Names not listed keep their registration order (stable sort).
_COMMAND_ORDER = (
    "init",
    "validate",
    "plan",
    "apply",
    "destroy",
    "introspect",
    "repair",
    "utils",
    "ci",
    "prune-dangling",
    "remove-duplicates",
    "verify-dtypes",
)


class _OrderedGroup(Group):
    """A Click group that lists commands in :data:`_COMMAND_ORDER`."""

    def list_commands(self, ctx: Any) -> list[str]:
        names = super().list_commands(ctx)
        rank = {name: index for index, name in enumerate(_COMMAND_ORDER)}
        return sorted(names, key=lambda name: rank.get(name, len(rank)))

    def invoke(self, ctx: Any) -> Any:
        """Render an expected failure as `Error: <message>` instead of a traceback.

        Every command in the tree runs inside this call, so catching here covers
        the subcommand groups too.  Only :class:`LaminSchemasError` is caught:
        those errors are written to be read by the operator and already say how
        to get out of the state, and burying them under a stack trace is what
        made the careful wording pointless.  Anything else is a bug in this
        package and keeps its traceback.

        The message goes to stderr with the same `Error:` prefix the commands
        already use for the failures they handle themselves, so a caller cannot
        tell which of the two paths reported a given failure.

        Set `LAMIN_SCHEMA_EVOLUTION_TRACEBACK=1` to re-raise instead, which is
        how to get a stack trace when the message is the thing under suspicion.
        """
        from lamindb_setup.evolve.core import LaminSchemasError

        try:
            return super().invoke(ctx)
        except LaminSchemasError as exc:
            if os.environ.get(TRACEBACK_ENV_VAR):
                raise
            click.echo(f"Error: {exc}", err=True)
            raise Exit(code=1) from exc


class _Command(Command):
    """A command that reports an unusable argument the engine found as a usage error."""

    def invoke(self, ctx: Any) -> Any:
        """Turn an :class:`InvalidArgumentError` into `click.BadParameter`.

        The engine raises it for a plan target naming no plan, among others.
        It is an argument of *this* command that is wrong, so it is reported
        against this command's context -- its usage line, `Invalid value:`
        and exit code 2 -- exactly as when the engine raised the Click error
        itself.  It has to happen here, before :meth:`_OrderedGroup.invoke`
        would render it as a plain `Error:`, because only the command still
        holds its own context.
        """
        from lamindb_setup.evolve.core import InvalidArgumentError

        try:
            return super().invoke(ctx)
        except InvalidArgumentError as exc:
            raise click.BadParameter(str(exc), ctx=ctx) from exc


def check_output_format(output_format: OutputFormat, output: Path | None) -> None:
    """Refuse `--output` with the one format that has nothing to write.

    The terminal view is drawn straight to the console by `rich`; there is no
    document to redirect, and writing its escape sequences to a file would be a
    worse answer than saying so.  Previously the option was simply ignored, so a
    workflow expecting a report found no file and no explanation.
    """
    if output is not None and not output_format.is_text:
        raise click.BadParameter(
            "--output needs a format that produces a document: pass "
            "'--format markdown' or '--format json' alongside it.",
            param_hint="--output",
        )


def _version_callback(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Print the installed package version and exit when `--version` is given."""
    if value:
        from lamindb_setup.evolve import __version__

        click.echo(__version__)
        raise Exit()


_PROFILE_HELP = (
    "Profile the command with pyinstrument and write the report here. A "
    "'.html' suffix produces the interactive flame view, anything else a "
    "plain-text tree. Needs the 'profile' extra."
)


def _start_profiler(ctx: click.Context, output: Path) -> None:
    """Profile until `ctx` closes, then write the report to `output`.

    Registered on the top-level context rather than wrapped around the
    subcommand so the report covers the whole run -- including the connect and
    import work that happens before a subcommand body starts -- and is still
    written when the command exits non-zero, which is exactly when a slow run
    is worth looking at.
    """
    try:
        from pyinstrument import Profiler
    except ImportError as exc:  # pragma: no cover - depends on the environment
        click.echo(
            "Error: --profile needs pyinstrument. Install it with "
            "'pip install lamin-cli[profile]'.",
            err=True,
        )
        raise Exit(code=1) from exc

    profiler = Profiler()
    profiler.start()

    def _write_report() -> None:
        profiler.stop()
        rendered = (
            profiler.output_html()
            if output.suffix == ".html"
            else profiler.output_text(unicode=True, color=False)
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered)
        # stderr: stdout carries the command's own output, which callers pipe.
        click.echo(f"Wrote profile to {output}", err=True)

    ctx.call_on_close(_write_report)


_LOG_QUERIES_HELP = (
    "Log every SQL statement issued against an instance to stderr, with its "
    "duration, and a total when the command finishes. Use it to see which query "
    "a slow command is waiting on."
)

_PROJECT_ROOT_HELP = (
    "Directory plan files are imported relative to, so a plan can import "
    "another one by its full dotted path ('from plans.wetlab.plan_x import y'). "
    "Defaults to the nearest ancestor of each plan holding a lamin_schemas.yml, "
    "pyproject.toml or .git, else the plan's own directory. Pass this only when "
    "the plans are scattered or vendored outside such a root."
)


@click.group(
    name="evolve",
    cls=_OrderedGroup,
    no_args_is_help=True,
    help="Manage LaminDB schema plans.",
)
@click.option(
    "--version",
    "version",
    default=False,
    show_default=True,
    is_flag=True,
    callback=_version_callback,
    is_eager=True,
    help="Print the installed lamindb-setup evolution version and exit.",
)
@click.option(
    "--log-queries",
    "log_queries",
    default=False,
    show_default=True,
    is_flag=True,
    help=_LOG_QUERIES_HELP,
)
@click.option(
    "--project-root",
    "project_root",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help=_PROJECT_ROOT_HELP,
)
@click.option("--profile", type=click.Path(path_type=Path), help=_PROFILE_HELP)
@click.pass_context
def app(
    ctx: click.Context,
    version: bool,
    log_queries: bool,
    project_root: Path,
    profile: Path | None,
) -> None:
    r"""Manage LaminDB schema plans.

    \f
    Whichever instance the user selected with `lamin connect` is masked for the
    whole run (see :func:`no_default_instance`) and restored when the command
    closes, so that no command connects to an instance it was not pointed at
    with `--instance`. Importing the evolution backend itself does not load
    lamindb, so the callback can establish the context before dtype resolution.

    The logged-in Lamin user is announced by the first connection this run opens
    (see :func:`echo_current_user`); the flag guarding that is reset here so the
    announcement is per run, not per process. Query logging is configured here
    for the same reason, and its summary registered as a close hook so it is
    printed even when the command exits non-zero.

    `--project-root` is applied here too, so it outranks the root a
    configuration file implies for the commands that read one.
    """
    if profile is not None:
        _start_profiler(ctx, profile)
    configure_query_logging(log_queries)
    if log_queries:
        ctx.call_on_close(echo_query_log_summary)
    reset_user_announcement()
    ctx.with_resource(project_root_override(project_root))
    ctx.with_resource(no_default_instance())


_INTROSPECTION_PANEL = "Introspection, debugging & migration"
_DEPRECATED_PANEL = "Deprecated"

_TARGETS_HELP = (
    "One or more plans to act on, each as 'path/to/plan.py:plan_variable' (the "
    "':plan_variable' suffix is optional when the file defines exactly one "
    "plan). When several are given they are evaluated together: an object "
    "dropped from one plan but still declared by another listed plan is treated "
    "as moved between plans, not deleted."
)
_INSTANCE_HELP = "LaminDB instance to act against, as 'account/instance'."
_FORMAT_HELP = (
    "Output format: 'terminal' for colored output (default), 'markdown' for a "
    "GitHub-flavored fragment to post on a pull request, or 'json' for a "
    "machine-readable document to pipe into a script."
)
_OUTPUT_HELP = (
    "Also write the rendered document to this file. Needs --format markdown or "
    "--format json."
)
_OUTPUT_DIR_HELP = "Directory to write generated files into."
_STRICT_RENAMES_HELP = (
    "Refuse to apply a delete+create that looks like a rename not declared with "
    "ctx.moved (default). Use --no-strict-renames to allow it when the objects "
    "are genuinely distinct."
)
_ALLOW_DATA_VALIDATED_CHANGES_HELP = (
    "Allow a blocked validation-affecting change to a feature when the live data "
    "proves it safe: the feature has no stored values, or every value validates "
    "against the new dtype. Off by default."
)
_SHOW_UNCHANGED_HELP = (
    "List every object of the plan in the terminal diff, unchanged ones included."
)


def _warn_deprecated(old: str, new: str) -> None:
    """Print a deprecation notice steering `old` usage towards `new`."""
    click.echo(f"Warning: {old} is deprecated; use {new} instead.", err=True)


def _fold_deprecated_alias(
    value: Path | None, alias_value: Path | None, *, alias: str, option: str
) -> Path | None:
    """The value of `option`, given either itself or its hidden `alias`.

    The alias is a separate, hidden option so that `--help` lists only the
    name to use.  It still works, with a deprecation warning; given together
    with `option` and a different value, it is a usage error rather than a
    silent choice between the two.
    """
    if alias_value is None:
        return value
    _warn_deprecated(alias, option)
    if value is not None and value != alias_value:
        raise click.BadParameter(
            f"{alias} and {option} disagree; pass only {option}.",
            param_hint=option,
        )
    return alias_value


def _prune_lock_after_destroy(
    lock: LockFile, lock_path: Path, keys: Iterable[str]
) -> int:
    """Drop the destroyed objects' `keys` from `lock` and persist the result.

    Destroying an object leaves its lock entry stale: the recorded `uid` no
    longer resolves to a live record, so a later `apply` would abort as drift
    ("the lock records uid ... but no matching object exists") instead of
    re-creating it.  Pruning the entry lets `apply` treat the object as absent
    and re-create it from a clean slate, making `destroy` + `apply` a working
    replace workflow.  The pruned lock is rewritten; when no entries remain the
    lock file is deleted.  Returns the number of entries pruned.
    """
    removed = sum(1 for key in keys if lock.remove(key))
    if not removed:
        return 0
    if not lock.is_empty:
        lock.write(lock_path)
    elif lock_path.exists():
        lock_path.unlink()
    return removed


@app.command(no_args_is_help=True, cls=_Command)
@click.argument(
    "name",
    cls=_Argument,
    required=False,
    type=str,
    help="Plan name, e.g. 'mynewplan' -> plan_mynewplan.py.",
)
@click.option(
    "--output-dir",
    "--directory",
    "-d",
    "directory",
    default=Path(),
    show_default=True,
    type=click.Path(path_type=Path),
    help="Directory in which to create the new plan file.",
)
@click.option(
    "--add-uid",
    "add_uid",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help="Deprecated: use 'lamin evolve utils add-uid <plan>'. Add a stem uid to this existing plan file instead of scaffolding.",
)
@click.option(
    "--minimal",
    "minimal",
    default=False,
    show_default=True,
    is_flag=True,
    help="Scaffold a bare plan without the commented building-block examples.",
)
@click.option(
    "--instance",
    "-i",
    "instance",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Instance(s) to check the generated uid against for collisions. Repeat to require the uid to be free on several instances.",
)
def init(
    name: str, directory: Path, add_uid: Path, minimal: bool, instance: list[str]
) -> None:
    """Scaffold a new plan file with a fresh transform stem uid.

    Writes 'plan_<name>.py' into --output-dir (the current directory by default)
    and prints the commands that take it from there: 'validate', 'plan', then
    'apply' once the file is committed and pushed.
    """
    from lamindb_setup.evolve.engine import PLAN_STEM_UID_VAR
    from lamindb_setup.evolve.introspect import (
        add_stem_uid_to_plan,
        generate_plan_stem_uid,
        plan_slug,
        render_plan,
    )

    # Mode 1: add a stem uid to an existing plan file.
    if add_uid is not None:
        _warn_deprecated("init --add-uid", "utils add-uid")
        if name is not None:
            click.echo(
                "Error: pass either a plan name or --add-uid, not both.", err=True
            )
            raise Exit(code=1)
        uid = add_stem_uid_to_plan(add_uid, instance or ())
        click.echo(f"Added {PLAN_STEM_UID_VAR} = {uid!r} to {add_uid}")
        return

    # Mode 2: scaffold a brand-new plan file.
    if name is None:
        click.echo("Error: provide a plan name or use --add-uid.", err=True)
        raise Exit(code=1)

    slug = plan_slug(name)
    file_stem = slug if slug.startswith("plan_") else f"plan_{slug}"
    func = slug if not slug[0].isdigit() else f"plan_{slug}"
    plan_path = directory / f"{file_stem}.py"
    if plan_path.exists():
        click.echo(f"Error: {plan_path} already exists.", err=True)
        raise Exit(code=1)

    uid = generate_plan_stem_uid(instance or ())
    content = render_plan(slug, func, uid, minimal=minimal)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(content)

    target = f"{plan_path}:{func}"
    click.echo(f"Created plan: {plan_path}")
    click.echo(f"  {PLAN_STEM_UID_VAR} = {uid!r}")
    click.echo("Next steps:")
    click.echo(f"  lamin evolve validate {target}")
    click.echo(f"  lamin evolve plan {target} -i <instance>")
    click.echo(f"  commit and push it, then: lamin evolve apply {target} -i <instance>")


@app.command(no_args_is_help=True, cls=_Command)
@click.argument(
    "targets",
    cls=_Argument,
    required=True,
    type=str,
    nargs=-1,
    callback=_as_list,
    help=_TARGETS_HELP,
)
@click.option(
    "--all-plans",
    "-a",
    "all_plans",
    default=False,
    show_default=True,
    is_flag=True,
    help="Include every plan defined in each provided file, instead of a single plan per target. Targets must be plain file paths without a ':plan_variable' suffix.",
)
@click.option(
    "--format",
    "-f",
    "output_format",
    default=OutputFormat.terminal,
    show_default=True,
    type=_EnumChoice(OutputFormat),
    help=_FORMAT_HELP,
)
@click.option(
    "--output",
    "-o",
    "output",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help=_OUTPUT_HELP,
)
def validate(
    targets: list[str], all_plans: bool, output_format: OutputFormat, output: Path
) -> None:
    """Finalize and render one or more plans offline (no instance connection)."""
    from lamindb_setup.evolve.render import (
        JsonPlanSummary,
        MarkdownPlanSummary,
        TerminalPlanSummary,
    )

    check_output_format(output_format, output)
    if all_plans:
        loaded: list[tuple[Plan, Path, ModuleType]] = []
        seen_files: set[Path] = set()
        for target in targets:
            path, var = parse_target(target)
            if var is not None:
                raise click.BadParameter(
                    "cannot combine --all-plans with an explicit "
                    f"':plan_variable' suffix ({target!r})"
                )
            resolved = path.resolve()
            if resolved in seen_files:
                raise click.BadParameter(f"file {path} is listed more than once")
            seen_files.add(resolved)
            loaded.extend(load_plans_in_file(path))
    else:
        loaded = load_all_plans(targets)

    renderers = {
        OutputFormat.markdown: MarkdownPlanSummary,
        OutputFormat.json: JsonPlanSummary,
    }
    sections: list[str] = []
    for plan_obj, plan_file, plan_module in loaded:
        require_stem_uid(plan_module, plan_file)
        plan_obj.finalize()
        renderer = renderers.get(output_format)
        if renderer is not None:
            sections.append(renderer().render(plan_obj))
        else:
            TerminalPlanSummary().print_summary(plan_obj)
    if output_format.is_text:
        write_and_echo(join_sections(sections, output_format), output)


@app.command(no_args_is_help=True, cls=_Command)
@click.argument(
    "targets",
    cls=_Argument,
    required=True,
    type=str,
    nargs=-1,
    callback=_as_list,
    help=_TARGETS_HELP,
)
@click.option(
    "--instance",
    "-i",
    "instance",
    required=True,
    show_default=True,
    type=str,
    help=_INSTANCE_HELP,
)
@click.option(
    "--format",
    "-f",
    "output_format",
    default=OutputFormat.terminal,
    show_default=True,
    type=_EnumChoice(OutputFormat),
    help=_FORMAT_HELP,
)
@click.option(
    "--output",
    "-o",
    "output",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help=_OUTPUT_HELP,
)
@click.option(
    "--preview-migrations",
    "preview_migrations",
    default=False,
    show_default=True,
    is_flag=True,
    help="Rehearse the apply inside a transaction that is always rolled back: create what the plan creates, run every declared ctx.migrate() migration and its checks, and report the outcome in the diff -- or fail as the apply would. Nothing is persisted. Off by default so the diff stays a pure read-only preview that never runs migration code.",
)
@click.option(
    "--strict-uids/--no-strict-uids",
    "strict_uids",
    default=True,
    show_default=True,
    is_flag=True,
    help="Judge an object whose uid differs from the uid the plan's lock files for other instances record as 'apply' does (default): --fail-on-diff counts it as pending, and --preview-migrations refuses to create an object such a lock pins but something else holds here. --no-strict-uids only reports the misalignment and rehearses the apply that creates the object with a fresh uid instead.",
)
@click.option(
    "--strict-renames/--no-strict-renames",
    "strict_renames",
    default=True,
    show_default=True,
    is_flag=True,
    help=f"{_STRICT_RENAMES_HELP} 'plan' shows the diff and exits 1 wherever 'apply' would refuse; --no-strict-renames only warns.",
)
@click.option(
    "--allow-data-validated-changes",
    "allow_data_validated_changes",
    default=False,
    show_default=True,
    is_flag=True,
    help=f"{_ALLOW_DATA_VALIDATED_CHANGES_HELP} 'plan' then shows the diff 'apply --allow-data-validated-changes' would apply.",
)
@click.option(
    "--fail-on-diff",
    "fail_on_diff",
    default=False,
    show_default=True,
    is_flag=True,
    help="Exit 1 once every plan is shown (and --output written) when any of them has a pending change of its own, or has a misaligned uid 'apply' would refuse (see --strict-uids). Off by default: only a refusal fails the run.",
)
@click.option(
    "--show-unchanged",
    "show_unchanged",
    default=False,
    show_default=True,
    is_flag=True,
    help=_SHOW_UNCHANGED_HELP,
)
def plan(
    targets: list[str],
    instance: str,
    output_format: OutputFormat,
    output: Path,
    preview_migrations: bool,
    strict_uids: bool,
    strict_renames: bool,
    allow_data_validated_changes: bool,
    fail_on_diff: bool,
    show_unchanged: bool,
) -> None:
    """Show a read-only diff of what applying the plan(s) would create.

    Pass several plan targets to evaluate them together against the instance:
    an object dropped from one plan but still declared by another listed plan is
    reported as moved between plans, not as a deletion, and an object a plan
    borrows from a listed prerequisite plan that the instance lacks is reported
    as created by that plan in this run, as 'apply' applies them
    dependencies-first.

    Every plan is shown; the command then exits non-zero if 'apply' would
    refuse any of them over a rename not declared with ctx.moved (see
    --strict-renames) or over an object it borrows from a prerequisite plan
    that is neither applied to the instance nor listed, or -- with
    --fail-on-diff -- if any of them has changes pending.
    """
    from lamindb_setup.evolve.console import progress_reporter
    from lamindb_setup.evolve.core import ApplyMode
    from lamindb_setup.evolve.engine import (
        LockFile,
        compute_changes,
        lock_path,
        order_dependencies_first,
        preview_changes,
        read_sibling_uids,
        rehearsal,
    )
    from lamindb_setup.evolve.render import (
        append_notes,
        missing_prerequisite_message,
        print_changes,
        render_changes_json,
        render_changes_markdown,
        render_move_outcome,
        render_uid_misalignments,
        unmoved_rename_refusal,
    )

    check_output_format(output_format, output)
    loaded = load_all_plans(targets)
    if preview_migrations:
        # Rehearsed in the order `apply` runs them: a plan adopts the objects
        # its prerequisites create, so they have to exist by then.
        loaded = order_dependencies_first(loaded)
    for _obj, plan_file, plan_module in loaded:
        require_stem_uid(plan_module, plan_file)
    claimed = sibling_claimed_keys([obj for obj, _f, _m in loaded])
    # What each plan's listed prerequisites declare, and so create first.  Only
    # a plain diff needs it: a rehearsal really creates them first.
    co_applied = co_applied_prerequisite_keys([obj for obj, _f, _m in loaded])
    instance_slug = safe_connect(instance)

    sections: list[str] = []
    # What a strict `apply` would refuse, reported once every plan is shown.
    refusals: list[str] = []
    # Whether any plan has something `apply` would do or refuse, for
    # `--fail-on-diff` -- judged as `ci plan` judges it (`PRReport.has_changes`).
    pending = False
    # One rehearsal for all of them, so each plan sees what the ones before it
    # created -- rolled back once they are all reported.
    with rehearsal() if preview_migrations else nullcontext():
        for (plan_obj, plan_file, _module), claimed_keys, co_applied_keys in zip(
            loaded, claimed, co_applied, strict=False
        ):
            lock = LockFile.read(lock_path(plan_file, instance_slug))
            with progress_reporter() as progress:
                common: dict[str, Any] = {
                    "lock": lock,
                    "claimed_keys": claimed_keys,
                    "report": progress.report,
                    "track": progress.track,
                    "sibling_uids": read_sibling_uids(
                        plan_file, plan_obj, instance_slug
                    ),
                    "allow_data_validated_changes": allow_data_validated_changes,
                    "strict_uids": strict_uids,
                }
                plan_ctx = (
                    preview_changes(plan_obj, **common)
                    if preview_migrations
                    else compute_changes(
                        plan_obj,
                        mode=ApplyMode.PLAN,
                        co_applied_keys=co_applied_keys,
                        **common,
                    )
                )
            # A borrowed object is its prerequisite's change, not this plan's --
            # unless no plan of the run creates it, which `apply` refuses.
            pending = (
                pending
                or any(c.is_own_change for c in plan_ctx.changes)
                or bool(plan_ctx.missing_prerequisites)
                or bool(strict_uids and plan_ctx.uid_misalignments)
            )
            title = f"Plan: {plan_obj.name}"
            move_notes = [
                *render_move_outcome(
                    plan_ctx.move_outcome, instance_slug=instance_slug
                ),
                *render_uid_misalignments(plan_ctx, instance_slug=instance_slug),
            ]
            if output_format is OutputFormat.markdown:
                sections.append(
                    append_notes(
                        render_changes_markdown(plan_ctx, title=title), move_notes
                    )
                )
            elif output_format is OutputFormat.json:
                # The notes are advisory prose about declared moves; a JSON
                # consumer gets them as data rather than appended to the document.
                sections.append(
                    render_changes_json(plan_ctx, title=title, notes=move_notes)
                )
            else:
                print_changes(plan_ctx, title=title, show_unchanged=show_unchanged)
                for note in move_notes:
                    click.echo(note)
            if strict_renames and plan_ctx.suspected_renames:
                refusals.append(
                    unmoved_rename_refusal(plan_obj.name, plan_ctx.suspected_renames)
                )
            if plan_ctx.missing_prerequisites:
                refusals.append(
                    missing_prerequisite_message(
                        plan_obj.name,
                        plan_ctx.missing_prerequisites,
                        instance=instance,
                    )
                )
    if output_format.is_text:
        write_and_echo(join_sections(sections, output_format), output)
    for refusal in refusals:
        click.echo(f"Error: {refusal}", err=True)
    if refusals or (fail_on_diff and pending):
        raise Exit(code=1)


@app.command(no_args_is_help=True, cls=_Command)
@click.argument(
    "targets",
    cls=_Argument,
    required=True,
    type=str,
    nargs=-1,
    callback=_as_list,
    help=_TARGETS_HELP,
)
@click.option(
    "--instance",
    "-i",
    "instance",
    required=True,
    show_default=True,
    type=str,
    help=_INSTANCE_HELP,
)
@click.option(
    "--yes",
    "-y",
    "yes",
    default=False,
    show_default=True,
    is_flag=True,
    help="Skip the confirmation prompt.",
)
@click.option(
    "--strict-renames/--no-strict-renames",
    "strict_renames",
    default=True,
    show_default=True,
    is_flag=True,
    help=f"{_STRICT_RENAMES_HELP} With --config, 'strict_renames: false' there allows it too.",
)
@click.option(
    "--allow-data-validated-changes",
    "allow_data_validated_changes",
    default=False,
    show_default=True,
    is_flag=True,
    help=f"{_ALLOW_DATA_VALIDATED_CHANGES_HELP} With --config, 'allow_data_validated_changes: true' there opts in too.",
)
@click.option(
    "--strict-uids/--no-strict-uids",
    "strict_uids",
    default=True,
    show_default=True,
    is_flag=True,
    help="Refuse to apply while an object's uid differs from the uid the plan's lock files for other instances record (default); align them with 'repair align-uids'. Use --no-strict-uids (or, with --config, 'strict_uids: false' there) to apply anyway, creating an object that cannot be pinned with a fresh uid.",
)
@click.option(
    "--format",
    "-f",
    "output_format",
    default=OutputFormat.terminal,
    show_default=True,
    type=_EnumChoice(OutputFormat),
    help=_FORMAT_HELP,
)
@click.option(
    "--output",
    "-o",
    "output",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help=_OUTPUT_HELP,
)
@click.option(
    "--config",
    "-c",
    "config",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help="Path to a lamin_schemas.yml whose settings this apply honours as 'ci apply' does: the instance's 'track_space'/'track_prefix' (falling back to the LAMIN_SCHEMA_EVOLUTION_TRACK_SPACE/LAMIN_SCHEMA_EVOLUTION_TRACK_PREFIX env vars), 'allow_data_validated_changes' (ORed with the flag), and 'strict_uids'/'strict_renames' (ANDed with the flags).",
)
@click.option(
    "--show-unchanged",
    "show_unchanged",
    default=False,
    show_default=True,
    is_flag=True,
    help=_SHOW_UNCHANGED_HELP,
)
@click.pass_context
def apply(
    ctx: click.Context,
    targets: list[str],
    instance: str,
    yes: bool,
    strict_renames: bool,
    allow_data_validated_changes: bool,
    strict_uids: bool,
    output_format: OutputFormat,
    output: Path,
    config: Path,
    show_unchanged: bool,
) -> None:
    """Create the plan(s)' objects in the instance and write the lock file(s).

    Pass several plan targets to apply them together against the instance: an
    object dropped from one plan but still declared by another listed plan is
    moved between plans rather than deleted.  Each plan still writes its own
    lock file and is tracked as its own transform.

    With --config the file's switches count as they do for 'ci apply':
    either the flag or the file opts in to data-validated changes or turns the
    uid and rename checks off.
    """
    from lamindb_setup.evolve.engine import (
        ApplyAbortedError,
        SheetExportError,
        TrackConfig,
        TrackError,
        apply_one,
        ensure_all_committed_and_pushed,
        order_dependencies_first,
        verified_session,
    )

    check_output_format(output_format, output)
    # Before the plans are imported: the config decides what they import from.
    cfg = _load_ci_config(ctx, config) if config is not None else None
    if cfg is not None:
        allow_data_validated_changes = (
            allow_data_validated_changes or cfg.allow_data_validated_changes
        )
        strict_uids = strict_uids and cfg.strict_uids
        strict_renames = strict_renames and cfg.strict_renames
    loaded = load_all_plans(targets)
    # A plan only ever *adopts* the objects a prerequisite owns, so the owner
    # has to be applied first or the borrowed objects do not exist yet. Listing
    # both is a statement that they belong together, not a statement about
    # order, so the order is derived rather than demanded of the caller.
    loaded = order_dependencies_first(loaded)
    stem_uids = [
        require_stem_uid(plan_module, plan_file)
        for _obj, plan_file, plan_module in loaded
    ]

    # A plan may only be applied if it is committed and pushed to git; fail fast
    # for every plan before connecting to the instance.
    try:
        verified = ensure_all_committed_and_pushed(
            [plan_file for _obj, plan_file, _m in loaded],
        )
    except TrackError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise Exit(code=1) from exc

    claimed = sibling_claimed_keys([obj for obj, _f, _m in loaded])
    instance_slug = safe_connect(instance)
    track_config = (
        cfg.track_config_for(instance) if cfg is not None else TrackConfig.resolve()
    )

    sections: list[str] = []
    export_failure: SheetExportError | None = None
    # A text format's stdout is the document, so the prompt and its answer go to
    # stderr, below the preview `apply_one` prints there before asking.
    prompt_on_stderr = output_format.is_text
    confirm = None if yes else partial(click.confirm, err=prompt_on_stderr)
    # Hand the gate's findings to the tracking of each plan so it does not spawn
    # git again to re-establish them (see `verified_session`).
    with verified_session(verified or {}):
        for (plan_obj, plan_file, _module), stem_uid, claimed_keys in zip(
            loaded, stem_uids, claimed, strict=False
        ):
            try:
                section = apply_one(
                    plan_obj,
                    plan_file,
                    stem_uid,
                    instance,
                    instance_slug,
                    claimed_keys=claimed_keys,
                    confirm=confirm,
                    output_format=output_format,
                    strict_renames=strict_renames,
                    allow_data_validated_changes=allow_data_validated_changes,
                    strict_uids=strict_uids,
                    track_config=track_config,
                    show_unchanged=show_unchanged,
                )
            except ApplyAbortedError as exc:
                click.echo("Aborted.", err=prompt_on_stderr)
                raise Exit(code=1) from exc
            except SheetExportError as exc:
                # The plan itself applied; only its sheet re-exports did not.
                # Report everything that ran and fail the command afterwards
                # rather than swallowing the sections already rendered.
                export_failure = exc
                section = exc.section
                if section is not None:
                    sections.append(section)
                break
            if section is not None:
                sections.append(section)
    if output_format.is_text:
        write_and_echo(join_sections(sections, output_format), output)
    if export_failure is not None:
        raise Exit(code=1)


def _load_ci_config(ctx: click.Context, config: Path) -> CIConfig:
    """Load the CI configuration and import this run's plans relative to it.

    Plan files are imported relative to the configuration's `project_root`,
    else to its own directory -- the base its plan paths already resolve
    against -- unless the run carries an explicit `--project-root`, which
    wins.  The override is released when the command's context closes.
    """
    from lamindb_setup.evolve.ci import ConfigError, load_config

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise Exit(code=1) from exc
    ctx.with_resource(project_root_override(cfg.import_root))
    return cfg


# Cap how many individual blocking references are listed when a destroy is
# refused.  The total count is always shown; only the per-record preview is
# bounded so listing never materializes (and prints) potentially millions of
# referencing rows.
_MAX_LISTED_REFERENCES = 20


def _blocking_reference_lines(merged: Any) -> list[str]:
    """Describe a bounded preview of the references that block a destroy.

    Only :data:`_MAX_LISTED_REFERENCES` individual references are fetched; the
    rest are summarized as a trailing count.  This keeps listing fast even when
    a target is referenced by huge numbers of rows, since the underlying query
    is sliced instead of fully materialized.
    """
    listed = merged.references(limit=_MAX_LISTED_REFERENCES)
    lines = [reference.describe() for reference in listed]
    remaining = merged.total_references - len(listed)
    if remaining > 0:
        lines.append(f"... and {remaining} more reference(s).")
    return lines


def _echo_blocking_references(merged: Any) -> None:
    """Print a bounded preview of the references that block a destroy."""
    for line in _blocking_reference_lines(merged):
        click.echo(line, err=True)


def _held_by_plans(
    held: list[PlanReference], *, single: bool
) -> tuple[str, list[ManagingPlan]]:
    """Summarize references other plans' objects hold, and name those plans.

    Returns `"2 object(s) managed by plan 'child' (plans/plan_child.py)
    reference objects this plan manages"` and the plans, in the order first
    met.  The count is of the distinct plan-managed objects, not of the rows.
    """
    plans = list(dict.fromkeys(plan for item in held for plan in item.plans))
    count = len({uid for item in held for uid in item.uids})
    noun = "plan" if len(plans) == 1 else "plans"
    names = ", ".join(plan.describe() for plan in plans)
    manages = "this plan manages" if single else "these plans manage"
    summary = f"{count} object(s) managed by {noun} {names} reference objects {manages}"
    return summary, plans


def _echo_plan_held_references(held: list[PlanReference], *, several: bool) -> None:
    """Print a bounded preview of the references other plans' objects hold.

    Each line names the holding plan when `several` plans hold references.
    """
    for item in held[:_MAX_LISTED_REFERENCES]:
        line = item.reference.describe()
        if several:
            line += "  [" + ", ".join(f"plan {p.name!r}" for p in item.plans) + "]"
        click.echo(line, err=True)
    if len(held) > _MAX_LISTED_REFERENCES:
        click.echo(
            f"... and {len(held) - _MAX_LISTED_REFERENCES} more reference(s).",
            err=True,
        )


def _destroy_together_command(
    plans: list[ManagingPlan], targets: list[str], instance: str, *, permanent: bool
) -> str:
    """The `destroy` that tears the holding `plans` down with `targets`."""
    import shlex

    args = ["lamin", "evolve", "destroy", *sorted(plan.target for plan in plans)]
    args += [*targets, "-i", instance]
    if permanent:
        args.append("--permanent")
    return shlex.join(args)


def _refuse_or_warn_plan_held_references(
    held: list[PlanReference],
    merged: Any,
    *,
    targets: list[str],
    instance: str,
    permanent: bool,
    force: bool,
    trash_references: bool,
) -> None:
    """Refuse a destroy blocked by other plans' objects, or warn that it goes on.

    Without --force or --trash-references the plans holding the references are
    named, with the `destroy` that tears them down together with the listed
    `targets` (one plan each, as given), and the command exits 1 -- no inline
    trash offer, whatever else blocks.  With one of them it goes on, warning
    that the holding plans' lock files are left stale.
    """
    summary, holders = _held_by_plans(held, single=len(targets) == 1)
    command = _destroy_together_command(holders, targets, instance, permanent=permanent)
    if not force and not trash_references:
        click.echo(f"Error: {summary}.", err=True)
        _echo_plan_held_references(held, several=len(holders) > 1)
        others = merged.total_references - len(held)
        if others > 0:
            click.echo(
                f"{others} other reference(s) come from objects no plan manages.",
                err=True,
            )
        click.echo(f"Destroy them together: {command}", err=True)
        click.echo("Nothing was destroyed.", err=True)
        raise Exit(code=1)
    it, its_locks = (
        ("that plan", "its lock file")
        if len(holders) == 1
        else ("those plans", "their lock files")
    )
    action = (
        "--force deletes them permanently"
        if force
        else "--trash-references moves them to the trash"
    )
    stale = ", ".join(plan.lock_display for plan in holders)
    click.echo(
        f"WARNING: {summary}. {action} under {it}, leaving {its_locks} stale: "
        f"{stale}. To keep {it} in sync, destroy them together instead: {command}",
        err=True,
    )


@app.command(no_args_is_help=True, cls=_Command)
@click.argument(
    "targets",
    cls=_Argument,
    required=True,
    type=str,
    nargs=-1,
    callback=_as_list,
    help=_TARGETS_HELP,
)
@click.option(
    "--instance",
    "-i",
    "instance",
    required=True,
    show_default=True,
    type=str,
    help=_INSTANCE_HELP,
)
@click.option(
    "--permanent",
    "permanent",
    default=False,
    show_default=True,
    is_flag=True,
    help="Delete irreversibly instead of moving objects to the trash.",
)
@click.option(
    "--force",
    "force",
    default=False,
    show_default=True,
    is_flag=True,
    help="Permanently delete the blocking references first, then the targets. This destroys data that cannot be recovered.",
)
@click.option(
    "--trash-references",
    "trash_references",
    default=False,
    show_default=True,
    is_flag=True,
    help="Instead of refusing, move the blocking references that support it to the trash (recoverable) and trash the targets too. Link rows that cannot be trashed are left in place. Cannot be combined with --force or --permanent.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    default=False,
    show_default=True,
    is_flag=True,
    help="Skip the confirmation prompt.",
)
@click.option(
    "--config",
    "-c",
    "config",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help="Path to a lamin_schemas.yml: the destroy's run is recorded under the instance's 'track_space'/'track_prefix' there, as 'apply -c' does (falling back to the LAMIN_SCHEMA_EVOLUTION_TRACK_SPACE/LAMIN_SCHEMA_EVOLUTION_TRACK_PREFIX env vars).",
)
@click.pass_context
def destroy(
    ctx: click.Context,
    targets: list[str],
    instance: str,
    permanent: bool,
    force: bool,
    trash_references: bool,
    yes: bool,
    config: Path,
) -> None:
    """Tear down the plan(s)' objects, refusing if any are referenced.

    Pass several plan targets to tear them down together: a reference from an
    object one plan manages to an object another listed plan manages does not
    block deletion, since both are being destroyed.  Objects managed by plans
    that are only depended on (but not listed) are left intact.  A reference
    held by an object another plan in the project manages (found by its lock
    file for the instance) refuses the destroy and names the command that
    destroys both together; --trash-references and --force still go ahead,
    warning that the other plan's lock file is left stale.

    A teardown that goes ahead is recorded in the instance as a run of the
    'destroy' operation, with a report of what it destroyed attached.
    """
    from lamindb_setup.evolve.console import progress_reporter
    from lamindb_setup.evolve.engine import (
        LockFile,
        TrackConfig,
        compute_destroy_group,
        lock_path,
        managing_plans,
        merge_destroy_plans,
        order_dependencies_first,
        plan_held_references,
        print_destroy,
    )

    if force and trash_references:
        click.echo(
            "Error: --force and --trash-references cannot be combined.", err=True
        )
        raise Exit(code=2)
    if permanent and trash_references:
        click.echo(
            "Error: --permanent and --trash-references cannot be combined.", err=True
        )
        raise Exit(code=2)

    # Before the plans are imported: the config decides what they import from.
    cfg = _load_ci_config(ctx, config) if config is not None else None
    loaded = load_all_plans(targets)
    # Order dependencies-first (a plan before the plans it depends on) so the
    # merged teardown's reversed deletion removes dependents before the types
    # they rely on, even when the referenced plan is listed alongside.
    loaded = order_dependencies_first(loaded)
    plans = [plan_obj for plan_obj, _f, _m in loaded]
    instance_slug = safe_connect(instance)
    locks = [
        LockFile.read(lock_path(plan_file, instance_slug))
        for _obj, plan_file, _module in loaded
    ]
    with progress_reporter() as progress:
        destroy_plans = compute_destroy_group(
            plans, locks=locks, report=progress.report, track=progress.track
        )
    titles = [f"Destroy: {plan_obj.name}" for plan_obj in plans]
    for destroy_plan, title in zip(destroy_plans, titles, strict=False):
        print_destroy(destroy_plan, title=title)

    merged = merge_destroy_plans(
        destroy_plans, plan_name=", ".join(plan.name for plan in plans)
    )
    scope = "this plan" if len(plans) == 1 else "these plans"

    if not merged.targets:
        click.echo(f"No objects from {scope} exist in the instance. Nothing to do.")
        return

    # A blocking reference another plan's object holds is not this teardown's
    # to trash or delete: that plan's lock would go on recording it.
    if not merged.is_safe:
        held = plan_held_references(
            merged, managing_plans([path for _o, path, _m in loaded], instance_slug)
        )
        if held:
            _refuse_or_warn_plan_held_references(
                held,
                merged,
                targets=targets,
                instance=instance,
                permanent=permanent,
                force=force,
                trash_references=trash_references,
            )

    already_confirmed = False
    if not merged.is_safe and not force and not trash_references:
        click.echo(
            f"Error: {merged.total_references} object(s) outside {scope} "
            "reference objects they manage.",
            err=True,
        )
        _echo_blocking_references(merged)
        if permanent:
            # Trashing the references keeps the teardown recoverable, which a
            # permanent deletion is not, so the inline trash offer is moot.
            click.echo(
                "Nothing was destroyed. Re-run with --force to permanently "
                "delete the blocking references as well, or drop --permanent "
                "and pass --trash-references to move them to the trash "
                "(recoverable).",
                err=True,
            )
            raise Exit(code=1)
        # Offer the recoverable trash path inline so the user need not restart
        # the command with an option.
        if yes or not click.confirm(
            f"Move up to {merged.total_references} referencing record(s) to the "
            f"trash (recoverable) and destroy {len(merged.targets)} object(s) "
            "to trash?"
        ):
            click.echo(
                "Nothing was destroyed. Re-run with --trash-references to trash "
                "the references (recoverable) or --force to delete them "
                "permanently.",
                err=True,
            )
            raise Exit(code=1)
        trash_references = True
        already_confirmed = True

    if force and not merged.is_safe:
        click.echo(
            f"WARNING: --force will PERMANENTLY delete "
            f"{merged.total_references} referencing record(s). This data "
            "cannot be recovered:",
            err=True,
        )
        _echo_blocking_references(merged)
        if not yes and not click.confirm(
            f"Permanently delete {merged.total_references} reference(s) and "
            f"destroy {len(merged.targets)} object(s)?"
        ):
            click.echo("Aborted.")
            raise Exit(code=1)
    elif trash_references and not merged.is_safe:
        if not already_confirmed:
            click.echo(
                f"--trash-references will move up to {merged.total_references} "
                "referencing record(s) to the trash (recoverable); link rows "
                "that cannot be trashed are left in place:",
                err=True,
            )
            _echo_blocking_references(merged)
            if not yes and not click.confirm(
                f"Trash the references and destroy {len(merged.targets)} "
                "object(s) to trash?"
            ):
                click.echo("Aborted.")
                raise Exit(code=1)
    else:
        verb = "Permanently destroy" if permanent else "Destroy"
        if not yes and not click.confirm(f"{verb} {len(merged.targets)} object(s)?"):
            click.echo("Aborted.")
            raise Exit(code=1)

    track_config = (
        cfg.track_config_for(instance) if cfg is not None else TrackConfig.resolve()
    )
    # The switches the teardown runs under: accepting the inline offer above
    # makes it a --trash-references one.
    params = {
        "instance": instance,
        "targets": list(targets),
        "permanent": permanent,
        "force": force,
        "trash_references": trash_references,
        "yes": yes,
    }
    _execute_tracked_destroy(
        loaded,
        locks,
        destroy_plans,
        titles,
        merged,
        instance=instance,
        instance_slug=instance_slug,
        params=params,
        track_config=track_config,
    )


def _execute_tracked_destroy(
    loaded: list[Any],
    locks: list[Any],
    destroy_plans: list[Any],
    titles: list[str],
    merged: Any,
    *,
    instance: str,
    instance_slug: str,
    params: dict[str, Any],
    track_config: TrackConfig,
) -> None:
    """Tear `merged` down and prune the plans' locks, as a `destroy` run.

    The run records `params` and gets a report attached: the rendered destroy
    plan(s), the blocking references acted on, the lock files pruned and the
    outcome.  A failure in the teardown propagates once the run is closed.
    """
    from lamindb_setup.evolve.console import progress_reporter
    from lamindb_setup.evolve.engine import (
        attach_operation_report,
        execute_destroy,
        lock_path,
        render_destroy,
        track_operation,
    )

    permanent = params["permanent"]
    force = params["force"]
    trash_references = params["trash_references"]
    with track_operation("destroy", params=params, config=track_config) as run:
        # Listed before the teardown removes the records they describe.
        acted_on = [] if merged.is_safe else _blocking_reference_lines(merged)
        with progress_reporter() as progress:
            execute_destroy(
                merged,
                permanent=permanent,
                force=force,
                trash_references=trash_references,
                track=progress.track,
            )

        # Every merged target is now gone from the live instance, so prune each
        # plan's lock of the objects it just tore down (deleting the file when
        # it ends up empty).  This keeps the lock in sync so a follow-up
        # `apply` re-creates the objects instead of aborting on the
        # now-dangling uids.
        lock_lines: list[str] = []
        for (_obj, plan_file, _module), lock, destroy_plan in zip(
            loaded, locks, destroy_plans, strict=False
        ):
            if lock is None:
                continue
            lock_file = lock_path(plan_file, instance_slug)
            pruned = _prune_lock_after_destroy(
                lock, lock_file, [target.key for target in destroy_plan.targets]
            )
            if pruned:
                lock_lines.append(
                    f"Pruned {pruned} entry(ies) from {lock_file}."
                    if lock_file.exists()
                    else f"Removed now-empty lock file {lock_file}."
                )

        outcome = _destroy_outcome(
            merged, permanent=permanent, force=force, trash_references=trash_references
        )
        for line in [*lock_lines, *outcome]:
            click.echo(line)

        sections = [
            render_destroy(destroy_plan, title=title)
            for destroy_plan, title in zip(destroy_plans, titles, strict=False)
        ]
        if acted_on:
            heading = (
                "Blocking references deleted permanently:"
                if force
                else "Blocking references moved to the trash (un-trashable link "
                "rows left in place):"
            )
            sections.append("\n".join([heading, *(f"  {x}" for x in acted_on)]))
        if lock_lines:
            sections.append("\n".join(lock_lines))
        sections.append("\n".join(outcome))
        report_text = "\n\n".join(section.rstrip("\n") for section in sections)
        attach_operation_report(
            run,
            report_text + "\n",
            instance=instance,
            prefix=track_config.prefix,
            operation="destroy",
            filename="destroy-report.txt",
            folder="destroy-reports",
        )


def _destroy_outcome(
    merged: Any, *, permanent: bool, force: bool, trash_references: bool
) -> list[str]:
    """The lines reporting what a finished teardown of `merged` did."""
    lines: list[str] = []
    if force and merged.total_references:
        lines.append(
            f"Deleted {merged.total_references} referencing record(s) permanently."
        )
    elif trash_references and merged.total_references:
        lines.append(
            "Moved the trashable blocking references to the trash (recoverable); "
            "un-trashable link rows were left in place."
        )
    location = "permanently" if permanent else "to trash"
    lines.append(f"Destroyed {len(merged.targets)} object(s) ({location}).")
    return lines


@app.command("introspect", no_args_is_help=True, cls=_Command)
@click.argument(
    "name",
    cls=_Argument,
    required=True,
    type=str,
    help="Plan name; the file is written as 'plan_<name>.py'.",
)
@click.option(
    "--instance",
    "-i",
    "instance",
    required=True,
    show_default=True,
    type=str,
    help=_INSTANCE_HELP,
)
@click.option(
    "--output-dir",
    "-d",
    "output_dir",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help="Directory to write the plan file into (default: the current directory).",
)
@click.option(
    "-o",
    "legacy_output_dir",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    hidden=True,
    help="Deprecated: use -d/--output-dir.",
)
@click.option(
    "--space",
    "-s",
    "space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Restrict introspection to one or more space uids (repeat to add spaces).",
)
@click.option(
    "--exclude-space",
    "-x",
    "exclude_space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Exclude one or more space uids from introspection (repeat to add spaces); applied after --space.",
)
@click.option(
    "--overwrite",
    "overwrite",
    default=False,
    show_default=True,
    is_flag=True,
    help="Overwrite the output file if it already exists.",
)
@click.option(
    "--no-verify",
    "no_verify",
    default=False,
    show_default=True,
    is_flag=True,
    help="Skip verifying that applying the plan would make no changes.",
)
def introspect_instance_cmd(
    name: str,
    instance: str,
    output_dir: Path,
    legacy_output_dir: Path,
    space: list[str],
    exclude_space: list[str],
    overwrite: bool,
    no_verify: bool,
) -> None:
    """Generate a plan file from an existing instance.

    Reads the instance's feature types, features, schema types, named schemas,
    record types and sheets and writes 'plan_<name>.py' (into --output-dir,
    the current directory by default) that adopts them unchanged. By default it
    verifies that applying the plan would make no changes; objects it cannot
    reconstruct exactly are reported and cause a non-zero exit.
    """
    from lamindb_setup.evolve.console import progress_reporter
    from lamindb_setup.evolve.introspect import introspect_instance, plan_slug

    output_dir = (
        _fold_deprecated_alias(
            output_dir,
            legacy_output_dir,
            alias="introspect -o",
            option="-d/--output-dir",
        )
        or Path()
    )
    output = output_dir / f"plan_{plan_slug(name)}.py"

    try:
        with progress_reporter() as progress:
            result = introspect_instance(
                instance,
                output,
                space_uids=space,
                exclude_space_uids=exclude_space,
                overwrite=overwrite,
                verify=not no_verify,
                reporter=progress.report,
                read_tracker=progress.track,
            )
    except FileExistsError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise Exit(code=1) from exc

    click.echo(f"Wrote plan: {result.output}")
    click.echo(
        f"  reconstructed {result.reconstructed} object(s); "
        f"plan function {result.func!r}, PLAN_TRANSFORM_STEM_UID = "
        f"{result.stem_uid!r}"
    )
    if result.skipped_unnamed_schemas:
        click.echo(
            f"  skipped {result.skipped_unnamed_schemas} unnamed schema(s) "
            "(not declaratively manageable)."
        )

    for error in result.errors:
        click.echo(f"  could not reconstruct: {error}", err=True)

    for warning in result.warnings:
        click.echo(f"  warning: {warning}", err=True)

    if result.verification is not None:
        if result.verification_failed:
            click.echo(
                f"  verification could not run: {result.verification['error']}",
                err=True,
            )
        elif result.actionable_changes:
            click.echo(
                f"  verification found {result.actionable_changes} change(s) "
                "(the plan does not round-trip):",
                err=True,
            )
            for change in result.verification.get("changes", []):
                click.echo(f"    {change['action']}: {change['object']}", err=True)

    if not result.ok:
        click.echo(
            "Plan written, but it does not faithfully reproduce the instance; "
            "see the messages above.",
            err=True,
        )
        raise Exit(code=1)

    click.echo(
        f"  apply with: lamin evolve apply {result.output}:{result.func} -i {instance}"
    )


# === repair subcommands =======================================================

repair_app = Group(
    name="repair",
    no_args_is_help=True,
    help=(
        "Repair an instance: dangling objects, duplicates, broken dtypes, "
        "stranded values, sheet schemas and misaligned uids."
    ),
)
app.add_command(repair_app, name="repair")

_DRY_RUN_HELP = (
    "Print the report and stop: never prompt, change nothing and record no run. "
    "Exits 0 even when a real run would refuse. Cannot be combined with --yes."
)
_REPAIR_CONFIG_HELP = (
    "Path to a lamin_schemas.yml: the repair's run is recorded under the "
    "instance's 'track_space'/'track_prefix' there, as 'apply -c' does (falling "
    "back to the LAMIN_SCHEMA_EVOLUTION_TRACK_SPACE/"
    "LAMIN_SCHEMA_EVOLUTION_TRACK_PREFIX env vars)."
)
_DRY_RUN_DONE = "Dry run: nothing was changed."


def _start_repair(
    ctx: click.Context, config: Path | None, *, yes: bool, dry_run: bool
) -> CIConfig | None:
    """Check a repair's switches and load its `-c` configuration, if given.

    Runs before the instance is connected, so a contradictory command line or an
    unreadable configuration fails without touching it.
    """
    if dry_run and yes:
        raise click.BadParameter(
            "cannot be combined with --yes (a dry run never asks).",
            param_hint="'--dry-run'",
        )
    return _load_ci_config(ctx, config) if config is not None else None


def _repair_track_config(cfg: CIConfig | None, instance: str) -> TrackConfig:
    """Return the :class:`TrackConfig` a repair of `instance` runs under."""
    from lamindb_setup.evolve.engine import TrackConfig

    return cfg.track_config_for(instance) if cfg is not None else TrackConfig.resolve()


def _confirm_repair(prompt: str, *, yes: bool, dry_run: bool) -> bool:
    """Return whether a repair goes ahead, once its report has been printed.

    A dry run stops here and says so.  Otherwise `prompt` is asked unless
    `yes`, and declining it exits 1.
    """
    if dry_run:
        click.echo(_DRY_RUN_DONE)
        return False
    if not yes and not click.confirm(prompt):
        click.echo("Aborted.")
        raise Exit(code=1)
    return True


def _refuse_repair(message: str, *, dry_run: bool) -> NoReturn:
    """Stop a repair whose report leaves nothing it may change on its own.

    `message` says why, on stderr.  A real run exits 1; a dry run, whose report
    was the point, ends like any other dry run, with exit code 0.
    """
    click.echo(message, err=True)
    if dry_run:
        click.echo(_DRY_RUN_DONE)
        raise Exit(code=0)
    raise Exit(code=1)


@repair_app.command("dangling", no_args_is_help=True, cls=_Command)
@click.option(
    "--instance",
    "-i",
    "instance",
    required=True,
    show_default=True,
    type=str,
    help=_INSTANCE_HELP,
)
@click.option(
    "--permanent",
    "permanent",
    default=False,
    show_default=True,
    is_flag=True,
    help="Delete irreversibly instead of moving objects to the trash.",
)
@click.option(
    "--space",
    "-s",
    "space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Restrict pruning to one or more space uids (repeat to add spaces).",
)
@click.option(
    "--exclude-space",
    "-x",
    "exclude_space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Exclude one or more space uids from pruning (repeat to add spaces); applied after --space.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    default=False,
    show_default=True,
    is_flag=True,
    help="Skip the confirmation prompt.",
)
@click.option(
    "--dry-run",
    "dry_run",
    default=False,
    show_default=True,
    is_flag=True,
    help=_DRY_RUN_HELP,
)
@click.option(
    "--config",
    "-c",
    "config",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help=_REPAIR_CONFIG_HELP,
)
@click.pass_context
def repair_dangling(
    ctx: click.Context,
    instance: str,
    permanent: bool,
    space: list[str],
    exclude_space: list[str],
    yes: bool,
    dry_run: bool,
    config: Path,
) -> None:
    """Trash live objects that still reference deleted (trashed) objects.

    When an object is moved to the trash, the features, schemas and records that
    referenced it are left pointing at a deleted target. This finds every such
    dangling object — via its type, a schema's members, index or slots, or a
    record's schema — and trashes it too, cascading until nothing live references
    a gone object. A target counts as gone when it was trashed or when it no
    longer exists anywhere in the instance (permanently deleted); a target that
    is still live but merely outside the prune scope is left alone. It reports
    what would be removed and asks for confirmation before deleting anything.
    """
    from lamindb_setup.evolve.console import progress_reporter
    from lamindb_setup.evolve.engine import track_operation
    from lamindb_setup.evolve.introspect import read_instance
    from lamindb_setup.evolve.maintenance import (
        compute_prune,
        execute_prune,
        print_prune,
        render_prune,
    )

    cfg = _start_repair(ctx, config, yes=yes, dry_run=dry_run)
    safe_connect(instance)

    with progress_reporter() as progress:
        contents = read_instance(
            space_uids=space,
            exclude_space_uids=exclude_space,
            track=progress.track,
        )

    prune_plan = compute_prune(contents)
    title = f"Prune dangling objects in {instance}"
    print_prune(prune_plan, title=title)

    if prune_plan.is_empty:
        click.echo("No live object references a deleted object. Nothing to do.")
        return

    verb = "Permanently delete" if permanent else "Trash"
    if not _confirm_repair(
        f"{verb} {len(prune_plan.objects)} dangling object(s)?",
        yes=yes,
        dry_run=dry_run,
    ):
        return

    location = "permanently" if permanent else "to trash"
    params = {
        "instance": instance,
        "permanent": permanent,
        "yes": yes,
        "space": list(space) if space else [],
        "exclude_space": list(exclude_space) if exclude_space else [],
    }
    track_config = _repair_track_config(cfg, instance)
    with track_operation("repair-dangling", params=params, config=track_config) as run:
        deleted = execute_prune(prune_plan, permanent=permanent)
        report_text = render_prune(prune_plan, title=title)
        report_text += f"\nPruned {deleted} object(s) ({location}).\n"
        _attach_prune_report(
            run, report_text, instance=instance, prefix=track_config.prefix
        )

    click.echo(f"Pruned {deleted} object(s) ({location}).")


def _attach_prune_report(
    run: Any, report_text: str, *, instance: str, prefix: str
) -> None:
    """Save the prune `report_text` as an artifact and attach it to `run`."""
    from lamindb_setup.evolve.engine import attach_operation_report

    attach_operation_report(
        run,
        report_text,
        instance=instance,
        prefix=prefix,
        operation="repair-dangling",
        filename="repair-dangling-report.txt",
        folder="repair-dangling-reports",
    )


@app.command("prune-dangling", no_args_is_help=True, deprecated=True, cls=_Command)
@click.option(
    "--instance",
    "-i",
    "instance",
    required=True,
    show_default=True,
    type=str,
    help=_INSTANCE_HELP,
)
@click.option(
    "--permanent",
    "permanent",
    default=False,
    show_default=True,
    is_flag=True,
    help="Delete irreversibly instead of moving objects to the trash.",
)
@click.option(
    "--space",
    "-s",
    "space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Restrict pruning to one or more space uids (repeat to add spaces).",
)
@click.option(
    "--exclude-space",
    "-x",
    "exclude_space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Exclude one or more space uids from pruning (repeat to add spaces); applied after --space.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    default=False,
    show_default=True,
    is_flag=True,
    help="Skip the confirmation prompt.",
)
@click.option(
    "--dry-run",
    "dry_run",
    default=False,
    show_default=True,
    is_flag=True,
    help=_DRY_RUN_HELP,
)
@click.option(
    "--config",
    "-c",
    "config",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help=_REPAIR_CONFIG_HELP,
)
@click.pass_context
def prune_dangling_cmd(
    ctx: click.Context,
    instance: str,
    permanent: bool,
    space: list[str],
    exclude_space: list[str],
    yes: bool,
    dry_run: bool,
    config: Path,
) -> None:
    """Deprecated alias for 'repair dangling'."""
    _warn_deprecated("prune-dangling", "repair dangling")
    ctx.invoke(
        repair_dangling,
        instance=instance,
        permanent=permanent,
        space=space,
        exclude_space=exclude_space,
        yes=yes,
        dry_run=dry_run,
        config=config,
    )


@repair_app.command("duplicates", no_args_is_help=True, cls=_Command)
@click.option(
    "--instance",
    "-i",
    "instance",
    required=True,
    show_default=True,
    type=str,
    help=_INSTANCE_HELP,
)
@click.option(
    "--permanent",
    "permanent",
    default=False,
    show_default=True,
    is_flag=True,
    help="Delete irreversibly instead of moving objects to the trash.",
)
@click.option(
    "--space",
    "-s",
    "space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Restrict the scan to one or more space uids (repeat to add spaces).",
)
@click.option(
    "--exclude-space",
    "-x",
    "exclude_space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Exclude one or more space uids from the scan (repeat to add spaces); applied after --space.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    default=False,
    show_default=True,
    is_flag=True,
    help="Skip the confirmation prompt.",
)
@click.option(
    "--dry-run",
    "dry_run",
    default=False,
    show_default=True,
    is_flag=True,
    help=_DRY_RUN_HELP,
)
@click.option(
    "--config",
    "-c",
    "config",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help=_REPAIR_CONFIG_HELP,
)
@click.pass_context
def repair_duplicates(
    ctx: click.Context,
    instance: str,
    permanent: bool,
    space: list[str],
    exclude_space: list[str],
    yes: bool,
    dry_run: bool,
    config: Path,
) -> None:
    """Trash duplicate features and schemas, keeping the most recent of each.

    Two features (or two schemas) that share the same type parent — or that both
    have no parent — and the same name are duplicates, which LaminDB's (name,
    type) natural key forbids. This groups every live feature and schema by kind,
    parent and name, and for each group with more than one member keeps the most
    recently created object and trashes the older ones. Only the Feature and
    Schema registries are considered; records are left untouched. It reports what
    would be removed and asks for confirmation before deleting anything.

    Live schemas sharing one content hash are duplicates too: LaminDB keeps one
    schema per content, but an instance written before LaminDB 2.12 can hold
    two. An inferred schema nothing else references is absorbed into the schema
    sharing its content, as 'apply' absorbs one: its artifact links are moved
    over and it is deleted. Any other such group is listed for a decision by
    hand. With -c, the schemas the instance's lock files under the project
    record are known to be plan-managed: they are never absorbed.
    """
    from lamindb_setup.evolve.console import progress_reporter
    from lamindb_setup.evolve.engine import track_operation
    from lamindb_setup.evolve.introspect import read_instance
    from lamindb_setup.evolve.maintenance import (
        compute_content_duplicates,
        compute_duplicates,
        execute_dedup,
        print_dedup,
        render_dedup,
    )

    cfg = _start_repair(ctx, config, yes=yes, dry_run=dry_run)
    slug = safe_connect(instance)

    with progress_reporter() as progress:
        contents = read_instance(
            space_uids=space,
            exclude_space_uids=exclude_space,
            track=progress.track,
        )
        plan = compute_duplicates(contents)
        progress.report("Looking for schemas sharing their content")
        plan.content_groups = compute_content_duplicates(
            space_uids=space,
            exclude_space_uids=exclude_space,
            managed_uids=_locked_schema_uids(cfg, slug),
            skip_uids={
                member.uid
                for group in plan.groups
                if group.registry == "Schema"
                for member in group.remove
            },
        )

    title = f"Remove duplicate features and schemas in {instance}"
    print_dedup(plan, title=title)

    if plan.is_empty:
        click.echo("No duplicate features or schemas found. Nothing to do.")
        return

    manual = len(plan.manual_groups)
    if not plan.has_changes:
        _refuse_repair(
            f"{manual} group(s) of schemas sharing their content need a decision "
            "by hand; nothing can be repaired automatically.",
            dry_run=dry_run,
        )
    if manual:
        click.echo(
            f"Note: {manual} group(s) of schemas sharing their content need a "
            "decision by hand and are left as they are.",
            err=True,
        )

    verb = "permanently delete" if permanent else "trash"
    actions = []
    if plan.remove_count:
        actions.append(f"{verb} {plan.remove_count} duplicate object(s)")
    if plan.absorb_count:
        actions.append(
            f"absorb {plan.absorb_count} inferred schema(s) into the schema "
            "sharing their content"
        )
    prompt = " and ".join(actions)
    if not _confirm_repair(
        prompt[0].upper() + prompt[1:] + "?", yes=yes, dry_run=dry_run
    ):
        return

    location = "permanently" if permanent else "to trash"
    params = {
        "instance": instance,
        "permanent": permanent,
        "yes": yes,
        "space": list(space) if space else [],
        "exclude_space": list(exclude_space) if exclude_space else [],
    }
    track_config = _repair_track_config(cfg, instance)
    with track_operation(
        "repair-duplicates", params=params, config=track_config
    ) as run:
        deleted = execute_dedup(plan, permanent=permanent)
        outcome = []
        if plan.remove_count:
            outcome.append(f"Removed {deleted} duplicate object(s) ({location}).")
        if plan.absorb_count:
            outcome.append(
                f"Absorbed {plan.absorb_count} inferred schema(s) into the schema "
                "sharing their content."
            )
        report_text = render_dedup(plan, title=title)
        report_text += "\n" + "\n".join(outcome) + "\n"
        _attach_dedup_report(
            run, report_text, instance=instance, prefix=track_config.prefix
        )

    click.echo("\n".join(outcome))


def _locked_schema_uids(cfg: CIConfig | None, slug: str) -> set[str]:
    """The schemas the lock files of instance `slug` record, under `cfg`'s root.

    Without a configuration there is no telling where the plans live, and no
    lock is read.
    """
    from lamindb_setup.evolve.engine import LockFile, instance_lock_files

    if cfg is None:
        return set()
    locks = (
        LockFile.read(path) for path in instance_lock_files([cfg.import_root], slug)
    )
    return {
        str(entry.uid)
        for lock in locks
        if lock is not None
        for entry in lock.objects.values()
        if entry.uid is not None and entry.kind in ("Schema", "SchemaType")
    }


def _attach_dedup_report(
    run: Any, report_text: str, *, instance: str, prefix: str
) -> None:
    """Save the dedup `report_text` as an artifact and attach it to `run`."""
    from lamindb_setup.evolve.engine import attach_operation_report

    attach_operation_report(
        run,
        report_text,
        instance=instance,
        prefix=prefix,
        operation="repair-duplicates",
        filename="repair-duplicates-report.txt",
        folder="repair-duplicates-reports",
    )


@app.command("remove-duplicates", no_args_is_help=True, deprecated=True, cls=_Command)
@click.option(
    "--instance",
    "-i",
    "instance",
    required=True,
    show_default=True,
    type=str,
    help=_INSTANCE_HELP,
)
@click.option(
    "--permanent",
    "permanent",
    default=False,
    show_default=True,
    is_flag=True,
    help="Delete irreversibly instead of moving objects to the trash.",
)
@click.option(
    "--space",
    "-s",
    "space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Restrict the scan to one or more space uids (repeat to add spaces).",
)
@click.option(
    "--exclude-space",
    "-x",
    "exclude_space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Exclude one or more space uids from the scan (repeat to add spaces); applied after --space.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    default=False,
    show_default=True,
    is_flag=True,
    help="Skip the confirmation prompt.",
)
@click.option(
    "--dry-run",
    "dry_run",
    default=False,
    show_default=True,
    is_flag=True,
    help=_DRY_RUN_HELP,
)
@click.option(
    "--config",
    "-c",
    "config",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help=_REPAIR_CONFIG_HELP,
)
@click.pass_context
def remove_duplicates_cmd(
    ctx: click.Context,
    instance: str,
    permanent: bool,
    space: list[str],
    exclude_space: list[str],
    yes: bool,
    dry_run: bool,
    config: Path,
) -> None:
    """Deprecated alias for 'repair duplicates'."""
    _warn_deprecated("remove-duplicates", "repair duplicates")
    ctx.invoke(
        repair_duplicates,
        instance=instance,
        permanent=permanent,
        space=space,
        exclude_space=exclude_space,
        yes=yes,
        dry_run=dry_run,
        config=config,
    )


@repair_app.command("dtypes", no_args_is_help=True, cls=_Command)
@click.option(
    "--instance",
    "-i",
    "instance",
    required=True,
    show_default=True,
    type=str,
    help=_INSTANCE_HELP,
)
@click.option(
    "--space",
    "-s",
    "space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Restrict verification to one or more space uids (repeat to add spaces).",
)
@click.option(
    "--exclude-space",
    "-x",
    "exclude_space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Exclude one or more space uids from verification (repeat to add spaces); applied after --space.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    default=False,
    show_default=True,
    is_flag=True,
    help="Skip the confirmation prompt.",
)
@click.option(
    "--dry-run",
    "dry_run",
    default=False,
    show_default=True,
    is_flag=True,
    help=_DRY_RUN_HELP,
)
@click.option(
    "--config",
    "-c",
    "config",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help=_REPAIR_CONFIG_HELP,
)
@click.pass_context
def repair_dtypes(
    ctx: click.Context,
    instance: str,
    space: list[str],
    exclude_space: list[str],
    yes: bool,
    dry_run: bool,
    config: Path,
) -> None:
    """Repair features whose stored dtype string is broken.

    Finds features with a bare 'cat' dtype (rewritten to 'cat[ULabel]') or a
    'cat[Record[<name>]]' dtype that addresses its record type by name instead
    of uid (rewritten to 'cat[Record[<uid>]]'). A 'cat[Record[<name>]]' whose
    name cannot be resolved to a record type is flagged for soft deletion; an
    ambiguous name requires manual resolution and blocks automatic changes. It
    reports each broken feature — with its record type, creator and space — and
    asks for confirmation before changing anything.
    """
    from lamindb_setup.evolve.console import progress_reporter
    from lamindb_setup.evolve.engine import attach_operation_report, track_operation
    from lamindb_setup.evolve.introspect import read_instance
    from lamindb_setup.evolve.maintenance import (
        compute_verify_dtypes,
        execute_verify_dtypes,
        print_verify_dtypes,
        render_verify_dtypes,
    )

    cfg = _start_repair(ctx, config, yes=yes, dry_run=dry_run)
    safe_connect(instance)

    with progress_reporter() as progress:
        contents = read_instance(
            space_uids=space,
            exclude_space_uids=exclude_space,
            track=progress.track,
        )

    plan = compute_verify_dtypes(contents)
    title = f"Verify dtypes in {instance}"
    print_verify_dtypes(plan, title=title)

    if plan.is_empty:
        click.echo("No broken dtype found. Nothing to do.")
        return

    if plan.manual:
        _refuse_repair(
            f"{len(plan.manual)} dtype(s) require manual resolution; "
            "no changes were made.",
            dry_run=dry_run,
        )

    if not _confirm_repair(
        f"Fix {len(plan.fixable)} dtype(s) and soft delete "
        f"{len(plan.deletable)} feature(s)?",
        yes=yes,
        dry_run=dry_run,
    ):
        return

    params = {
        "instance": instance,
        "yes": yes,
        "space": list(space) if space else [],
        "exclude_space": list(exclude_space) if exclude_space else [],
    }
    track_config = _repair_track_config(cfg, instance)
    with track_operation("repair-dtypes", params=params, config=track_config) as run:
        fixed, deleted = execute_verify_dtypes(plan)
        report_text = render_verify_dtypes(plan, title=title)
        report_text += f"\nFixed {fixed} dtype(s); soft deleted {deleted} feature(s).\n"
        attach_operation_report(
            run,
            report_text,
            instance=instance,
            prefix=track_config.prefix,
            operation="repair-dtypes",
            filename="repair-dtypes-report.txt",
            folder="repair-dtypes-reports",
        )

    click.echo(f"Fixed {fixed} dtype(s); soft deleted {deleted} feature(s).")


@repair_app.command("stranded-values", no_args_is_help=True, cls=_Command)
@click.option(
    "--instance",
    "-i",
    "instance",
    required=True,
    show_default=True,
    type=str,
    help=_INSTANCE_HELP,
)
@click.option(
    "--space",
    "-s",
    "space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Restrict the scan to one or more space uids (repeat to add spaces).",
)
@click.option(
    "--exclude-space",
    "-x",
    "exclude_space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Exclude one or more space uids from the scan (repeat to add spaces); applied after --space.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    default=False,
    show_default=True,
    is_flag=True,
    help="Skip the confirmation prompt.",
)
@click.option(
    "--dry-run",
    "dry_run",
    default=False,
    show_default=True,
    is_flag=True,
    help=_DRY_RUN_HELP,
)
@click.option(
    "--config",
    "-c",
    "config",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help=_REPAIR_CONFIG_HELP,
)
@click.pass_context
def repair_stranded_values(
    ctx: click.Context,
    instance: str,
    space: list[str],
    exclude_space: list[str],
    yes: bool,
    dry_run: bool,
    config: Path,
) -> None:
    """Delete values left behind by a feature that was deleted.

    Deleting a feature moves it to the trash, which detaches nothing: its value
    rows stay keyed to it, unreachable through any live feature yet still
    reported by 'get_values()' on the rows they annotate. This finds those
    features and offers to delete the values they still own.

    Only a value whose feature is gone is reported. A row carrying a value for
    a feature its schema does not declare is legal under 'minimal_set', so it
    is not an error and is left alone. A deletion declared with
    'keep_values()' is an accepted retention: it is listed for visibility and
    never repaired.
    """
    from lamindb_setup.evolve.engine import attach_operation_report, track_operation
    from lamindb_setup.evolve.maintenance import (
        compute_stranded_values,
        execute_stranded_values,
        print_stranded_values,
        render_stranded_values,
    )

    cfg = _start_repair(ctx, config, yes=yes, dry_run=dry_run)
    safe_connect(instance)

    plan = compute_stranded_values(
        space_uids=list(space) if space else None,
        exclude_space_uids=list(exclude_space) if exclude_space else None,
    )
    title = f"Stranded values in {instance}"
    print_stranded_values(plan, title=title)

    if plan.is_empty:
        click.echo("No stranded values to repair. Nothing to do.")
        return

    if not _confirm_repair(
        f"Delete the values owned by {len(plan.features)} deleted feature(s)?",
        yes=yes,
        dry_run=dry_run,
    ):
        return

    params = {
        "instance": instance,
        "yes": yes,
        "space": list(space) if space else [],
        "exclude_space": list(exclude_space) if exclude_space else [],
    }
    track_config = _repair_track_config(cfg, instance)
    with track_operation(
        "repair-stranded-values", params=params, config=track_config
    ) as run:
        removed = execute_stranded_values(plan)
        report_text = render_stranded_values(plan, title=title)
        report_text += f"\nDeleted {removed} stranded value row(s).\n"
        attach_operation_report(
            run,
            report_text,
            instance=instance,
            prefix=track_config.prefix,
            operation="repair-stranded-values",
            filename="repair-stranded-values-report.txt",
            folder="repair-stranded-values-reports",
        )

    click.echo(f"Deleted {removed} stranded value row(s).")


@repair_app.command("sheet-schemas", no_args_is_help=True, cls=_Command)
@click.option(
    "--instance",
    "-i",
    "instance",
    required=True,
    show_default=True,
    type=str,
    help=_INSTANCE_HELP,
)
@click.option(
    "--space",
    "-s",
    "space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Restrict the scan to one or more space uids (repeat to add spaces).",
)
@click.option(
    "--exclude-space",
    "-x",
    "exclude_space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Exclude one or more space uids from the scan (repeat to add spaces); applied after --space.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    default=False,
    show_default=True,
    is_flag=True,
    help="Skip the confirmation prompt.",
)
@click.option(
    "--dry-run",
    "dry_run",
    default=False,
    show_default=True,
    is_flag=True,
    help=_DRY_RUN_HELP,
)
@click.option(
    "--config",
    "-c",
    "config",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help=_REPAIR_CONFIG_HELP,
)
@click.pass_context
def repair_sheet_schemas(
    ctx: click.Context,
    instance: str,
    space: list[str],
    exclude_space: list[str],
    yes: bool,
    dry_run: bool,
    config: Path,
) -> None:
    """Detach record types whose schema is a schema type.

    lamindb's 'Record.schema' foreign key accepts a schema with
    'is_type=True', so an instance can hold record types pointed at a schema
    type. A schema type declares no features, so such a sheet enforces nothing,
    and 'introspect' cannot express it — it excludes the record type, leaving
    the instance only partly manageable.

    An empty schema type (no members, no slots, no 'itype') enforces nothing,
    so the record type's schema is cleared and it becomes a plain record type. A
    schema type that carries members is a schema mislabeled as a type: it is
    reported but never changed, because clearing that flag rehashes the schema
    and orphans whatever types under it.
    """
    from lamindb_setup.evolve.console import progress_reporter
    from lamindb_setup.evolve.engine import attach_operation_report, track_operation
    from lamindb_setup.evolve.introspect import read_instance
    from lamindb_setup.evolve.maintenance import (
        compute_sheet_schemas,
        execute_sheet_schemas,
        print_sheet_schemas,
        render_sheet_schemas,
    )

    cfg = _start_repair(ctx, config, yes=yes, dry_run=dry_run)
    safe_connect(instance)

    with progress_reporter() as progress:
        contents = read_instance(
            space_uids=space,
            exclude_space_uids=exclude_space,
            track=progress.track,
        )

    plan = compute_sheet_schemas(contents)
    title = f"Sheet schemas in {instance}"
    print_sheet_schemas(plan, title=title)

    if plan.is_empty:
        click.echo("No record type is pointed at a schema type. Nothing to do.")
        return

    if not plan.fixable:
        _refuse_repair(
            f"{len(plan.unfixable)} record type(s) need a manual decision; "
            "nothing can be repaired automatically.",
            dry_run=dry_run,
        )

    if not _confirm_repair(
        f"Detach {len(plan.fixable)} record type(s) from their schema type?",
        yes=yes,
        dry_run=dry_run,
    ):
        return

    params = {
        "instance": instance,
        "yes": yes,
        "space": list(space) if space else [],
        "exclude_space": list(exclude_space) if exclude_space else [],
    }
    track_config = _repair_track_config(cfg, instance)
    with track_operation(
        "repair-sheet-schemas", params=params, config=track_config
    ) as run:
        detached = execute_sheet_schemas(plan)
        report_text = render_sheet_schemas(plan, title=title)
        report_text += f"\nDetached {detached} record type(s).\n"
        attach_operation_report(
            run,
            report_text,
            instance=instance,
            prefix=track_config.prefix,
            operation="repair-sheet-schemas",
            filename="repair-sheet-schemas-report.txt",
            folder="repair-sheet-schemas-reports",
        )

    click.echo(f"Detached {detached} record type(s).")


def _align_uids_selection(
    targets: list[str] | None,
    source: str,
    instances: list[str] | None,
    cfg: Any,
) -> tuple[list[tuple[Any, Path]], dict[str, list[tuple[Any, Path]]]]:
    """Resolve which plans to align on which target instances.

    Returns every plan involved (the source's identity is read for all of them)
    and, per target instance, the plans it shares with the source.  Plans are
    loaded here, before any instance is connected, because a plan can only be
    constructed before connecting to an instance.
    """
    from lamindb_setup.evolve.engine import load_plan

    if targets:
        loaded = [(plan, path) for plan, path, _m in load_all_plans(targets)]
        if instances:
            per_instance = {slug: list(loaded) for slug in instances}
        elif cfg is not None:
            files = {path.resolve() for _plan, path in loaded}
            per_instance = {
                inst.slug: [
                    (plan, path)
                    for plan, path in loaded
                    if path.resolve() in {ref.path.resolve() for ref in inst.plans}
                ]
                for inst in cfg.instances
                if inst.slug != source
                and any(ref.path.resolve() in files for ref in inst.plans)
            }
        else:
            raise click.BadParameter(
                "name the target instance(s) with -i, or pass -c to align every "
                "configured instance that shares these plans with the source."
            )
        return loaded, per_instance

    if cfg is None:
        raise click.BadParameter(
            "pass plan targets, or -c to align the plans configured for the source."
        )
    configured = {inst.slug: inst for inst in cfg.instances}
    if source not in configured:
        raise click.BadParameter(
            f"--source {source!r} is not an instance in {cfg.config_path}."
        )
    source_targets = {ref.target for ref in configured[source].plans}
    chosen = instances or [slug for slug in configured if slug != source]
    loaded_by_target: dict[str, tuple[Any, Path]] = {}
    per_instance: dict[str, list[tuple[Any, Path]]] = {}  # type: ignore[no-redef]
    for slug in chosen:
        if slug not in configured:
            raise click.BadParameter(f"instance {slug!r} is not in {cfg.config_path}.")
        shared = [ref for ref in configured[slug].plans if ref.target in source_targets]
        if not shared:
            continue
        for ref in shared:
            if ref.target not in loaded_by_target:
                plan, path, _module = load_plan(ref.load_target)
                loaded_by_target[ref.target] = (plan, path)
        per_instance[slug] = [loaded_by_target[ref.target] for ref in shared]
    return list(loaded_by_target.values()), per_instance


@repair_app.command("align-uids", no_args_is_help=True, cls=_Command)
@click.argument(
    "targets",
    cls=_Argument,
    required=False,
    type=str,
    nargs=-1,
    callback=_as_optional_list,
    help="Plans to align, as 'path/to/plan.py[:plan_variable]'. Their prerequisite plans are always included. Omit with -c to align every plan the source shares with each target.",
)
@click.option(
    "--source",
    "source",
    required=True,
    show_default=True,
    type=str,
    help="Instance whose uids win, as 'account/instance'. Its lock files say which uids those are, and are checked against it before anything changes. It is only read.",
)
@click.option(
    "--instance",
    "-i",
    "instance",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Target instance to rewrite (repeat for several). With -c and no -i, every configured instance sharing plans with the source.",
)
@click.option(
    "--config",
    "-c",
    "config",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help="lamin_schemas.yml to select targets and plans from, and to read each target's 'track_space' from.",
)
@click.option(
    "--merge-duplicates",
    "merge_duplicates",
    default=False,
    show_default=True,
    is_flag=True,
    help="When a live record already holds the uid an object needs and is the same object (same kind, name, parent and shape -- e.g. a stub 'lamin io sync' created), merge it into the plan's object: move everything referencing it over, then delete it permanently. Likewise, when a schema no plan manages already holds the content of a plan's schema and is not an inferred schema (which is absorbed anyway), merge it into the plan's schema: move everything referencing it over, then move it to the trash.",
)
@click.option(
    "--reassign-colliding",
    "reassign_colliding",
    default=False,
    show_default=True,
    is_flag=True,
    help="When a live record that cannot be merged holds the uid an object needs, give it a fresh uid instead; its references follow it.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    default=False,
    show_default=True,
    is_flag=True,
    help="Skip the per-instance confirmation prompt.",
)
@click.option(
    "--dry-run",
    "dry_run",
    default=False,
    show_default=True,
    is_flag=True,
    help="Stop after each target's rolled-back preview: never prompt, change no instance or lock file and record no run. Lock files that are not committed are only noted. Exits 0 even when a real run would refuse. Cannot be combined with --yes.",
)
@click.pass_context
def repair_align_uids(
    ctx: click.Context,
    targets: list[str],
    source: str,
    instance: list[str],
    config: Path,
    merge_duplicates: bool,
    reassign_colliding: bool,
    yes: bool,
    dry_run: bool,
) -> None:
    """Rewrite plan-managed uids on other instances to the source instance's.

    Every object the plans declare -- feature types, features, schema types,
    schemas, record types, sheets and seeded records -- is looked up on each
    target and given the uid the source records for it. References held as uid
    strings (dtypes such as 'cat[Record[<uid>]]', schema optionals and index)
    are rewritten instance-wide, schema hashes recomputed, and the target's lock
    files rewritten to match. Each target changes in one transaction, previewed
    by a rolled-back dry run first.

    A uid already held on a target by a trashed record is freed by giving that
    record a fresh uid. A live holder is refused unless --merge-duplicates or
    --reassign-colliding says what to do with it.

    lamindb keeps one live schema per content. A schema the rehash would leave
    on the content of another -- typically because the two already shared it --
    is settled the way 'apply' settles one: an inferred schema nothing else
    references is absorbed into the other; any other schema no plan manages is
    merged into the plan's with --merge-duplicates; anything else refuses the
    target, with the reasons.

    The lock files involved must be committed, so the rewrite is its own diff.
    With --dry-run, which rewrites none, a lock file that is not committed is
    only noted.
    """
    from lamindb_setup.evolve.console import progress_reporter, report_stage
    from lamindb_setup.evolve.engine import (
        attach_operation_report,
        lock_path,
        project_root,
        track_operation,
    )
    from lamindb_setup.evolve.maintenance import (
        collect_instance_locks,
        compute_alignment,
        execute_alignment,
        print_alignment,
        read_source_identity,
        render_alignment,
        rewrite_target_locks,
        with_prerequisites,
    )

    cfg = _start_repair(ctx, config, yes=yes, dry_run=dry_run)
    loaded, per_instance = _align_uids_selection(
        targets or None, source, instance or None, cfg
    )
    per_instance = {
        slug: plans for slug, plans in per_instance.items() if slug != source
    }
    if not per_instance:
        click.echo("No target instance shares these plans with the source.")
        return
    everything = with_prerequisites(loaded)

    def _reset() -> None:
        # Connecting rebuilds lamindb's classes, so plans finalized against the
        # previous instance are re-finalized against the new one.
        for plan, _path in everything:
            plan.reset()

    report_stage(f"Connecting to source {source}")
    source_slug = safe_connect(source)
    _reset()
    _ensure_locks_committed(
        [lock_path(path, source_slug) for _plan, path in everything], dry_run=dry_run
    )
    report_stage(f"Reading the uids {source_slug} holds for {len(everything)} plan(s)")
    identity = read_source_identity(everything, source_slug)
    if identity.missing_locks:
        click.echo(
            f"Note: {source_slug} has no lock file for "
            + ", ".join(repr(name) for name in identity.missing_locks)
            + "; their objects are left as they are.",
            err=True,
        )

    failed: list[str] = []
    for target, plans in per_instance.items():
        report_stage(f"Connecting to target {target}")
        target_slug = safe_connect(target)
        _reset()
        target_plans = with_prerequisites(plans)
        with progress_reporter() as progress:
            alignment = compute_alignment(
                target_plans,
                identity,
                instance=target,
                slug=target_slug,
                merge_duplicates=merge_duplicates,
                reassign_colliding=reassign_colliding,
                report=progress.report,
                track=progress.track,
            )
        # Plans built on the aligned ones record their uids too; their locks
        # are rewritten with the rest, so they have to be committed as well.
        collect_instance_locks(
            alignment, {project_root(path) for _plan, path in target_plans}
        )
        _ensure_locks_committed(alignment.lock_files, dry_run=dry_run)
        if alignment.is_empty:
            print_alignment([alignment], title=f"Align uids: {target}")
            click.echo(f"{target}: every uid already matches {source_slug}.")
            continue
        with progress_reporter() as progress:
            progress.report(f"Previewing the rewrite on {target} (rolled back)")
            execute_alignment(
                alignment, dry_run=True, report=progress.report, track=progress.track
            )
        print_alignment([alignment], title=f"Align uids (preview): {target}")
        if not alignment.ok and dry_run:
            click.echo(f"Note: a real run would refuse {target}.", err=True)
            continue
        if not alignment.ok:
            click.echo(f"Error: {target} was not changed.", err=True)
            failed.append(target)
            continue
        if dry_run:
            continue
        if not yes and not click.confirm(
            f"Rewrite {len(alignment.renames)} uid(s) on {target}?"
        ):
            click.echo(f"Skipped {target}.")
            continue

        params = {
            "source": source,
            "instance": target,
            "plans": alignment.plans,
            "merge_duplicates": merge_duplicates,
            "reassign_colliding": reassign_colliding,
        }
        track_config = _repair_track_config(cfg, target)
        with track_operation(
            "repair-align-uids", params=params, config=track_config
        ) as run:
            with progress_reporter() as progress:
                progress.report(f"Rewriting {target}")
                execute_alignment(
                    alignment, report=progress.report, track=progress.track
                )
            title = f"Align uids: {target} <- {source_slug}"
            report_text = render_alignment([alignment], title=title)
            attach_operation_report(
                run,
                report_text,
                instance=target,
                prefix=track_config.prefix,
                operation="repair-align-uids",
                filename="repair-align-uids-report.txt",
                folder="repair-align-uids-reports",
            )
        if not alignment.executed:
            click.echo(
                f"Error: aligning {target} was refused and rolled back: "
                f"{alignment.error}",
                err=True,
            )
            failed.append(target)
            continue
        written = rewrite_target_locks(alignment)
        click.echo(f"Aligned {len(alignment.renames)} uid(s) on {target}.")
        for path in written:
            click.echo(f"Rewrote lock file: {path}")

    if dry_run:
        click.echo(_DRY_RUN_DONE)
    if failed:
        raise Exit(code=1)


def _ensure_locks_committed(lock_files: Iterable[Path], *, dry_run: bool) -> None:
    """Refuse lock files that are not committed, or only note them in a dry run.

    `repair align-uids` rewrites lock files, so it starts from committed state.
    A dry run rewrites none, so its preview goes on and the note says what a real
    run would refuse.
    """
    from lamindb_setup.evolve.engine import TrackError, ensure_lock_files_committed

    try:
        ensure_lock_files_committed(lock_files)
    except TrackError as exc:
        if not dry_run:
            raise
        click.echo(f"Note: a real run would refuse: {exc}", err=True)


@app.command("verify-dtypes", no_args_is_help=True, deprecated=True, cls=_Command)
@click.option(
    "--instance",
    "-i",
    "instance",
    required=True,
    show_default=True,
    type=str,
    help=_INSTANCE_HELP,
)
@click.option(
    "--space",
    "-s",
    "space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Restrict verification to one or more space uids (repeat to add spaces).",
)
@click.option(
    "--exclude-space",
    "-x",
    "exclude_space",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Exclude one or more space uids from verification (repeat to add spaces); applied after --space.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    default=False,
    show_default=True,
    is_flag=True,
    help="Skip the confirmation prompt.",
)
@click.option(
    "--dry-run",
    "dry_run",
    default=False,
    show_default=True,
    is_flag=True,
    help=_DRY_RUN_HELP,
)
@click.option(
    "--config",
    "-c",
    "config",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help=_REPAIR_CONFIG_HELP,
)
@click.pass_context
def verify_dtypes_cmd(
    ctx: click.Context,
    instance: str,
    space: list[str],
    exclude_space: list[str],
    yes: bool,
    dry_run: bool,
    config: Path,
) -> None:
    """Deprecated alias for 'repair dtypes'."""
    _warn_deprecated("verify-dtypes", "repair dtypes")
    ctx.invoke(
        repair_dtypes,
        instance=instance,
        space=space,
        exclude_space=exclude_space,
        yes=yes,
        dry_run=dry_run,
        config=config,
    )


# === CI subcommands ===========================================================

ci_app = Group(
    name="ci",
    no_args_is_help=True,
    help="Run plan checks in CI from a lamin_schemas.yml configuration.",
)

_CONFIG_HELP = "Path to the CI configuration file (default: lamin_schemas.yml)."
_CI_INSTANCE_HELP = (
    "Restrict the run to this configured instance (repeat for several); "
    "intersects with --changed."
)


@ci_app.command("plan", no_args_is_help=False, cls=_Command)
@click.option(
    "--config",
    "-c",
    "config",
    default=Path("lamin_schemas.yml"),
    show_default=True,
    type=click.Path(path_type=Path),
    help=_CONFIG_HELP,
)
@click.option(
    "--changed",
    "changed",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Restrict the check to these changed plan files (repeat to add files).",
)
@click.option(
    "--instance",
    "-i",
    "instance",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help=_CI_INSTANCE_HELP,
)
@click.option(
    "--output",
    "-o",
    "output",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help="Write the Markdown report to this file (always also printed).",
)
@click.option(
    "--fail-on-diff",
    "fail_on_diff",
    default=False,
    show_default=True,
    is_flag=True,
    help="Exit non-zero when any instance would see changes, not only on errors.",
)
@click.option(
    "--strict-uids/--no-strict-uids",
    "strict_uids",
    default=True,
    show_default=True,
    is_flag=True,
    help="Count an object whose uid differs from the uid the plan's lock files for other instances record as pending, since apply refuses it (default). --no-strict-uids, or 'strict_uids: false' in the config, only reports it, matching what 'ci apply' then does.",
)
@click.option(
    "--strict-renames/--no-strict-renames",
    "strict_renames",
    default=True,
    show_default=True,
    is_flag=True,
    help=f"{_STRICT_RENAMES_HELP} 'ci plan' reports it as an error for that plan and instance, failing the run as 'ci apply' would fail; --no-strict-renames, or 'strict_renames: false' in the config, only warns.",
)
@click.option(
    "--allow-data-validated-changes",
    "allow_data_validated_changes",
    default=False,
    show_default=True,
    is_flag=True,
    help=f"{_ALLOW_DATA_VALIDATED_CHANGES_HELP} 'allow_data_validated_changes: true' in the config opts in too; either way the diff is the one 'ci apply' applies with the same setting.",
)
@click.option(
    "--preview-migrations",
    "preview_migrations",
    default=False,
    show_default=True,
    is_flag=True,
    help="Rehearse each instance's configured plans, dependencies-first as 'ci apply' runs them, in one transaction that is always rolled back: run every declared ctx.migrate() migration and its checks, and report the outcome under each diff -- or record the failure for that plan and instance, as 'ci apply' would fail. The rehearsal attempts writes, so the CI account needs write access to the instances; nothing is persisted and no lock file is written. 'preview_migrations: true' in the config opts in too.",
)
@click.pass_context
def ci_plan(
    ctx: click.Context,
    config: Path,
    changed: list[str],
    instance: list[str],
    output: Path,
    fail_on_diff: bool,
    strict_uids: bool,
    strict_renames: bool,
    allow_data_validated_changes: bool,
    preview_migrations: bool,
) -> None:
    """Validate the configured plans and diff them against their instances.

    Finalizes each selected plan offline, then for every instance the plan is
    deployed to computes a read-only diff, and prints a Markdown report a
    workflow can post on the pull request. Exits non-zero when a plan fails to
    validate, a diff cannot run or 'ci apply' would refuse a diff (a rename not
    declared with ctx.moved, see --strict-renames, or an object borrowed from a
    prerequisite plan neither applied to the instance nor configured for it);
    the presence of pending changes only fails the run with --fail-on-diff.

    --preview-migrations replaces the read-only diff with a rolled-back
    rehearsal of the apply, so a migration that would fail fails the check
    instead of the post-merge 'ci apply'.

    --instance restricts the check to the named instances, as if the
    configuration listed only them: the plans deployed to none of them are
    neither validated nor diffed.
    """
    from lamindb_setup.evolve.ci import render_report, run_pr_check
    from lamindb_setup.evolve.console import progress_reporter

    cfg = _load_ci_config(ctx, config)

    with progress_reporter() as progress:
        report = run_pr_check(
            cfg,
            changed_files=changed or None,
            instances=instance or None,
            report=progress.report,
            track=progress.track,
            strict_uids=strict_uids,
            strict_renames=strict_renames,
            allow_data_validated_changes=allow_data_validated_changes,
            preview_migrations=preview_migrations,
        )
    markdown = render_report(report)

    # Emit the report before deciding the exit code so the workflow can always
    # post it, including on failure.
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
    click.echo(markdown)

    if report.has_errors:
        raise Exit(code=1)
    if fail_on_diff and report.has_changes:
        raise Exit(code=1)


@ci_app.command("apply", no_args_is_help=False, cls=_Command)
@click.option(
    "--config",
    "-c",
    "config",
    default=Path("lamin_schemas.yml"),
    show_default=True,
    type=click.Path(path_type=Path),
    help=_CONFIG_HELP,
)
@click.option(
    "--changed",
    "changed",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Restrict the apply to the instances that have at least one of these changed plan files (repeat to add files).",
)
@click.option(
    "--instance",
    "-i",
    "instance",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help=_CI_INSTANCE_HELP,
)
@click.option(
    "--yes",
    "-y",
    "yes",
    default=False,
    show_default=True,
    is_flag=True,
    hidden=True,
    help="Deprecated and ignored: ci apply never prompts.",
)
@click.option(
    "--strict-renames/--no-strict-renames",
    "strict_renames",
    default=True,
    show_default=True,
    is_flag=True,
    help=f"{_STRICT_RENAMES_HELP} 'strict_renames: false' in the config allows it too.",
)
@click.option(
    "--allow-data-validated-changes",
    "allow_data_validated_changes",
    default=False,
    show_default=True,
    is_flag=True,
    help=f"{_ALLOW_DATA_VALIDATED_CHANGES_HELP} 'allow_data_validated_changes: true' in the config opts in too.",
)
@click.option(
    "--strict-uids/--no-strict-uids",
    "strict_uids",
    default=True,
    show_default=True,
    is_flag=True,
    help="Refuse to apply while an object's uid differs from the uid the plan's lock files for other instances record (default). --no-strict-uids, or 'strict_uids: false' in the config, applies anyway.",
)
@click.option(
    "--format",
    "-f",
    "output_format",
    default=OutputFormat.terminal,
    show_default=True,
    type=_EnumChoice(OutputFormat),
    help=_FORMAT_HELP,
)
@click.option(
    "--output",
    "-o",
    "output",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help=_OUTPUT_HELP,
)
@click.pass_context
def ci_apply(
    ctx: click.Context,
    config: Path,
    changed: list[str],
    instance: list[str],
    yes: bool,
    strict_renames: bool,
    allow_data_validated_changes: bool,
    strict_uids: bool,
    output_format: OutputFormat,
    output: Path,
) -> None:
    """Apply the configured plans to their instances from the configuration.

    The configuration-driven counterpart of 'apply': for each instance in the
    configuration -- optionally restricted with --changed to those declaring
    one of the given plan files -- applies all of its configured plans
    together, so an object moved between sibling plans is treated as moved, not
    deleted, and writes each plan's per-instance lock file.  Like 'apply',
    every plan must declare a stem uid and be committed and pushed.

    Instances are applied in configuration order, each reading the lock files
    the ones before it wrote, so an object new to the plan is created on the
    first instance and with that same uid on the rest.

    --instance restricts the apply to the named instances, still in
    configuration order; with --changed an instance must match both. An
    instance applied on its own reads the lock files an earlier run wrote for
    the others, so a staged roll-out -- one run per instance -- keeps one uid
    per object across them.

    It never asks for confirmation: it runs unattended, and the configuration
    and the reviewed 'ci plan' are what approve the changes.
    """
    from lamindb_setup.evolve.ci import CIApplyError, ConfigError, run_apply
    from lamindb_setup.evolve.console import report_stage
    from lamindb_setup.evolve.engine import SheetExportError, TrackError

    if yes:
        click.echo(
            "Warning: ci apply --yes is deprecated and has no effect; "
            "ci apply never prompts.",
            err=True,
        )
    check_output_format(output_format, output)
    cfg = _load_ci_config(ctx, config)

    try:
        sections = run_apply(
            cfg,
            changed_files=changed or None,
            instances=instance or None,
            output_format=output_format,
            strict_renames=strict_renames,
            allow_data_validated_changes=allow_data_validated_changes,
            strict_uids=strict_uids,
            report=report_stage,
        )
    except ConfigError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise Exit(code=1) from exc
    except TrackError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise Exit(code=1) from exc
    except SheetExportError as exc:
        # Every plan up to this one applied; report them, then fail.
        if output_format.is_text:
            write_and_echo(join_sections(exc.sections, output_format), output)
        raise Exit(code=1) from exc
    except CIApplyError as exc:
        # Some instances applied and some did not. Their applies are committed
        # and their lock files written, so the report is written before failing.
        if output_format.is_text:
            write_and_echo(join_sections(exc.sections, output_format), output)
        click.echo(f"Error: {exc}", err=True)
        raise Exit(code=1) from exc

    if output_format.is_text:
        write_and_echo(join_sections(sections, output_format), output)


@ci_app.command("drift", no_args_is_help=False, cls=_Command)
@click.option(
    "--config",
    "-c",
    "config",
    default=Path("lamin_schemas.yml"),
    show_default=True,
    type=click.Path(path_type=Path),
    help=_CONFIG_HELP,
)
@click.option(
    "--instance",
    "-i",
    "instance",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Restrict the run to this configured instance (repeat for several).",
)
@click.option(
    "--output-dir",
    "-d",
    "output_dir",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help="Directory to write each instance's drift plan into (default: 'drift_output_dir' from the config, else the current dir).",
)
@click.option(
    "--output",
    "-o",
    "output",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help="Write the Markdown drift report to this file (always also printed).",
)
@click.option(
    "--report",
    "-r",
    "report",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    hidden=True,
    help="Deprecated: use -o/--output.",
)
@click.option(
    "--exit-zero",
    "exit_zero",
    default=False,
    show_default=True,
    is_flag=True,
    help="Always exit 0, even when drift is detected.",
)
@click.option(
    "--format-max-chars",
    "format_max_chars",
    default=None,
    show_default=True,
    type=int,
    help="Truncate the rendered report to at most this many characters (e.g. to fit GitHub's issue-body limit).",
)
@click.option(
    "--github-issue",
    "github_issue",
    default=False,
    show_default=True,
    is_flag=True,
    help="Deprecated: use '--format-max-chars'. Condense the report to fit GitHub's issue-body character limit.",
)
@click.pass_context
def ci_drift(
    ctx: click.Context,
    config: Path,
    instance: list[str],
    output_dir: Path,
    output: Path,
    report: Path,
    exit_zero: bool,
    format_max_chars: int,
    github_issue: bool,
) -> None:
    """Detect drift across the configured instances for scheduled runs.

    For each instance, writes 'plan_drift_<slug>.py' describing the objects no
    configured plan tracks, and diffs each configured plan to surface tracked
    objects modified or deleted directly in the instance. Prints a Markdown
    report and exits non-zero when drift is found unless --exit-zero. Pass
    --format-max-chars to truncate the report (e.g. to GitHub's issue limit).

    Drift plans are written to --output-dir when given, otherwise to the
    configuration's 'drift_output_dir' if set, otherwise the current directory.
    --instance restricts the run to the named instances.
    """
    from lamindb_setup.evolve.ci import (
        GITHUB_ISSUE_MAX_CHARS,
        render_drift_report,
        run_drift_detection,
    )
    from lamindb_setup.evolve.console import report_stage

    report_file = _fold_deprecated_alias(
        output, report, alias="ci drift --report/-r", option="-o/--output"
    )
    cfg = _load_ci_config(ctx, config)

    if output_dir is None:
        output_dir = cfg.drift_output_dir or Path()
    result = run_drift_detection(
        cfg, output_dir, instances=instance or None, report=report_stage
    )
    max_chars = format_max_chars
    if github_issue:
        _warn_deprecated("--github-issue", "--format-max-chars")
        if max_chars is None:
            max_chars = GITHUB_ISSUE_MAX_CHARS
    markdown = render_drift_report(result, max_chars=max_chars)

    if report_file is not None:
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(markdown, encoding="utf-8")
    click.echo(markdown)

    # An instance that could not be checked fails the run whatever --exit-zero
    # says: the flag means "report drift without failing the job", not "pass
    # when the check did not happen". The report names each failure.
    if result.has_errors:
        click.echo(
            "Error: drift detection failed for one or more instances; see the "
            "report above.",
            err=True,
        )
        raise Exit(code=1)
    if result.has_drift and not exit_zero:
        raise Exit(code=1)


class _ReportFormat(str, enum.Enum):
    """The formats `ci missing-exports` renders.

    A report has no terminal view, so the shared :class:`OutputFormat` would
    offer a `terminal` choice that could only be ignored.
    """

    markdown = "markdown"
    json = "json"


@ci_app.command("missing-exports", no_args_is_help=False, cls=_Command)
@click.option(
    "--config",
    "-c",
    "config",
    default=Path("lamin_schemas.yml"),
    show_default=True,
    type=click.Path(path_type=Path),
    help=_CONFIG_HELP,
)
@click.option(
    "--changed",
    "changed",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Restrict to the instances that declare these plan files (repeat).",
)
@click.option(
    "--instance",
    "-i",
    "instance",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help=_CI_INSTANCE_HELP,
)
@click.option(
    "--output",
    "-o",
    "output",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help="Write the report to this file (always also printed).",
)
@click.option(
    "--format",
    "-f",
    "output_format",
    default=_ReportFormat.markdown,
    show_default=True,
    type=_EnumChoice(_ReportFormat),
    help="Output format: 'markdown' (default) for a report, or 'json' payload.",
)
@click.option(
    "--json",
    "json_output",
    default=False,
    show_default=True,
    is_flag=True,
    help="Deprecated: use '--format json'. Emit a JSON payload.",
)
@click.option(
    "--fail-on-missing",
    "fail_on_missing",
    default=False,
    show_default=True,
    is_flag=True,
    help="Exit non-zero when any object still needs exporting.",
)
@click.pass_context
def ci_missing_exports(
    ctx: click.Context,
    config: Path,
    changed: list[str],
    instance: list[str],
    output: Path,
    output_format: _ReportFormat,
    json_output: bool,
    fail_on_missing: bool,
) -> None:
    """Report the full closure of tracked objects that plans must export.

    Read-only counterpart of 'ci drift' aimed at agents: in a single introspection
    pass per instance it computes every tracked object an untracked object
    references but that no plan exports yet, grouped by the owning plan, plus the
    untracked objects that exporting cannot make reconstructable. Writes no plan
    or lock files. Exits 0 unless --fail-on-missing and something is missing.
    --instance restricts the run to the named instances.
    """
    import dataclasses
    import json as _json

    from lamindb_setup.evolve.ci import (
        ConfigError,
        render_missing_exports_report,
        run_missing_exports,
    )

    cfg = _load_ci_config(ctx, config)

    try:
        result = run_missing_exports(
            cfg, changed_files=changed or None, instances=instance or None
        )
    except ConfigError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise Exit(code=1) from exc

    if json_output:
        _warn_deprecated("--json", "--format json")
    as_json = json_output or output_format is _ReportFormat.json
    if as_json:
        payload = _json.dumps(dataclasses.asdict(result), indent=2)
    else:
        payload = render_missing_exports_report(result)
    write_and_echo(payload, output)

    # Independent of --fail-on-missing: that flag asks whether *found* gaps
    # should fail the run, and an instance that could not be reconstructed
    # reports no gaps precisely because it could not look for any.
    if result.has_errors:
        click.echo(
            "Error: could not reconstruct one or more instances; see the report above.",
            err=True,
        )
        raise Exit(code=1)
    if fail_on_missing and result.has_missing:
        raise Exit(code=1)


@ci_app.command("verify-config", no_args_is_help=False, cls=_Command)
@click.option(
    "--config",
    "-c",
    "config",
    default=Path("lamin_schemas.yml"),
    show_default=True,
    type=click.Path(path_type=Path),
    help=_CONFIG_HELP,
)
@click.option(
    "--output",
    "-o",
    "output",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help="Write the Markdown report to this file (always also printed).",
)
def ci_verify_config(config: Path, output: Path) -> None:
    """Verify every committed plan is referenced or explicitly ignored.

    Offline check: discovers 'plans/plan_*.py' next to the configuration and
    exits non-zero if any is referenced by no instance and not listed in
    'ignored_plans' (an orphan), or if an ignored plan carries a lock file
    (it must never be applied). Prints a Markdown report a workflow can post.
    """
    from lamindb_setup.evolve.ci import (
        ConfigError,
        load_config,
        render_verify_report,
        verify_config,
    )

    # No project root is resolved here: this check never imports a plan file.
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise Exit(code=1) from exc

    result = verify_config(cfg)
    markdown = render_verify_report(result)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
    click.echo(markdown)

    if not result.ok:
        raise Exit(code=1)


# === utils subcommands ========================================================

utils_app = Group(
    name="utils",
    no_args_is_help=True,
    help="Miscellaneous helpers for authoring plans.",
)


@utils_app.command("add-uid", no_args_is_help=True, cls=_Command)
@click.argument(
    "plan",
    cls=_Argument,
    required=True,
    type=click.Path(path_type=Path),
    help="Existing plan file to add a PLAN_TRANSFORM_STEM_UID to.",
)
@click.option(
    "--instance",
    "-i",
    "instance",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Instance(s) to check the generated uid against for collisions. Repeat to require the uid to be free on several instances.",
)
def utils_add_uid(plan: Path, instance: list[str]) -> None:
    """Add a transform stem uid to an existing plan file.

    Inserts a PLAN_TRANSFORM_STEM_UID above the plan's '@create_plan'
    decorator so it can be used with 'validate', 'plan' and 'apply'. Errors
    if the plan already declares one.
    """
    from lamindb_setup.evolve.engine import PLAN_STEM_UID_VAR
    from lamindb_setup.evolve.introspect import add_stem_uid_to_plan

    uid = add_stem_uid_to_plan(plan, instance or ())
    click.echo(f"Added {PLAN_STEM_UID_VAR} = {uid!r} to {plan}")


@utils_app.command("generate-stem-uid", no_args_is_help=False, cls=_Command)
@click.option(
    "--instance",
    "-i",
    "instance",
    default=None,
    show_default=True,
    type=str,
    multiple=True,
    callback=_as_optional_list,
    help="Instance(s) to check the generated uid(s) against for collisions. Repeat to require the uids to be free on several instances.",
)
@click.option(
    "--count",
    "-n",
    "count",
    default=1,
    show_default=True,
    type=click.IntRange(min=1),
    help="Number of stem uids to generate.",
)
def utils_generate_stem_uid(instance: list[str], count: int) -> None:
    """Generate one or more fresh 12-character base62 transform stem uids.

    A stem uid is the value a plan assigns to PLAN_TRANSFORM_STEM_UID. Each
    --instance is checked for the transforms it already holds, so the uids do
    not collide with any of them; the generated uids are also kept distinct from
    one another. Each uid is printed on its own line.
    """
    from lamindb_setup.evolve.engine import generate_stem_uids

    for uid in generate_stem_uids(count, instances=instance or ()):
        click.echo(uid)


def _fingerprint_entry(key: str, cfg: dict[str, Any]) -> str:
    """Render one fingerprint entry, normalizing encoding-only dtype differences.

    `serialize_config` records a plan's dtype as written: an object dtype
    (`LaminDType(ln.Record, ...)`) serializes to the registry's import path
    (`lamindb.models.record.Record`) while the annotation form
    (`CatDType[ln.Record, RecordCat(...)]`) serializes to lamindb's canonical
    string (`cat[Record]`). Both materialize the same feature, so rewriting a
    plan from one form to the other must not read as drift; the dtype is
    canonicalized through the same normalizer the drift classifier uses.
    """
    import json

    from lamindb_setup.evolve.authoring import canonical_dtype_str

    dtype = cfg.get("dtype")
    if isinstance(dtype, str):
        cfg = {**cfg, "dtype": canonical_dtype_str(dtype)}
    return f"{key}\n{json.dumps(cfg, sort_keys=True, default=str)}"


def _plan_fingerprint(
    loaded: list[tuple[Plan, Path, ModuleType]],
) -> Counter[str]:
    """Return the multiset of `(natural_key, canonical config)` over the plans.

    Each entry is the natural key and a canonical, `uid`-independent JSON
    snapshot of one object in a plan's `own_dependencies` (the objects the plan
    concretely creates, excluding those inherited from `depends_on` plans). The
    plans are finalized here, so they must be loaded -- but not yet finalized --
    up front (as :func:`~lamindb_setup.evolve.engine._loader.load_all_plans` does),
    keeping construction lamindb-free.
    """
    from collections import Counter

    from lamindb_setup.evolve.engine import natural_key, serialize_config

    counter: Counter[str] = Counter()
    for plan_obj, _path, _module in loaded:
        plan_obj.finalize()
        for dep in plan_obj.own_dependencies:
            obj = dep.obj
            counter[_fingerprint_entry(natural_key(obj), serialize_config(obj))] += 1
    return counter


def _fingerprint_configs_by_key(counter: Counter[str]) -> dict[str, list[dict]]:
    """Expand a fingerprint multiset into `natural key -> [config, ...]`."""
    import json

    grouped: dict[str, list[dict]] = {}
    for entry, count in counter.items():
        key, _, payload = entry.partition("\n")
        try:
            cfg = json.loads(payload)
        except ValueError:
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        grouped.setdefault(key, []).extend([cfg] * count)
    return grouped


def _echo_fingerprint_config_diff(old: dict, new: dict) -> None:
    """Print the per-field differences between two serialized configurations."""
    for field in sorted(set(old) | set(new)):
        before = old.get(field, "<unset>")
        after = new.get(field, "<unset>")
        if before != after:
            click.echo(f"      {field}: {before!r} -> {after!r}")


def _echo_fingerprint_drift(base: Counter[str], current: Counter[str]) -> None:
    """Report the difference between a baseline and a current fingerprint.

    Objects whose natural key exists on both sides are reported as `CHANGED`
    with the configuration fields that differ, so a diff points at the property
    that moved instead of only naming the object twice.
    """
    click.echo(f"DRIFT: baseline={sum(base.values())} current={sum(current.values())}")

    missing = _fingerprint_configs_by_key(base - current)
    extra = _fingerprint_configs_by_key(current - base)

    for key in sorted(set(missing) & set(extra)):
        olds = missing.pop(key)
        news = extra.pop(key)
        for old_cfg, new_cfg in zip(olds, news, strict=False):
            click.echo(f"  ~ CHANGED: {key}")
            _echo_fingerprint_config_diff(old_cfg, new_cfg)
        if len(olds) > len(news):
            missing[key] = olds[len(news) :]
        elif len(news) > len(olds):
            extra[key] = news[len(olds) :]

    for key, cfgs in sorted(missing.items()):
        click.echo(f"  - MISSING x{len(cfgs)}: {key}")
    for key, cfgs in sorted(extra.items()):
        click.echo(f"  + EXTRA   x{len(cfgs)}: {key}")


_FINGERPRINT_TARGETS_HELP = (
    "One or more plans to fingerprint, each as 'path/to/plan.py:plan_variable' "
    "(the ':plan_variable' suffix is optional when the file defines exactly one "
    "plan). Pass every plan the refactor involves: their fingerprints are "
    "combined into one."
)


@utils_app.command("fingerprint", no_args_is_help=True, cls=_Command)
@click.argument(
    "targets",
    cls=_Argument,
    required=True,
    type=str,
    nargs=-1,
    callback=_as_list,
    help=_FINGERPRINT_TARGETS_HELP,
)
@click.option(
    "--baseline",
    "-b",
    "baseline",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help="Compare the plans against this captured fingerprint and exit non-zero on drift, instead of capturing a new one.",
)
@click.option(
    "--output",
    "-o",
    "output",
    default=None,
    show_default=True,
    type=click.Path(path_type=Path),
    help="Write the captured fingerprint JSON here (printed to stdout when omitted); also honored in --baseline mode to save the current one.",
)
def utils_fingerprint(targets: list[str], baseline: Path, output: Path) -> None:
    """Fingerprint one or more plans to prove a refactor is drift-free.

    The fingerprint is the multiset of (natural key, canonical serialized
    config) over every object each plan concretely creates, independent of
    materialized uid values. Splitting a monolithic plan into several is
    drift-free exactly when the union of the new plans' fingerprints equals the
    original's. Dtypes are canonicalized, so rewriting a
    'LaminDType(ln.Record, ...)' as the equivalent 'CatDType' annotation -- the
    same lamindb dtype in a different notation -- does not read as drift.

    Without --baseline this captures: it writes the fingerprint JSON to
    --output (or prints it to stdout). With --baseline BASELINE.json it
    diffs the given plans against that captured fingerprint, printing whether
    they are identical and, on drift, the changed, missing and extra objects; it
    exits non-zero when they differ.

    Typical use:

        lamin evolve utils fingerprint plans/plan_mono.py -o baseline.json
        # ... split plan_mono into plan_a and plan_b ...
        lamin evolve utils fingerprint plans/plan_a.py plans/plan_b.py -b baseline.json
    """
    import json
    from collections import Counter

    loaded = load_all_plans(targets)
    current = _plan_fingerprint(loaded)

    if baseline is None:
        payload = json.dumps(dict(current), indent=2, sort_keys=True)
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload, encoding="utf-8")
            click.echo(f"Captured {sum(current.values())} objects -> {output}")
        else:
            click.echo(payload)
        return

    try:
        raw: dict[str, int] = json.loads(baseline.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        click.echo(f"Error: could not read baseline {baseline}: {exc}", err=True)
        raise Exit(code=1) from exc

    # Re-render the baseline through the same normalizer, so a fingerprint
    # captured by an older version (or from a plan written in the other dtype
    # form) is compared on equal footing.
    base: Counter[str] = Counter()
    for entry, count in raw.items():
        key, _, payload = entry.partition("\n")
        try:
            cfg = json.loads(payload)
        except ValueError:
            base[entry] += count
            continue
        base[_fingerprint_entry(key, cfg) if isinstance(cfg, dict) else entry] += count

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(dict(current), indent=2, sort_keys=True), encoding="utf-8"
        )

    if current == base:
        click.echo(f"IDENTICAL: {sum(current.values())} objects, drift-free.")
        return

    _echo_fingerprint_drift(base, current)
    raise Exit(code=1)


# Register the sub-command groups here, after
# `introspect` and `repair`, so the "Introspection, debugging & migration" panel
# lists them in the order: introspect, repair, utils, ci.
app.add_command(utils_app, name="utils")
app.add_command(ci_app, name="ci")


if __name__ == "__main__":  # pragma: no cover
    app()
