from __future__ import annotations

from lamin_cli.hub.branches import list_branches


def test_list_branches_constructs_request(monkeypatch):
    calls = []

    def fake_request_json(method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        return [
            {
                "uid": "abc123",
                "name": "main",
                "description": "Main branch",
                "created_at": "2026-06-19T08:00:00",
            }
        ]

    monkeypatch.setattr("lamin_cli.hub.branches.request_json", fake_request_json)

    df = list_branches()

    assert calls == [
        (
            "post",
            "modules/core/branch",
            {"limit_to_many": 10, "limit": 100, "offset": 0},
            {
                "select": ["uid", "name", "description", "created_at"],
                "order_by": [{"field": "id", "descending": True}],
            },
        )
    ]
    assert list(df.columns) == ["uid", "name", "description", "created_at"]
    assert df.iloc[0].to_dict() == {
        "uid": "abc123",
        "name": "main",
        "description": "Main branch",
        "created_at": "2026-06-19T08:00:00",
    }
