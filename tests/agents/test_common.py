import time

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


def test_wait_for_finish_invocation_retries_until_found():
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
        read_fn, is_done_fn, budget_seconds=3.0, poll_interval_seconds=0.05
    )
    elapsed = time.monotonic() - start

    assert is_done_fn(result)
    assert calls["n"] == 3
    assert elapsed < 1.0  # resolved quickly, not the full budget


def test_wait_for_finish_invocation_gives_up_gracefully():
    def read_fn():
        return [_bash_entry("echo hi")]

    def is_done_fn(entries):
        return _common.contains_finish_invocation(entries, _SHELL_TOOL_NAMES)

    start = time.monotonic()
    result = _common.wait_for_finish_invocation(
        read_fn, is_done_fn, budget_seconds=0.3, poll_interval_seconds=0.1
    )
    elapsed = time.monotonic() - start

    assert not is_done_fn(result)  # never found it
    assert 0.25 < elapsed < 0.8  # honored the budget, didn't hang


def test_wait_for_finish_invocation_returns_immediately_if_already_done():
    calls = {"n": 0}

    def read_fn():
        calls["n"] += 1
        return [_bash_entry("lamin finish")]

    start = time.monotonic()
    _common.wait_for_finish_invocation(
        read_fn,
        lambda entries: _common.contains_finish_invocation(entries, _SHELL_TOOL_NAMES),
        budget_seconds=5.0,
        poll_interval_seconds=0.3,
    )
    elapsed = time.monotonic() - start

    assert calls["n"] == 1
    assert elapsed < 0.1
