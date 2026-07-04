from __future__ import annotations

from lamin_cli.hub.branches import create_branch, list_branches


def test_list_branches_constructs_request(monkeypatch):
    calls = []
    header_calls = []
    print_calls = []

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
    monkeypatch.setattr(
        "lamin_cli.hub.branches._pretty_print_json_list_header",
        lambda columns, **kwargs: header_calls.append((columns, kwargs)),
    )

    def fake_pretty_print_json_list(rows, *, show_header=True, column_widths=None):
        print_calls.append((rows, show_header, column_widths))

    monkeypatch.setattr(
        "lamin_cli.hub.branches._pretty_print_json_list",
        fake_pretty_print_json_list,
    )

    list_branches()

    assert calls == [
        (
            "post",
            "modules/core/branch",
            {"limit_to_many": 10, "limit": 20, "offset": 0},
            {
                "select": ["name", "created_at", "_status_code", "created_by(handle)"],
                "order_by": [{"field": "id", "descending": True}],
            },
        )
    ]
    assert header_calls == [
        (
            ["name", "change_request", "created_by", "created_at"],
            {
                "column_widths": {
                    "name": 26,
                    "change_request": 14,
                    "created_by": 12,
                    "created_at": 19,
                }
            },
        )
    ]
    assert print_calls == [
        (
            [
                {
                    "name": "main",
                    "change_request": "draft",
                    "created_by": "falexwolf",
                    "created_at": "2026-06-19 08:00:00",
                }
            ],
            False,
            {
                "name": 26,
                "change_request": 14,
                "created_by": 12,
                "created_at": 19,
            },
        )
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
