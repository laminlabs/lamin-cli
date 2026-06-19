from __future__ import annotations

import json

from click.testing import CliRunner
from lamin_cli.hub import hub


def test_rest_schema_raw_scopes_response(patch_schema_request_json, schema_payload):
    def handler(method, path, params, body):
        return schema_payload()

    calls = patch_schema_request_json(handler)

    result = CliRunner().invoke(
        hub, ["schema", "core", "artifact", "--raw", "--compact"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == schema_payload()["core"]["artifact"]
    assert calls == [("get", "schema", None, None)]


def test_rest_schema_markdown_summary(patch_schema_request_json, schema_payload):
    def handler(method, path, params, body):
        return schema_payload()

    patch_schema_request_json(handler)

    result = CliRunner().invoke(hub, ["schema", "core", "artifact"])

    assert result.exit_code == 0, result.output
    assert "# Schema: core.artifact" in result.output
    assert "- class: Artifact" in result.output
    assert "- table: lamindb_artifact" in result.output
    assert "- fields: 1 scalar, 1 relations, 1 hidden (3 total)" in result.output
    assert "- uid: string" in result.output
    assert "- created_by -> core.user (many-to-one)" in result.output
    assert "select `" not in result.output
    assert "_aux" not in result.output


def test_rest_schema_models_matches_each_module_output(
    patch_schema_request_json, schema_payload
):
    def handler(method, path, params, body):
        return schema_payload()

    patch_schema_request_json(handler)

    all_models = CliRunner().invoke(hub, ["schema", "--models"])
    core = CliRunner().invoke(hub, ["schema", "core"])
    bionty = CliRunner().invoke(hub, ["schema", "bionty"])

    assert all_models.exit_code == 0, all_models.output
    assert core.exit_code == 0, core.output
    assert bionty.exit_code == 0, bionty.output
    assert all_models.output == f"{bionty.output}\n{core.output}"


def test_rest_schema_models_rejects_raw(patch_schema_request_json):
    patch_schema_request_json(lambda *args: {})

    result = CliRunner().invoke(hub, ["schema", "--models", "--raw"])

    assert result.exit_code != 0
    assert "--models cannot be combined with --raw." in result.output


def test_rest_schema_json_summary_includes_hidden(
    patch_schema_request_json, schema_payload
):
    def handler(method, path, params, body):
        return schema_payload()

    patch_schema_request_json(handler)

    result = CliRunner().invoke(
        hub,
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


def test_rest_schema_uses_cache(monkeypatch, tmp_path, schema_payload):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return schema_payload()

    monkeypatch.setattr("lamin_cli.hub._schema.utils.request_json", fake_request_json)
    monkeypatch.setattr(
        "lamin_cli.hub._schema.utils._current_instance", lambda: ("inst/1", "")
    )
    monkeypatch.setattr(
        "lamin_cli.hub._schema.utils._current_instance_schema_id",
        lambda: "schema:1",
    )
    monkeypatch.setenv("LAMIN_REST_SCHEMA_CACHE_DIR", str(tmp_path))

    first = CliRunner().invoke(hub, ["schema", "--format", "json", "--compact"])
    second = CliRunner().invoke(hub, ["schema", "--format", "json", "--compact"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert calls == [("get", "schema", None, None)]
    assert json.loads(first.output) == json.loads(second.output)


def test_rest_schema_refresh_bypasses_cache(monkeypatch, tmp_path, schema_payload):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        schema = schema_payload()
        schema["new"] = {}
        return schema

    monkeypatch.setattr("lamin_cli.hub._schema.utils.request_json", fake_request_json)
    monkeypatch.setattr(
        "lamin_cli.hub._schema.utils._current_instance", lambda: ("inst/1", "")
    )
    monkeypatch.setattr(
        "lamin_cli.hub._schema.utils._current_instance_schema_id",
        lambda: "schema:1",
    )
    monkeypatch.setenv("LAMIN_REST_SCHEMA_CACHE_DIR", str(tmp_path))

    cached = tmp_path / "hub" / "schemas" / "inst_1" / "schema_1.json"
    cached.parent.mkdir(parents=True)
    cached.write_text(json.dumps(schema_payload()))

    result = CliRunner().invoke(
        hub, ["schema", "--refresh", "--format", "json", "--compact"]
    )

    assert result.exit_code == 0, result.output
    assert calls == [("get", "schema", None, None)]
    assert any(
        module["module"] == "new" for module in json.loads(result.output)["modules"]
    )
