"""Click parsing and help conventions for the evolution commands."""

from __future__ import annotations

import os
from typing import Any

if os.environ.get("NO_RICH"):
    import click as click
    from click import Command as BaseCommand
    from click import Group
else:
    import rich_click as click
    from rich_click import RichCommand as BaseCommand
    from rich_click import RichGroup as Group


class Argument(click.Argument):
    """Retain argument help on Click versions that do not accept it directly."""

    def __init__(self, *args: Any, help: str | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.help = help


class Command(BaseCommand):
    """Show argument descriptions in the plain Click help as well."""

    def format_options(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        super().format_options(ctx, formatter)
        if os.environ.get("NO_RICH"):
            rows = [
                (p.human_readable_name, p.help)
                for p in self.params
                if isinstance(p, Argument) and p.help
            ]
            if rows:
                with formatter.section("Arguments"):
                    formatter.write_dl(rows)


class EnumChoice(click.Choice):
    """Parse the source command's enum values without depending on Typer."""

    def __init__(self, enum_type: Any):
        self.enum_type = enum_type
        super().__init__([member.value for member in enum_type])

    def convert(self, value: Any, param: Any, ctx: Any) -> Any:
        if isinstance(value, self.enum_type):
            return value
        return self.enum_type(super().convert(value, param, ctx))


def as_list(ctx: Any, param: Any, value: Any) -> list[str]:
    return list(value)


def as_optional_list(ctx: Any, param: Any, value: Any) -> list[str] | None:
    return list(value) if value else None


def configure_help(app: Group) -> None:
    """Use Lamin's rich-click panels, with literal help text and visible arguments."""
    if not os.environ.get("NO_RICH"):
        click.rich_config(
            help_config=click.RichHelpConfiguration(
                show_arguments=True,
                use_rich_markup=False,
                command_groups={
                    "*evolve": [
                        {
                            "name": "Commands",
                            "commands": [
                                "init",
                                "validate",
                                "plan",
                                "apply",
                                "destroy",
                            ],
                        },
                        {
                            "name": "Introspection, debugging & migration",
                            "commands": ["introspect", "repair", "utils", "ci"],
                        },
                    ]
                },
            )
        )(app)
