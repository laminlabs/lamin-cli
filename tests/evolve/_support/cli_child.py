"""Run `lamin evolve` in this process, with the faults the test asked for.

The `run_cli` fixture (see `../conftest.py`) launches this file as a child
process rather than invoking the Click app in the test process, for two reasons:

* A command that reaches an instance has to import its plans *before* lamindb
  connects, and the test process is already connected through the `instance`
  fixture.  A child connects to the same SQLite instance by slug, exactly the
  way a user's shell does.
* `sys.addaudithook` cannot be undone.  Installed in the test process, a hook
  would outlive the test that wanted it; installed here, it dies with the one
  command it was meant for.

`LAMIN_SCHEMAS_TEST_FAULTS` holds a JSON list of faults.  Each names an audit
event (`sqlite3.connect`, `urllib.Request`, `subprocess.Popen`, ...), an
optional substring one of the event's arguments must contain, and the builtin
exception to raise -- which then surfaces from the very call that raised the
event, as if the database, network or tool had failed for real.

Independently of those, a network guard refuses every connection that does not
stay on this machine, so no command can quietly depend on the Lamin hub.
"""

from __future__ import annotations

import builtins
import ipaddress
import json
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

FAULTS_ENV_VAR = "LAMIN_SCHEMAS_TEST_FAULTS"


def _is_local_host(host: object) -> bool:
    if host is None:
        return True  # getaddrinfo(None, ...) resolves the local machine
    name = host.decode() if isinstance(host, bytes) else str(host)
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _audit_hook(
    faults: list[dict[str, str]],
) -> Callable[[str, tuple[object, ...]], None]:
    def hook(event: str, args: tuple[object, ...]) -> None:
        if event == "socket.getaddrinfo" and not _is_local_host(args[0]):
            raise OSError(f"name resolution refused in tests: {args[0]}")
        # AF_UNIX sockets connect to a path, not a (host, port, ...) tuple.
        address = args[1] if event == "socket.connect" else None
        if isinstance(address, tuple) and not _is_local_host(address[0]):
            raise ConnectionRefusedError(f"network access refused in tests: {address}")
        for fault in faults:
            if event != fault["event"]:
                continue
            needle = fault.get("match")
            if needle is not None and not any(needle in str(arg) for arg in args):
                continue
            error = getattr(builtins, fault.get("raise", "OSError"))
            raise error(fault.get("message", f"injected fault on {event}"))

    return hook


def main() -> None:
    sys.addaudithook(_audit_hook(json.loads(os.environ.get(FAULTS_ENV_VAR, "[]"))))
    if os.environ.get("COVERAGE_PROCESS_START"):
        import coverage

        coverage.process_startup()

    import runpy
    from pathlib import Path

    if not os.environ.get("LAMIN_SCHEMAS_TEST_ROOT"):
        sys.argv[1:1] = ["evolve"]
    runpy.run_module("lamin_cli", run_name="__main__")


if __name__ == "__main__":
    main()
