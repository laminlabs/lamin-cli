import os
import time
from pathlib import Path

from lamin_cli.agents import _common

_SHELL_TOOL_NAMES = frozenset({"Bash"})


def _bash_entry(command: str) -> dict:
    return {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": command}}
        ],
    }


def _non_shell_tool_entry(tool_name: str, command_like_text: str) -> dict:
    return {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "t1",
                "name": tool_name,
                "input": {"command": command_like_text},
            }
        ],
    }


def _fresh_transcript_path(tmp_path: Path) -> Path:
    """A file that was just modified -- looks like an active, live session."""
    p = tmp_path / "session.jsonl"
    p.write_text("")
    return p


def _stale_transcript_path(tmp_path: Path) -> Path:
    """A file modified long ago -- looks like an abandoned/unrelated session."""
    p = tmp_path / "session.jsonl"
    p.write_text("")
    old = time.time() - _common._LIVENESS_WINDOW_SECONDS - 60
    os.utime(p, (old, old))
    return p


def test_is_finish_invocation_matches_bare_command():
    assert _common._is_finish_invocation("lamin finish")
    assert _common._is_finish_invocation("cd /some/dir && lamin finish")


def test_is_finish_invocation_matches_lamin_bin_fallback():
    assert _common._is_finish_invocation('"$LAMIN_BIN" finish')
    assert _common._is_finish_invocation("$LAMIN_BIN finish")


def test_is_finish_invocation_does_not_match_python_method_call():
    # a script being written or run may legitimately contain ln.finish() --
    # that's not evidence the session's own closing command was invoked.
    assert not _common._is_finish_invocation("import lamindb as ln\nln.track()\nln.finish()")


def test_is_finish_invocation_does_not_match_unrelated_text():
    assert not _common._is_finish_invocation("echo all done, task finished successfully")


def test_is_finish_invocation_does_not_match_quoted_search_string():
    """A real command that just happens to search for/mention the text --
    shlex keeps a quoted "lamin finish" as a single token, distinct from two
    adjacent unquoted ones, so this correctly isn't treated as an invocation."""
    assert not _common._is_finish_invocation('grep -rn "lamin finish" tests/')
    assert not _common._is_finish_invocation('echo "remember to run lamin finish"')


def test_is_finish_invocation_handles_unparseable_command():
    # unbalanced quote -- shlex.split raises ValueError; must not crash
    assert not _common._is_finish_invocation('echo "unterminated')


def test_contains_finish_invocation_true_for_real_bash_call():
    entries = [_bash_entry("lamin finish")]
    assert _common.contains_finish_invocation(entries, _SHELL_TOOL_NAMES)


def test_contains_finish_invocation_false_when_absent():
    entries = [_bash_entry("echo hi"), _bash_entry("python script.py")]
    assert not _common.contains_finish_invocation(entries, _SHELL_TOOL_NAMES)


def test_contains_finish_invocation_ignores_non_shell_tool_calls():
    """Real false positive found in production data: an apply_patch call
    writing a script whose *source code* contains `ln.finish()`, and a `view`
    call displaying skill documentation that mentions `lamin finish` as an
    example -- neither is the session's own closing command being invoked."""
    entries = [
        _non_shell_tool_entry("apply_patch", "*** Add File: s.py\n+ln.finish()\n"),
        _non_shell_tool_entry("view", "... run this now: lamin finish ..."),
    ]
    assert not _common.contains_finish_invocation(entries, _SHELL_TOOL_NAMES)


def test_contains_finish_invocation_ignores_plain_text_mentions():
    entries = [
        {"role": "user", "content": "please run lamin finish when you're done"},
    ]
    assert not _common.contains_finish_invocation(entries, _SHELL_TOOL_NAMES)


def test_contains_finish_invocation_ignores_genuine_old_invocation_outside_window():
    """A real earlier `lamin finish` call (e.g. a stray retry from much
    earlier in the session) shouldn't count as evidence *this* closing
    command has landed -- only the most recent entries matter."""
    entries = [_bash_entry("lamin finish")] + [
        _bash_entry(f"echo step {i}") for i in range(10)
    ]
    assert not _common.contains_finish_invocation(entries, _SHELL_TOOL_NAMES)


def test_contains_finish_invocation_finds_it_within_recent_window():
    entries = [_bash_entry(f"echo step {i}") for i in range(10)] + [
        _bash_entry("lamin finish")
    ]
    assert _common.contains_finish_invocation(entries, _SHELL_TOOL_NAMES)


def test_wait_for_finish_invocation_retries_until_found(tmp_path):
    calls = {"n": 0}

    def read_fn():
        calls["n"] += 1
        if calls["n"] < 3:
            return [_bash_entry("echo hi")]
        return [_bash_entry("echo hi"), _bash_entry("lamin finish")]

    def is_done_fn(entries):
        return _common.contains_finish_invocation(entries, _SHELL_TOOL_NAMES)

    start = time.monotonic()
    result = _common.wait_for_finish_invocation(
        read_fn,
        is_done_fn,
        transcript_path=_fresh_transcript_path(tmp_path),
        budget_seconds=3.0,
        poll_interval_seconds=0.05,
    )
    elapsed = time.monotonic() - start

    assert is_done_fn(result)
    assert calls["n"] == 3
    assert elapsed < 1.0  # resolved quickly, not the full budget


def test_wait_for_finish_invocation_gives_up_gracefully(tmp_path):
    def read_fn():
        return [_bash_entry("echo hi")]

    def is_done_fn(entries):
        return _common.contains_finish_invocation(entries, _SHELL_TOOL_NAMES)

    start = time.monotonic()
    result = _common.wait_for_finish_invocation(
        read_fn,
        is_done_fn,
        transcript_path=_fresh_transcript_path(tmp_path),
        budget_seconds=0.3,
        poll_interval_seconds=0.1,
    )
    elapsed = time.monotonic() - start

    assert not is_done_fn(result)  # never found it
    assert 0.25 < elapsed < 0.8  # honored the budget, didn't hang


def test_wait_for_finish_invocation_returns_immediately_if_already_done(tmp_path):
    calls = {"n": 0}

    def read_fn():
        calls["n"] += 1
        return [_bash_entry("lamin finish")]

    start = time.monotonic()
    _common.wait_for_finish_invocation(
        read_fn,
        lambda entries: _common.contains_finish_invocation(entries, _SHELL_TOOL_NAMES),
        transcript_path=_fresh_transcript_path(tmp_path),
        budget_seconds=5.0,
        poll_interval_seconds=0.3,
    )
    elapsed = time.monotonic() - start

    assert calls["n"] == 1
    assert elapsed < 0.1


def test_wait_for_finish_invocation_skips_wait_for_stale_transcript(tmp_path):
    """A transcript untouched for a long time isn't a live session (e.g.
    `lamin finish` run manually to clean up after a crash) -- must read once
    and return immediately, not burn the full budget on a doomed wait."""
    calls = {"n": 0}

    def read_fn():
        calls["n"] += 1
        return [_bash_entry("echo hi")]  # never matches -- would time out if waited

    start = time.monotonic()
    _common.wait_for_finish_invocation(
        read_fn,
        lambda entries: _common.contains_finish_invocation(entries, _SHELL_TOOL_NAMES),
        transcript_path=_stale_transcript_path(tmp_path),
        budget_seconds=5.0,
        poll_interval_seconds=0.1,
    )
    elapsed = time.monotonic() - start

    assert calls["n"] == 1
    assert elapsed < 0.5


def test_wait_for_finish_invocation_missing_transcript_file_does_not_wait(tmp_path):
    # stat() on a nonexistent path raises OSError -- must be treated as "not
    # live" (safe default) rather than crashing or waiting the full budget.
    missing_path = tmp_path / "does-not-exist.jsonl"
    calls = {"n": 0}

    def read_fn():
        calls["n"] += 1
        return []

    start = time.monotonic()
    _common.wait_for_finish_invocation(
        read_fn,
        lambda entries: False,
        transcript_path=missing_path,
        budget_seconds=5.0,
        poll_interval_seconds=0.1,
    )
    elapsed = time.monotonic() - start

    assert calls["n"] == 1
    assert elapsed < 0.5
