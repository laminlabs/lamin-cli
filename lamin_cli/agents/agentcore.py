"""Track the public conversation supplied by LaminHub's AgentCore runtime.

The runtime refreshes this in-memory snapshot before each Python tool call.
There is no log discovery, HTTP client or second transcript recorder here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import click

from lamin_cli.agents import _common

_TRANSFORM_KEY = "__agentcore__"
_TRANSFORM_UID = "AgntCoreChat0000"


@dataclass(frozen=True)
class _Context:
    session_id: str
    instance_id: str
    entries: list[dict]


_context: _Context | None = None


def normalize_messages(messages: list[dict]) -> list[dict]:
    """Convert Hub's public message strings to the shared renderer's format."""
    return [
        {"role": message["role"], "content": [{"type": "text", "text": text}]}
        for message in messages
        if message.get("role") in {"user", "assistant"}
        and isinstance(text := message.get("content"), str)
        and text.strip()
    ]


def set_context(*, session_id: str, instance_id: str, messages: list[dict]) -> None:
    """Supply a snapshot from the host, before executing the current tool."""
    global _context
    _context = _Context(
        str(UUID(session_id)), str(UUID(instance_id)), normalize_messages(messages)
    )


def clear_context() -> None:
    """Release turn-local context along with the worker's credentials."""
    global _context
    _context = None
    os.environ.pop("LAMIN_INITIATED_BY_RUN_UID", None)


def has_context() -> bool:
    return _context is not None


def _connected_context(ln) -> _Context:
    if _context is None:
        raise click.ClickException(
            "AgentCore tracking requires a LaminHub runtime context."
        )
    if not _common.instance_connected(ln):
        raise click.ClickException("No LaminDB instance connected.")
    if str(ln.setup.settings.instance._id) != _context.instance_id:
        raise click.ClickException("AgentCore context belongs to a different instance.")
    return _context


def _run_uid(context: _Context) -> str:
    # A stable DB identity survives worker/VM expiry without another state store.
    return uuid5(
        NAMESPACE_URL, f"lamin-agentcore:{context.instance_id}:{context.session_id}"
    ).hex[:20]


def get_agentcore_run():
    """Return this chat's Run, including on a later turn or worker restart."""
    import lamindb as ln

    context = _connected_context(ln)
    run = ln.Run.filter(uid=_run_uid(context)).one_or_none()
    if run is not None and (
        run.transform.uid != _TRANSFORM_UID
        or run.reference != context.session_id
        or run.reference_type != "agentcore"
    ):
        raise click.ClickException("AgentCore Run identity does not match this chat.")
    return run


def track_agentcore_session(name: str) -> None:
    import lamindb as ln
    from django.db import IntegrityError

    context = _connected_context(ln)
    run = get_agentcore_run()
    if run is None:
        transform, _ = ln.Transform.objects.get_or_create(
            uid=_TRANSFORM_UID,
            defaults={
                "key": _TRANSFORM_KEY,
                "kind": "function",
                "description": "An AgentCore chat.",
            },
        )
        run = ln.Run(
            transform,
            name=name,
            status="started",
            reference=context.session_id,
            reference_type="agentcore",
        )
        run.uid = _run_uid(context)
        try:
            run.save()
        except IntegrityError:
            # Reconcile a concurrent/retried creation using the DB's unique UID.
            run = get_agentcore_run()
            if run is None:
                raise
    if run.finished_at is not None:
        run._status_code = -2  # restarted, as for other harnesses
        run.finished_at = None
        run.save()
    os.environ["LAMIN_INITIATED_BY_RUN_UID"] = run.uid
    _common.info(f"tracking AgentCore session: {run.uid}")


def finish_agentcore_session() -> None:
    import lamindb as ln

    context = _connected_context(ln)
    run = get_agentcore_run()
    if run is None:
        raise click.ClickException("Run lamin track agentcore --name <task> first.")
    # The snapshot precedes this tool, so do not wait for a finish command in it.
    html_doc = _common.render_transcript_html(
        context.entries,
        is_bookkeeping_bash_cmd=lambda _: False,
        skill_marker=None,
        shell_tool_names=frozenset(),
    )
    _common.save_or_replace_report(
        run,
        html_doc,
        context.entries,
        description="AgentCore session conversation (rendered)",
        ln=ln,
    )
    _common.stamp_transforms(run, context.entries, ln)
    run._status_code = 0
    run.finished_at = datetime.now(UTC)
    run.save()
    os.environ.pop("LAMIN_INITIATED_BY_RUN_UID", None)
    _common.info(f"finished tracking AgentCore session: {run.uid}")
