from __future__ import annotations

import json

from click.testing import CliRunner
from lamin_cli.hub import rest


def test_rest_insert_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return [{"uid": "abc123", "name": "treated"}]

    monkeypatch.setattr("lamin_cli.hub._mutations.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        ["insert", "core", "ulabel", "--objects", '{"name":"treated"}', "--compact"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [{"uid": "abc123", "name": "treated"}]
    assert calls == [
        ("put", "modules/core/ulabel", None, {"name": "treated"}),
    ]


def test_rest_upsert_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return [{"uid": "abc123", "name": "treated"}]

    monkeypatch.setattr("lamin_cli.hub._mutations.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        [
            "upsert",
            "core",
            "ulabel",
            "--conflict-column",
            "name",
            "--objects",
            '[{"name":"treated"}]',
            "--compact",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [{"uid": "abc123", "name": "treated"}]
    assert calls == [
        (
            "put",
            "modules/core/ulabel/upsert",
            {"conflict_columns": ["name"]},
            [{"name": "treated"}],
        )
    ]


def test_rest_update_single_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return {"uid": "abc123", "description": "updated"}

    monkeypatch.setattr("lamin_cli.hub._mutations.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        [
            "update",
            "core",
            "ulabel",
            "abc123",
            "--values",
            '{"description":"updated"}',
            "--compact",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"uid": "abc123", "description": "updated"}
    assert calls == [
        (
            "patch",
            "modules/core/ulabel/abc123",
            None,
            {"description": "updated"},
        )
    ]


def test_rest_update_batch_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return [{"uid": "abc123", "description": "updated"}]

    monkeypatch.setattr("lamin_cli.hub._mutations.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        [
            "update",
            "core",
            "project",
            "--index-column",
            "uid",
            "--objects",
            '[{"uid":"abc123","description":"updated"}]',
            "--compact",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [{"uid": "abc123", "description": "updated"}]
    assert calls == [
        (
            "patch",
            "modules/core/project/batch-update",
            None,
            {
                "index_columns": ["uid"],
                "records": [{"uid": "abc123", "description": "updated"}],
            },
        )
    ]


def test_rest_delete_single_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return {"deleted": 1}

    monkeypatch.setattr("lamin_cli.hub._mutations.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        ["delete", "core", "ulabel", "abc123", "--compact"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"deleted": 1}
    assert calls == [("delete", "modules/core/ulabel/abc123", None, None)]


def test_rest_delete_batch_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return {"deleted": 2}

    monkeypatch.setattr("lamin_cli.hub._mutations.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        [
            "delete",
            "core",
            "recordrecord",
            "--objects",
            '[{"record_id":1,"feature_id":2,"value_id":3}]',
            "--compact",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"deleted": 2}
    assert calls == [
        (
            "post",
            "modules/core/recordrecord/batch-delete",
            None,
            {"records": [{"record_id": 1, "feature_id": 2, "value_id": 3}]},
        )
    ]
