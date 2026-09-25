import click
from lamin_cli.__main__ import main


def test_cli_init_storage_default_is_storage_dir():
    context = click.Context(main)
    command = main.get_command(context, "init")
    assert command is not None
    storage_option = next(param for param in command.params if param.name == "storage")
    assert storage_option.default == "./storage"
