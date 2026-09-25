from __future__ import annotations

import json

from click.testing import CliRunner
from lamin_cli.hub import hub


def test_rest_statistics_constructs_request(patch_request_json):
    def handler(method, path, params, body):
        return {"instance_size": 123, "counts": {"core": {"ULabel": 2}}}

    calls = patch_request_json("_statistics", handler)

    result = CliRunner().invoke(
        hub,
        [
            "statistics",
            "--model",
            "core.ULabel",
            "--model",
            "core.Artifact",
            "--format",
            "json",
            "--compact",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "instance_size": 123,
        "counts": {"core": {"ULabel": 2}},
    }
    assert calls == [
        ("get", "statistics", {"q": ["core.ULabel", "core.Artifact"]}, None)
    ]


def test_rest_statistics_module_scope_constructs_request(
    patch_statistics_request_json, schema_payload
):
    def handler(method, path, params, body):
        if path == "schema":
            return schema_payload()
        return {"instance_size": 123, "counts": {"core": {"Artifact": 1, "User": 2}}}

    calls = patch_statistics_request_json(handler)

    result = CliRunner().invoke(
        hub, ["statistics", "core", "--format", "json", "--compact"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "instance_size": 123,
        "counts": {"core": {"Artifact": 1, "User": 2}},
    }
    assert calls == [
        ("get", "schema", None, None),
        ("get", "statistics", {"q": ["core.Artifact", "core.User"]}, None),
    ]


def test_rest_statistics_model_scope_constructs_request(
    patch_statistics_request_json, schema_payload
):
    def handler(method, path, params, body):
        if path == "schema":
            return schema_payload()
        return {"instance_size": 123, "counts": {"core": {"Artifact": 1}}}

    calls = patch_statistics_request_json(handler)

    result = CliRunner().invoke(
        hub, ["statistics", "core", "artifact", "--format", "json", "--compact"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "instance_size": 123,
        "counts": {"core": {"Artifact": 1}},
    }
    assert calls == [
        ("get", "schema", None, None),
        ("get", "statistics", {"q": ["core.Artifact"]}, None),
    ]


def test_rest_statistics_object_scope_constructs_relation_counts_request(
    patch_statistics_request_json, schema_payload
):
    def handler(method, path, params, body):
        if path == "schema":
            return schema_payload()
        return {"projects": 1, "ulabels": 2}

    calls = patch_statistics_request_json(handler)

    result = CliRunner().invoke(
        hub,
        ["statistics", "core", "artifact", "123", "--format", "json", "--compact"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"projects": 1, "ulabels": 2}
    assert calls == [
        ("get", "schema", None, None),
        ("get", "modules/core/artifact/123/counts", None, None),
    ]


def test_rest_statistics_markdown_summary_by_default(
    patch_statistics_request_json, schema_payload
):
    def handler(method, path, params, body):
        if path == "schema":
            return schema_payload()
        return {"instance_size": 123, "counts": {"core": {"Artifact": 1}}}

    calls = patch_statistics_request_json(handler)

    result = CliRunner().invoke(hub, ["statistics", "core", "artifact"])

    assert result.exit_code == 0, result.output
    assert "# Statistics" in result.output
    assert "- instance size: 123 bytes" in result.output
    assert "## Counts" in result.output
    assert "- core.Artifact: 1" in result.output
    assert calls == [
        ("get", "schema", None, None),
        ("get", "statistics", {"q": ["core.Artifact"]}, None),
    ]


def test_rest_statistics_relation_counts_markdown_by_default(
    patch_statistics_request_json, schema_payload
):
    def handler(method, path, params, body):
        if path == "schema":
            return schema_payload()
        return {"projects": 1, "ulabels": 2}

    calls = patch_statistics_request_json(handler)

    result = CliRunner().invoke(hub, ["statistics", "core", "artifact", "123"])

    assert result.exit_code == 0, result.output
    assert "# Relation Counts" in result.output
    assert "- projects: 1" in result.output
    assert "- ulabels: 2" in result.output
    assert calls == [
        ("get", "schema", None, None),
        ("get", "modules/core/artifact/123/counts", None, None),
    ]


def test_rest_statistics_rejects_mixed_model_option_and_scope(
    patch_statistics_request_json,
):
    patch_statistics_request_json(lambda *args: {})

    result = CliRunner().invoke(hub, ["statistics", "core", "--model", "core.ULabel"])

    assert result.exit_code != 0
    assert "--model cannot be combined with module/model scope." in result.output


def test_rest_relation_counts_constructs_request(
    patch_statistics_request_json, schema_payload
):
    def handler(method, path, params, body):
        if path == "schema":
            return schema_payload()
        return {"projects": 1, "ulabels": 2}

    calls = patch_statistics_request_json(handler)

    result = CliRunner().invoke(
        hub,
        ["relation-counts", "core", "artifact", "123", "--compact"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"projects": 1, "ulabels": 2}
    assert calls == [
        ("get", "schema", None, None),
        ("get", "modules/core/artifact/123/counts", None, None),
    ]
