from __future__ import annotations

from click.testing import CliRunner
from lamin_cli.__main__ import main


def test_notion_sync_forwards_args(monkeypatch):
    calls: dict[str, object] = {}

    class DummyReport:
        def as_dict(self):
            return {"created": 1, "updated": 2}

    def fake_sync_objects_from_notion(*, parents, token=None, apply=False, depth=None):
        calls["token"] = token
        calls["parents"] = parents
        calls["apply"] = apply
        calls["depth"] = depth
        return DummyReport()

    monkeypatch.setattr(
        "lamindb.integrations.notion.sync_objects_from_notion",
        fake_sync_objects_from_notion,
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
            "--depth",
            "5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == {
        "token": "token-123",
        "parents": ["page-a", "db-b"],
        "apply": True,
        "depth": 5,
    }
    assert result.output == ""


def test_notion_sync_defaults_to_dry_run(monkeypatch):
    calls: dict[str, object] = {}

    class DummyReport:
        def as_dict(self):
            return {"created": 1, "updated": 2}

    def fake_sync_objects_from_notion(*, parents, token=None, apply=False, depth=None):
        calls["token"] = token
        calls["parents"] = parents
        calls["apply"] = apply
        calls["depth"] = depth
        return DummyReport()

    monkeypatch.setattr(
        "lamindb.integrations.notion.sync_objects_from_notion",
        fake_sync_objects_from_notion,
    )
    result = CliRunner().invoke(main, ["integrations", "notion", "sync", "page-a"])

    assert result.exit_code == 0, result.output
    assert calls["apply"] is False


def test_notion_sync_accepts_zero_depth(monkeypatch):
    calls: dict[str, object] = {}

    class DummyReport:
        def as_dict(self):
            return {"created": 0, "updated": 0}

    def fake_sync_objects_from_notion(*, parents, token=None, apply=False, depth=None):
        calls["parents"] = parents
        calls["depth"] = depth
        return DummyReport()

    monkeypatch.setattr(
        "lamindb.integrations.notion.sync_objects_from_notion",
        fake_sync_objects_from_notion,
    )
    result = CliRunner().invoke(
        main, ["integrations", "notion", "sync", "page-a", "--depth", "0"]
    )

    assert result.exit_code == 0, result.output
    assert calls == {"parents": ["page-a"], "depth": 0}


def test_transfer_url_forwards_args(monkeypatch):
    calls: dict[str, object] = {}

    def fake_sync_objects_from_database(
        registry, uids, *, source, depth=None, transfer=None
    ):
        calls["registry"] = registry
        calls["uids"] = uids
        calls["source"] = source
        calls["depth"] = depth
        calls["transfer"] = transfer
        return []

    monkeypatch.setattr(
        "lamindb.models.sync_objects_from_database",
        fake_sync_objects_from_database,
    )
    result = CliRunner().invoke(
        main,
        [
            "io",
            "transfer",
            "https://lamin.ai/laminlabs/lamindata/record/UrcIKR8v0ywim0pE",
            "--depth",
            "1",
            "--transfer",
            "annotations",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == {
        "registry": "record",
        "uids": "UrcIKR8v0ywim0pE",
        "source": "laminlabs/lamindata",
        "depth": 1,
        "transfer": "annotations",
    }


def test_transfer_entity_uid_forwards_args(monkeypatch):
    calls: dict[str, object] = {}

    def fake_sync_objects_from_database(
        registry, uids, *, source, depth=None, transfer=None
    ):
        calls["registry"] = registry
        calls["uids"] = uids
        calls["source"] = source
        calls["depth"] = depth
        calls["transfer"] = transfer
        return []

    monkeypatch.setattr(
        "lamindb.models.sync_objects_from_database",
        fake_sync_objects_from_database,
    )
    result = CliRunner().invoke(
        main,
        [
            "io",
            "transfer",
            "artifact",
            "--uid",
            "e2G7k9EVul4JbfsE",
            "--from",
            "laminlabs/lamindata",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["registry"] == "artifact"
    assert calls["uids"] == "e2G7k9EVul4JbfsE"
    assert calls["source"] == "laminlabs/lamindata"


def test_notion_sync_requires_parents():
    result = CliRunner().invoke(main, ["integrations", "notion", "sync"])

    assert result.exit_code != 0
    assert "PARENTS" in result.output
