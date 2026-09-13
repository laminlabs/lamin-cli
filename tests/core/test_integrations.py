from __future__ import annotations

from click.testing import CliRunner
from lamin_cli.__main__ import main


def test_notion_sync_forwards_args(monkeypatch):
    calls: dict[str, object] = {}

    class DummyReport:
        def as_dict(self):
            return {"created": 1, "updated": 2}

    def fake_sync_from_notion(*, parents, token=None, apply=False, limit=None):
        calls["token"] = token
        calls["parents"] = parents
        calls["apply"] = apply
        calls["limit"] = limit
        return DummyReport()

    monkeypatch.setattr(
        "lamindb.integrations.notion.sync_from_notion", fake_sync_from_notion
    )
    result = CliRunner().invoke(
        main,
        [
            "integrations",
            "notion",
            "sync",
            "page-a",
            "db-b",
            "--token",
            "token-123",
            "--apply",
            "--limit",
            "5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == {
        "token": "token-123",
        "parents": ["page-a", "db-b"],
        "apply": True,
        "limit": 5,
    }
    assert result.output == ""


def test_notion_sync_defaults_to_dry_run(monkeypatch):
    calls: dict[str, object] = {}

    class DummyReport:
        def as_dict(self):
            return {"created": 1, "updated": 2}

    def fake_sync_from_notion(*, parents, token=None, apply=False, limit=None):
        calls["token"] = token
        calls["parents"] = parents
        calls["apply"] = apply
        calls["limit"] = limit
        return DummyReport()

    monkeypatch.setattr(
        "lamindb.integrations.notion.sync_from_notion", fake_sync_from_notion
    )
    result = CliRunner().invoke(main, ["integrations", "notion", "sync", "page-a"])

    assert result.exit_code == 0, result.output
    assert calls["apply"] is False


def test_notion_sync_accepts_zero_limit(monkeypatch):
    calls: dict[str, object] = {}

    class DummyReport:
        def as_dict(self):
            return {"created": 0, "updated": 0}

    def fake_sync_from_notion(*, parents, token=None, apply=False, limit=None):
        calls["parents"] = parents
        calls["limit"] = limit
        return DummyReport()

    monkeypatch.setattr(
        "lamindb.integrations.notion.sync_from_notion", fake_sync_from_notion
    )
    result = CliRunner().invoke(
        main, ["integrations", "notion", "sync", "page-a", "--limit", "0"]
    )

    assert result.exit_code == 0, result.output
    assert calls == {"parents": ["page-a"], "limit": 0}


def test_notion_sync_requires_parents():
    result = CliRunner().invoke(main, ["integrations", "notion", "sync"])

    assert result.exit_code != 0
    assert "PARENTS" in result.output


def test_notion_sync_reraises_as_click_exception(monkeypatch):
    def fake_sync_from_notion(*, parents, token=None, apply=False, limit=None):
        raise ValueError("No LaminDB record type named 'Website analytics'.")

    monkeypatch.setattr(
        "lamindb.integrations.notion.sync_from_notion", fake_sync_from_notion
    )
    result = CliRunner().invoke(main, ["integrations", "notion", "sync", "page-a"])

    assert result.exit_code != 0
    assert "No LaminDB record type named 'Website analytics'." in result.output
