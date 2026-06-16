from __future__ import annotations

import pytest


@pytest.fixture
def schema_payload():
    def build():
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

    return build


@pytest.fixture
def patch_request_json(monkeypatch):
    def patch(target: str, handler):
        calls = []

        def fake_request_json(method, path, *, params=None, body=None):
            calls.append((method, path, params, body))
            return handler(method, path, params, body)

        monkeypatch.setattr(f"lamin_cli._rest.{target}.request_json", fake_request_json)
        return calls

    return patch


@pytest.fixture
def patch_schema_request_json(monkeypatch, patch_request_json):
    def patch(handler):
        calls = patch_request_json("_schema.utils", handler)
        monkeypatch.setattr(
            "lamin_cli._rest._schema.utils._current_schema_cache_path", lambda: None
        )
        return calls

    return patch


@pytest.fixture
def patch_statistics_request_json(monkeypatch):
    def patch(handler):
        calls = []

        def fake_request_json(method, path, *, params=None, body=None):
            calls.append((method, path, params, body))
            return handler(method, path, params, body)

        monkeypatch.setattr(
            "lamin_cli._rest._schema.utils.request_json", fake_request_json
        )
        monkeypatch.setattr(
            "lamin_cli._rest._statistics.request_json", fake_request_json
        )
        monkeypatch.setattr(
            "lamin_cli._rest._schema.utils._current_schema_cache_path", lambda: None
        )
        return calls

    return patch
