from __future__ import annotations

import json

from click.testing import CliRunner
from lamin_cli._rest import rest


def test_rest_list_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return [{"uid": "abc123", "key": "sample.parquet"}]

    monkeypatch.setattr("lamin_cli._rest.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        [
            "list",
            "core",
            "artifact",
            "--select",
            "uid",
            "--select",
            "key",
            "--filter",
            '{"key":{"contains":"sample"}}',
            "--search",
            "sample",
            "--search-in",
            "ulabels.name",
            "--limit",
            "10",
            "--offset",
            "2",
            "--include-foreign-keys",
            "--compact",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [{"uid": "abc123", "key": "sample.parquet"}]
    assert calls == [
        (
            "post",
            "modules/core/artifact",
            {
                "limit_to_many": 10,
                "limit": 10,
                "offset": 2,
                "include_foreign_keys": "true",
            },
            {
                "select": ["uid", "key"],
                "filter": {"key": {"contains": "sample"}},
                "search": "sample",
                "search_in": ["ulabels.name"],
            },
        )
    ]


def test_rest_get_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return {"uid": "abc123", "name": "sample"}

    monkeypatch.setattr("lamin_cli._rest.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        [
            "get",
            "core",
            "record",
            "abc123",
            "--select",
            '["uid","name"]',
            "--limit-to-many",
            "3",
            "--compact",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"uid": "abc123", "name": "sample"}
    assert calls == [
        (
            "post",
            "modules/core/record/abc123",
            {"limit_to_many": 3},
            {"select": ["uid", "name"]},
        )
    ]


def test_rest_schema_scopes_response(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return {
            "core": {
                "artifact": {"fields": {"uid": {"type": "string"}}},
                "record": {"fields": {"name": {"type": "string"}}},
            },
            "bionty": {},
        }

    monkeypatch.setattr("lamin_cli._rest.request_json", fake_request_json)

    result = CliRunner().invoke(rest, ["schema", "core", "artifact", "--compact"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"fields": {"uid": {"type": "string"}}}
    assert calls == [("get", "schema", None, None)]


def test_rest_statistics_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return {"instance_size": 123, "counts": {"core": {"ULabel": 2}}}

    monkeypatch.setattr("lamin_cli._rest.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        [
            "statistics",
            "--model",
            "core.ULabel",
            "--model",
            "core.Artifact",
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


def test_rest_relation_counts_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return {"projects": 1, "ulabels": 2}

    monkeypatch.setattr("lamin_cli._rest.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        ["relation-counts", "core", "artifact", "123", "--compact"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"projects": 1, "ulabels": 2}
    assert calls == [("get", "modules/core/artifact/123/counts", None, None)]
