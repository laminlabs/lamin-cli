from __future__ import annotations

import json

from click.testing import CliRunner
from lamin_cli._rest import rest


def _schema_payload():
    return {
        "core": {
            "artifact": {
                "class_name": "Artifact",
                "table_name": "lamindb_artifact",
                "name_field": "key",
                "fields": {
                    "uid": {
                        "type": "string",
                        "column_name": "uid",
                        "is_primary_key": False,
                        "is_editable": False,
                    },
                    "_aux": {
                        "type": "json",
                        "column_name": "_aux",
                        "is_primary_key": False,
                        "is_editable": False,
                    },
                    "created_by": {
                        "relation_type": "many-to-one",
                        "related_schema_name": "core",
                        "related_model_name": "user",
                        "through": None,
                    },
                },
            },
            "user": {
                "class_name": "User",
                "fields": {
                    "uid": {"type": "string"},
                    "name": {"type": "string"},
                    "handle": {"type": "string"},
                },
            },
            "artifact__ulabels": {
                "is_link_table": True,
                "fields": {"artifact": {"relation_type": "many-to-one"}},
            },
        },
        "bionty": {},
    }


def test_rest_list_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return [{"uid": "abc123", "key": "sample.parquet"}]

    monkeypatch.setattr("lamin_cli._rest._query.request_json", fake_request_json)

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

    monkeypatch.setattr("lamin_cli._rest._query.request_json", fake_request_json)

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


def test_rest_schema_raw_scopes_response(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return _schema_payload()

    monkeypatch.setattr("lamin_cli._rest._query.request_json", fake_request_json)
    monkeypatch.setattr(
        "lamin_cli._rest._query._current_schema_cache_path", lambda: None
    )

    result = CliRunner().invoke(
        rest, ["schema", "core", "artifact", "--raw", "--compact"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == _schema_payload()["core"]["artifact"]
    assert calls == [("get", "schema", None, None)]


def test_rest_schema_markdown_summary(monkeypatch):
    def fake_request_json(method, path, *, params=None, body=None):
        return _schema_payload()

    monkeypatch.setattr("lamin_cli._rest._query.request_json", fake_request_json)
    monkeypatch.setattr(
        "lamin_cli._rest._query._current_schema_cache_path", lambda: None
    )

    result = CliRunner().invoke(rest, ["schema", "core", "artifact"])

    assert result.exit_code == 0, result.output
    assert "# Schema: core.artifact" in result.output
    assert "- class: Artifact" in result.output
    assert "- table: lamindb_artifact" in result.output
    assert "- fields: 1 scalar, 1 relations, 1 hidden (3 total)" in result.output
    assert "- uid: string" in result.output
    assert "- created_by -> core.user (many-to-one)" in result.output
    assert "select `" not in result.output
    assert "_aux" not in result.output


def test_rest_schema_models_matches_each_module_output(monkeypatch):
    def fake_request_json(method, path, *, params=None, body=None):
        return _schema_payload()

    monkeypatch.setattr("lamin_cli._rest._query.request_json", fake_request_json)
    monkeypatch.setattr(
        "lamin_cli._rest._query._current_schema_cache_path", lambda: None
    )

    all_models = CliRunner().invoke(rest, ["schema", "--models"])
    core = CliRunner().invoke(rest, ["schema", "core"])
    bionty = CliRunner().invoke(rest, ["schema", "bionty"])

    assert all_models.exit_code == 0, all_models.output
    assert core.exit_code == 0, core.output
    assert bionty.exit_code == 0, bionty.output
    assert all_models.output == f"{bionty.output}\n{core.output}"


def test_rest_schema_models_rejects_raw(monkeypatch):
    monkeypatch.setattr(
        "lamin_cli._rest._query.request_json", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr(
        "lamin_cli._rest._query._current_schema_cache_path", lambda: None
    )

    result = CliRunner().invoke(rest, ["schema", "--models", "--raw"])

    assert result.exit_code != 0
    assert "--models cannot be combined with --raw." in result.output


def test_rest_schema_json_summary_includes_hidden(monkeypatch):
    def fake_request_json(method, path, *, params=None, body=None):
        return _schema_payload()

    monkeypatch.setattr("lamin_cli._rest._query.request_json", fake_request_json)
    monkeypatch.setattr(
        "lamin_cli._rest._query._current_schema_cache_path", lambda: None
    )

    result = CliRunner().invoke(
        rest,
        [
            "schema",
            "core",
            "artifact",
            "--format",
            "json",
            "--include-hidden",
            "--compact",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "scope": "model",
        "module": "core",
        "model": "artifact",
        "class": "Artifact",
        "table": "lamindb_artifact",
        "name_field": "key",
        "ontology_id_field": None,
        "fields": 3,
        "scalar_fields": [
            {
                "name": "uid",
                "type": "string",
                "column": "uid",
                "primary_key": False,
                "editable": False,
            },
            {
                "name": "_aux",
                "type": "json",
                "column": "_aux",
                "primary_key": False,
                "editable": False,
            },
        ],
        "relations": [
            {
                "name": "created_by",
                "relation_type": "many-to-one",
                "target": "core.user",
                "through": None,
            }
        ],
        "hidden_fields": 0,
        "include_hidden": True,
    }


def test_rest_schema_uses_cache(monkeypatch, tmp_path):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return _schema_payload()

    monkeypatch.setattr("lamin_cli._rest._query.request_json", fake_request_json)
    monkeypatch.setattr(
        "lamin_cli._rest._query._current_instance", lambda: ("inst/1", "")
    )
    monkeypatch.setattr(
        "lamin_cli._rest._query._current_instance_schema_id", lambda: "schema:1"
    )
    monkeypatch.setenv("LAMIN_REST_SCHEMA_CACHE_DIR", str(tmp_path))

    first = CliRunner().invoke(rest, ["schema", "--format", "json", "--compact"])
    second = CliRunner().invoke(rest, ["schema", "--format", "json", "--compact"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert calls == [("get", "schema", None, None)]
    assert json.loads(first.output) == json.loads(second.output)


def test_rest_schema_refresh_bypasses_cache(monkeypatch, tmp_path):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        schema = _schema_payload()
        schema["new"] = {}
        return schema

    monkeypatch.setattr("lamin_cli._rest._query.request_json", fake_request_json)
    monkeypatch.setattr(
        "lamin_cli._rest._query._current_instance", lambda: ("inst/1", "")
    )
    monkeypatch.setattr(
        "lamin_cli._rest._query._current_instance_schema_id", lambda: "schema:1"
    )
    monkeypatch.setenv("LAMIN_REST_SCHEMA_CACHE_DIR", str(tmp_path))

    cached = tmp_path / "rest" / "schemas" / "inst_1" / "schema_1.json"
    cached.parent.mkdir(parents=True)
    cached.write_text(json.dumps(_schema_payload()))

    result = CliRunner().invoke(
        rest, ["schema", "--refresh", "--format", "json", "--compact"]
    )

    assert result.exit_code == 0, result.output
    assert calls == [("get", "schema", None, None)]
    assert any(
        module["module"] == "new" for module in json.loads(result.output)["modules"]
    )


def test_rest_statistics_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return {"instance_size": 123, "counts": {"core": {"ULabel": 2}}}

    monkeypatch.setattr("lamin_cli._rest._query.request_json", fake_request_json)

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

    monkeypatch.setattr("lamin_cli._rest._query.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        ["relation-counts", "core", "artifact", "123", "--compact"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"projects": 1, "ulabels": 2}
    assert calls == [("get", "modules/core/artifact/123/counts", None, None)]


def test_rest_insert_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return [{"uid": "abc123", "name": "treated"}]

    monkeypatch.setattr("lamin_cli._rest._mutations.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        ["insert", "core", "ulabel", "--records", '{"name":"treated"}', "--compact"],
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

    monkeypatch.setattr("lamin_cli._rest._mutations.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        [
            "upsert",
            "core",
            "ulabel",
            "--conflict-column",
            "name",
            "--records",
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

    monkeypatch.setattr("lamin_cli._rest._mutations.request_json", fake_request_json)

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

    monkeypatch.setattr("lamin_cli._rest._mutations.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        [
            "update",
            "core",
            "project",
            "--index-column",
            "uid",
            "--records",
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

    monkeypatch.setattr("lamin_cli._rest._mutations.request_json", fake_request_json)

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

    monkeypatch.setattr("lamin_cli._rest._mutations.request_json", fake_request_json)

    result = CliRunner().invoke(
        rest,
        [
            "delete",
            "core",
            "recordrecord",
            "--records",
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
