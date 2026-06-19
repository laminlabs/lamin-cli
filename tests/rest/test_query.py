from __future__ import annotations

import json

from click.testing import CliRunner
from lamin_cli.hub import rest


def test_rest_list_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return [{"uid": "abc123", "key": "sample.parquet"}]

    monkeypatch.setattr("lamin_cli.hub._query.request_json", fake_request_json)

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

    monkeypatch.setattr("lamin_cli.hub._query.request_json", fake_request_json)

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
