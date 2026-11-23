"""The `lamin evolve` command group, loaded before any instance connection."""

from __future__ import annotations

from typing import Any

from ._click import Group, click


def _backend_app() -> Group:
    try:
        from ._commands import app
    except ModuleNotFoundError as exc:
        if (exc.name or "").startswith("lamindb_setup.evolve"):
            raise click.ClickException(
                "This command requires a lamindb-setup build with evolve. "
                "Install the matching lamindb-setup checkout with its 'evolve' extra."
            ) from None
        raise
    return app


class _LazyEvolveGroup(Group):
    """Defer the backend import until this group is actually selected."""

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: click.Context | None = None,
        **extra: Any,
    ) -> click.Context:
        return _backend_app().make_context(info_name, args, parent=parent, **extra)

    def invoke(self, ctx: click.Context) -> Any:
        # make_context returns the real group's context, including its options.
        return ctx.command.invoke(ctx)

    def _load(self, ctx: click.Context) -> Group:
        app = _backend_app()
        self.commands = app.commands
        return app

    def get_help(self, ctx: click.Context) -> str:
        return self._load(ctx).get_help(ctx)

    def list_commands(self, ctx: click.Context) -> list[str]:
        return self._load(ctx).list_commands(ctx)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        return self._load(ctx).get_command(ctx, cmd_name)


evolve = _LazyEvolveGroup(
    name="evolve", help="Manage schema plans, migrations, and repairs."
)
