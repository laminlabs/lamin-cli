from pathlib import Path

import click
from lamin_cli.__main__ import main


def test_save_path_shell_completion_returns_file_items(
    tmp_path: Path, monkeypatch
) -> None:
    file_path = tmp_path / "data.csv"
    file_path.write_text("a,b\n1,2\n")
    monkeypatch.chdir(tmp_path)

    root_ctx = click.Context(main)
    save_cmd = main.get_command(root_ctx, "save")
    assert save_cmd is not None
    save_ctx = click.Context(save_cmd, info_name="save", parent=root_ctx)

    path_param = next(param for param in save_cmd.params if param.name == "path")
    completions = path_param.shell_complete(save_ctx, "da")

    assert any(completion.type == "file" for completion in completions)
