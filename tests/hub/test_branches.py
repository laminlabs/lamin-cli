from __future__ import annotations

from lamin_cli.hub.branches import create_branch, list_branches


def test_list_branches_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return [
            {
                "name": "main",
                "created_at": "2026-06-19T08:00:00",
                "_status_code": 1,
                "created_by": {"handle": "falexwolf"},
            }
        ]

    monkeypatch.setattr("lamin_cli.hub.branches.request_json", fake_request_json)

    records = list_branches()

    assert calls == [
        (
            "post",
            "modules/core/branch",
            {"limit_to_many": 10, "limit": 100, "offset": 0},
            {
                "select": ["name", "created_at", "_status_code", "created_by(handle)"],
                "order_by": [{"field": "id", "descending": True}],
            },
        )
    ]
    assert records == [
        {
            "name": "main",
            "created_at": "2026-06-19 08:00:00",
            "change_request": "draft",
            "created_by": "falexwolf",
        }
    ]


def test_create_branch_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return [{"id": 1, "uid": "abc123", "name": "new", "description": None}]

    monkeypatch.setattr("lamin_cli.hub.branches.request_json", fake_request_json)

    result = create_branch("new")

    assert calls == [
        (
            "put",
            "modules/core/branch",
            None,
            {"name": "new", "description": None},
        )
    ]
    assert result == {"id": 1, "uid": "abc123", "name": "new", "description": None}
