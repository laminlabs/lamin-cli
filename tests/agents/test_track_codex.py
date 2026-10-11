import json
from pathlib import Path

import click
import lamindb as ln
import pytest
from lamin_cli.agents import codex

THREAD = "01a0aed8-1d24-7611-bef7-46496219561a"
OTHER = "01a0af3b-9222-7d11-9be5-d92a6e3babac"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", THREAD)
    monkeypatch.setattr(codex, "_codex_root", lambda: tmp_path / "codexhome")
    yield
    transform = ln.Transform.filter(key=codex._TRANSFORM_KEY).first()
    if transform is None:
        return
    for run in transform.runs.all():
        report = run.report
        if report is not None:
            run.report = None
            run.save()
        run.delete(permanent=True)
        if report is not None:
            report.delete(permanent=True)
    transform.delete(permanent=True)


def _event(item: dict) -> dict:
    return {"type": "event_msg", "payload": {"type": "item_completed", "item": item}}


def _write_rollout(path: Path, thread_id: str, timestamp: str, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {
            "type": "session_meta",
            "payload": {
                "id": thread_id,
                "session_id": thread_id,
                "timestamp": timestamp,
            },
        },
        *events,
    ]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))


def _sample_rollouts(root: Path) -> None:
    _write_rollout(
        root / "archived_sessions" / f"rollout-2026-09-17T12-09-53-{THREAD}.jsonl",
        THREAD,
        "2026-09-17T10:09:53.978Z",
        [
            _event(
                {
                    "type": "UserMessage",
                    "content": [{"type": "text", "text": "first question"}],
                }
            ),
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "secret developer prompt"}],
                },
            },
            {"type": "compacted", "payload": {"message": "old history should stay out"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": 10}},
                },
            },
        ],
    )
    _write_rollout(
        root
        / "sessions"
        / "2026"
        / "10"
        / "07"
        / f"rollout-2026-10-07T10-17-20-{THREAD}_{OTHER}.jsonl",
        THREAD,
        "2026-10-07T08:17:20.452Z",
        [
            _event(
                {
                    "type": "UserMessage",
                    "content": [{"type": "text", "text": "later question"}],
                }
            ),
            _event(
                {
                    "type": "Reasoning",
                    "summary_text": ["checking the routes"],
                    "raw_content": [],
                }
            ),
            _event(
                {
                    "type": "AgentMessage",
                    "content": [{"type": "Text", "text": "the route is fine"}],
                }
            ),
            _event(
                {
                    "type": "CommandExecution",
                    "id": "exec-1",
                    "command": ["/bin/zsh", "-lc", "pwd"],
                    "parsed_cmd": [{"type": "unknown", "cmd": "pwd"}],
                    "aggregated_output": "/work",
                }
            ),
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": 4}},
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": 7}},
                },
            },
            _event(
                {
                    "type": "CommandExecution",
                    "id": "exec-finish",
                    "command": ["/bin/zsh", "-lc", "lamin finish"],
                    "parsed_cmd": [{"type": "unknown", "cmd": "lamin finish"}],
                    "aggregated_output": "",
                }
            ),
        ],
    )
    _write_rollout(
        root / "sessions" / "2026" / "10" / "01" / f"rollout-2026-10-01T00-00-00-{OTHER}.jsonl",
        OTHER,
        "2026-10-01T00:00:00.000Z",
        [
            _event(
                {
                    "type": "UserMessage",
                    "content": [{"type": "text", "text": "other chat"}],
                }
            )
        ],
    )


def test_rollouts_join_in_time_order_without_hidden_text(tmp_path):
    root = tmp_path / "codexhome"
    _sample_rollouts(root)

    entries, counts, newest = codex._load_transcript(THREAD)

    texts = [
        block.get("text") or block.get("thinking") or block.get("input", {}).get("command")
        for entry in entries
        for block in entry["content"]
        if block.get("type") != "tool_result"
    ]
    assert texts == [
        "first question",
        "later question",
        "checking the routes",
        "the route is fine",
        "pwd",
        "lamin finish",
    ]
    assert counts == {"n_tokens": 17, "n_steps": 1, "n_tool_calls": 2}
    assert newest is not None
    assert newest.name.startswith("rollout-2026-10-07")
    rendered = "\n".join(json.dumps(entry) for entry in entries)
    assert "secret developer prompt" not in rendered
    assert "old history should stay out" not in rendered
    assert "other chat" not in rendered
    assert "/bin/zsh" not in rendered


def test_track_requires_thread_id(monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    with pytest.raises(click.ClickException, match="CODEX_THREAD_ID"):
        codex.track_codex_session(name="missing id")


def test_finish_report_spans_every_rollout_file(tmp_path):
    _sample_rollouts(tmp_path / "codexhome")
    codex.track_codex_session(name="join rollout files")
    uid = codex._run_uid_file(THREAD).read_text().strip()

    codex.finish_codex_session()

    assert not codex._run_uid_file(THREAD).exists()
    run = ln.Run.get(uid=uid)
    assert run.finished_at is not None
    report = run.report.path.read_text()
    assert report.index("first question") < report.index("later question")
    assert "checking the routes" in report
    assert "pwd" in report
    assert "/work" in report
    assert "secret developer prompt" not in report
    assert "other chat" not in report
    assert run.extra_data["n_tokens"] == 17
    assert run.extra_data["n_tool_calls"] == 2

    codex.track_codex_session(name="same chat again")
    assert codex._run_uid_file(THREAD).read_text().strip() == uid
    assert ln.Run.filter(uid=uid).one().finished_at is None
