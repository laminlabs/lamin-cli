import json
import os
from uuid import uuid4

import click
import lamindb as ln
import pytest
from click.testing import CliRunner
from lamin_cli.__main__ import main
from lamin_cli.agents import agentcore


@pytest.fixture
def chat(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    session_id = str(uuid4())

    def supply(messages, *, instance_id=None):
        agentcore.set_context(
            session_id=session_id,
            instance_id=instance_id or str(ln.setup.settings.instance._id),
            messages=messages,
        )

    supply([{"role": "user", "content": "Review this dataset"}])
    yield supply
    agentcore.clear_context()
    transform = ln.Transform.filter(uid=agentcore._TRANSFORM_UID).one_or_none()
    if transform:
        for run in transform.runs.all():
            report = run.report
            run.report = None
            run.save()
            run.delete(permanent=True)
            if report:
                report.delete(permanent=True)
        transform.delete(permanent=True)


def test_normalize_only_public_hub_text():
    messages = [
        {"role": "system", "content": "hidden system"},
        {"role": "user", "content": "saved text", "access_token": "secret"},
        {"role": "assistant", "content": "public reply"},
        {"role": "assistant", "content": " "},
        {
            "role": "assistant",
            "content": [
                {"reasoningContent": {"reasoningText": {"text": "private reasoning"}}},
                {
                    "toolUse": {
                        "name": "execute_python",
                        "input": {"code": "hidden tool"},
                    }
                },
            ],
        },
    ]
    entries = agentcore.normalize_messages(messages)
    assert entries == [
        {"role": "user", "content": [{"type": "text", "text": "saved text"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "public reply"}]},
    ]


def test_finish_stores_summary_once_and_reuses_run_after_context_restart(
    chat, monkeypatch
):
    runner = CliRunner()
    result = runner.invoke(main, ["track", "agentcore", "--name", "Review dataset"])
    assert result.exit_code == 0, result.output
    run = agentcore.get_agentcore_run()
    assert run.status == "started"
    assert run.uid == os.environ["LAMIN_INITIATED_BY_RUN_UID"]
    summary = (
        "Unicode café — <script>alert(1)</script>\n```python\nx = 1\n```\n"
        + "long summary " * 150
    )
    messages = [
        {"role": "user", "content": "Review this dataset"},
        {"role": "assistant", "content": summary},
    ]
    chat(messages)
    # Mutation of the caller's data must not change the supplied snapshot.
    messages[-1]["content"] = "not the snapshot"
    # Other desktop environment markers must not override the hosted context.
    monkeypatch.setenv("CODEX_THREAD_ID", "desktop-chat")
    result = runner.invoke(main, ["finish"])
    assert result.exit_code == 0, result.output
    run.refresh_from_db()
    assert run.status == "completed"
    assert run.report.n_files == 2
    entries = [
        json.loads(line)
        for line in (run.report.path / "trace.jsonl").read_text().splitlines()
    ]
    assert entries[-1]["content"][0]["text"] == summary
    html = (run.report.path / "index.html").read_text()
    assert html.count("Unicode café") == 1
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert ("long summary " * 150).rstrip() in html
    assert "not the snapshot" not in html and "lamin finish" not in html
    report_uid = run.report.uid

    agentcore.clear_context()  # simulate a worker/VM replacement; no local files
    chat(
        [
            {"role": "user", "content": "Follow-up"},
            {"role": "assistant", "content": "Second summary"},
        ]
    )
    agentcore.track_agentcore_session(name="Follow-up")
    assert agentcore.get_agentcore_run().uid == run.uid
    assert agentcore.get_agentcore_run().status == "restarted"
    agentcore.finish_agentcore_session()
    agentcore.finish_agentcore_session()  # retry does not create another report
    run.refresh_from_db()
    assert run.report.uid == report_uid
    assert ln.Run.filter(transform=run.transform).count() == 1
    assert "Second summary" in (run.report.path / "index.html").read_text()


def test_finish_failure_leaves_run_open_and_can_retry(chat, monkeypatch):
    agentcore.track_agentcore_session(name="Retry report")
    save = agentcore._common.save_or_replace_report

    def fail(*args, **kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(agentcore._common, "save_or_replace_report", fail)
    with pytest.raises(OSError, match="storage unavailable"):
        agentcore.finish_agentcore_session()
    run = agentcore.get_agentcore_run()
    assert run.status == "started" and run.report is None
    monkeypatch.setattr(agentcore._common, "save_or_replace_report", save)
    agentcore.finish_agentcore_session()
    assert agentcore.get_agentcore_run().status == "completed"


def test_missing_tracking_or_wrong_instance_fails(chat):
    with pytest.raises(click.ClickException, match="track agentcore"):
        agentcore.finish_agentcore_session()
    chat([], instance_id=str(uuid4()))
    with pytest.raises(click.ClickException, match="different instance"):
        agentcore.track_agentcore_session(name="wrong instance")
    agentcore.clear_context()
    with pytest.raises(click.ClickException, match="runtime context"):
        agentcore.track_agentcore_session(name="no context")


def test_session_and_instance_both_scope_run_identity():
    session, instance = str(uuid4()), str(uuid4())
    base = agentcore._Context(session, instance, [])
    assert agentcore._run_uid(base) != agentcore._run_uid(
        agentcore._Context(str(uuid4()), instance, [])
    )
    assert agentcore._run_uid(base) != agentcore._run_uid(
        agentcore._Context(session, str(uuid4()), [])
    )
