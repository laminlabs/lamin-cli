from __future__ import annotations

import os
from functools import wraps
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

REST_COMMAND_GROUP_LIST = [
    {
        "name": "Metadata",
        "commands": ["schema", "statistics"],
    },
    {
        "name": "Reads",
        "commands": ["list", "get"],
    },
    {
        "name": "Mutations",
        "commands": ["insert", "upsert", "update", "delete"],
    },
]

REST_COMMAND_GROUPS = {"*": REST_COMMAND_GROUP_LIST}

if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click


class PlainHubGroup(click.Group):
    """Group Hub commands by purpose in plain Click help output."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        ordered: list[str] = []
        for group in REST_COMMAND_GROUP_LIST:
            ordered.extend(
                command for command in group["commands"] if command in self.commands
            )
        ordered_set = set(ordered)
        ordered.extend(
            command for command in self.commands if command not in ordered_set
        )
        return ordered

    def format_commands(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        grouped = set()
        for group in REST_COMMAND_GROUP_LIST:
            rows = []
            for name in group["commands"]:
                command = self.get_command(ctx, name)
                if command is None or command.hidden:
                    continue
                grouped.add(name)
                rows.append((name, command.get_short_help_str(limit=120)))
            if rows:
                with formatter.section(group["name"]):
                    formatter.write_dl(rows)

        rows = []
        for name in self.commands:
            if name in grouped:
                continue
            command = self.get_command(ctx, name)
            if command is None or command.hidden:
                continue
            rows.append((name, command.get_short_help_str(limit=120)))
        if rows:
            with formatter.section("Other commands"):
                formatter.write_dl(rows)


if os.environ.get("NO_RICH"):

    def hub_group(f: Callable[..., Any]) -> click.Group:
        return click.group(cls=PlainHubGroup)(f)


else:

    def hub_group(f: Callable[..., Any]) -> click.Group:
        @click.rich_config(
            help_config=click.RichHelpConfiguration(
                command_groups=REST_COMMAND_GROUPS,
                style_commands_table_column_width_ratio=(1, 8),
            )
        )
        @click.group()
        @wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)

        return wrapper
