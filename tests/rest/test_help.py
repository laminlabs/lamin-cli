from __future__ import annotations

from click.testing import CliRunner
from lamin_cli._rest import rest


def test_rest_mutation_help_uses_objects_option():
    for command in ["insert", "upsert", "update", "delete"]:
        result = CliRunner().invoke(rest, [command, "--help"])

        assert result.exit_code == 0, result.output
        assert "--objects" in result.output


def test_rest_help_groups_commands():
    result = CliRunner().invoke(rest, ["--help"])

    assert result.exit_code == 0, result.output
    assert "Metadata" in result.output
    assert "Reads" in result.output
    assert "Mutations" in result.output


def test_rest_list_help_includes_nested_filter_example():
    result = CliRunner().invoke(rest, ["list", "--help"])

    assert result.exit_code == 0, result.output
    assert "--filter" in result.output
    assert '"and"' in result.output
    assert '"is_type":{"eq":true}' in result.output
    assert '"name":{"contains":"dataset"}' in result.output


def test_rest_insert_help_includes_bulk_ulabel_example():
    result = CliRunner().invoke(rest, ["insert", "--help"])

    assert result.exit_code == 0, result.output
    assert "lamin rest insert core ulabel" in result.output
    assert "--objects" in result.output
    assert '[{"name":"control"},{"name":"treated"}]' in result.output


def test_rest_delete_help_includes_bulk_ulabel_example():
    result = CliRunner().invoke(rest, ["delete", "--help"])

    assert result.exit_code == 0, result.output
    assert "lamin rest delete core ulabel" in result.output
    assert "--objects" in result.output
    assert '[{"name":"control"},{"name":"treated"}]' in result.output
