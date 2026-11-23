from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence


# Isolate settings before the native package is imported during collection.
import shutil
import tempfile
from types import SimpleNamespace
from uuid import UUID

_settings_dir = tempfile.TemporaryDirectory(prefix="schema-evolution-tests-")
os.environ["LAMIN_SETTINGS_DIR"] = _settings_dir.name
os.environ.pop("LAMIN_CURRENT_INSTANCE", None)
os.environ.pop("LAMIN_API_KEY", None)
os.environ["LAMIN_ENV"] = "pytest"

from lamindb_setup import settings as _settings
from lamindb_setup.core import InstanceSettings

_offline = InstanceSettings(
    id=UUID(int=0), owner="none", name="none", storage=None, modules="bionty,pertdb"
)
_settings._instance_settings = _offline

# Keep database tests offline, including the process that creates the instance.
# CLI children independently install the same audit guard.
import runpy

_test_child = runpy.run_path(str(Path(__file__).parent / "_support" / "cli_child.py"))
sys.addaudithook(_test_child["_audit_hook"]([]))


@pytest.fixture(scope="session")
def _database(tmp_path_factory, lamin_hub):
    import lamindb_setup as setup

    root = tmp_path_factory.mktemp("schema-evolution-instance")
    os.environ["SUPABASE_API_URL"] = lamin_hub
    os.environ["SUPABASE_ANON_KEY"] = "pytest"
    os.environ["LAMIN_CACHE_DIR"] = str(root / "cache")
    previous = Path.cwd()
    try:
        os.chdir(root)
        setup.init(
            storage=root / "storage",
            name="schema-evolution-tests",
            modules="bionty,pertdb",
        )
    finally:
        os.chdir(previous)
    import lamindb as ln
    from django.db import connections

    isettings = setup.settings.instance
    path = Path(connections["default"].settings_dict["NAME"])
    connections.close_all()
    backup = root / "baseline.sqlite"
    shutil.copy2(path, backup)
    return SimpleNamespace(settings=isettings, path=path, backup=backup, ln=ln)


@pytest.fixture
def instance(_database):
    from django.db import connections

    connections.close_all()
    shutil.copy2(_database.backup, _database.path)
    _settings._instance_settings = _database.settings
    try:
        yield SimpleNamespace(slug=_database.settings.slug, path=_database.path)
    finally:
        connections.close_all()
        _settings._instance_settings = _offline


@pytest.fixture
def ln(instance, _database):
    return _database.ln


@pytest.fixture
def bt(instance):
    import bionty

    return bionty


@pytest.fixture
def pt(instance):
    import pertdb

    return pertdb


class _HubHandler(http.server.BaseHTTPRequestHandler):
    """Answer every hub query with an empty result: nothing is on the hub."""

    def _empty(self) -> None:
        body = b"[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PATCH = do_DELETE = _empty

    def log_message(self, format: str, *args: object) -> None:
        """Silence the per-request logging to stderr."""


@pytest.fixture(scope="session")
def lamin_hub() -> Iterator[str]:
    """Return the URL of a stand-in for the Lamin hub.

    lamindb asks the hub whether an instance is registered there before it
    saves a run's artifacts -- which `apply` does when it tracks the run -- so
    a child would otherwise need the network.  A local, anonymous test instance
    never is registered, and this answers exactly that.
    """
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


_CLI_CHILD = Path(__file__).resolve().parent / "_support" / "cli_child.py"

# Non-production settings direct CLI children at the loopback hub fixture.
_CHILD_LAMIN_ENV = "pytest"


def _mirror_lamin_settings() -> None:
    """Copy this worker's lamin settings into the namespace a child reads.

    The `instance` fixture writes the instance's connection settings here, so
    the mirror is refreshed before every run.
    """
    settings_dir = Path(os.environ["LAMIN_SETTINGS_DIR"]) / ".lamin"
    prefix = f"{_CHILD_LAMIN_ENV}--"
    for path in settings_dir.glob("*"):
        if path.is_file() and not path.name.startswith(prefix):
            (settings_dir / f"{prefix}{path.name}").write_bytes(path.read_bytes())


def _coverage_env(rootdir: Path) -> dict[str, str]:
    """Point a child's coverage at this run's config and data file.

    Both default to paths relative to the working directory, and a child
    usually runs somewhere under `tmp_path`.
    """
    start = os.environ.get("COVERAGE_PROCESS_START")
    if not start:
        return {}
    return {
        "COVERAGE_PROCESS_START": str((rootdir / start).resolve()),
        "COVERAGE_FILE": os.environ.get("COVERAGE_FILE", str(rootdir / ".coverage")),
    }


@pytest.fixture
def run_cli(
    pytestconfig: pytest.Config, lamin_hub: str, tmp_path: Path
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Return `run(*args, cwd=, input=, env=, faults=)` running `lamin evolve`.

    The child parses the arguments, imports the plans, connects to an instance
    by slug -- the `instance` fixture's --
    and exits with the command's exit code.  It runs in `tmp_path` unless
    given a `cwd`, so nothing it writes lands in the repo.

    `faults` is a list of `{"event", "match", "raise", "message"}` dicts, each
    becoming an audit hook in the child that fails the named operation, e.g.
    `{"event": "sqlite3.connect", "match": str(instance.path)}` makes the
    instance's database unreachable (see `_support/cli_child.py`).
    """
    rootdir = Path(pytestconfig.rootpath)

    def run(
        *args: str,
        cwd: Path | None = None,
        input: str | None = None,
        env: Mapping[str, str] | None = None,
        faults: Sequence[Mapping[str, Any]] = (),
    ) -> subprocess.CompletedProcess[str]:
        _mirror_lamin_settings()
        child_env = {
            **os.environ,
            "NO_COLOR": "1",
            "TERM": "dumb",
            "COLUMNS": "200",
            # The stand-ins below live on loopback; an ambient proxy cannot reach them.
            "no_proxy": "127.0.0.1,localhost",
            "NO_PROXY": "127.0.0.1,localhost",
            "LAMIN_ENV": _CHILD_LAMIN_ENV,
            "SUPABASE_API_URL": lamin_hub,
            "SUPABASE_ANON_KEY": "pytest",
            **_coverage_env(rootdir),
            # `cli_child.FAULTS_ENV_VAR`
            "LAMIN_SCHEMAS_TEST_FAULTS": json.dumps([dict(f) for f in faults]),
            **(env or {}),
        }
        return subprocess.run(
            [sys.executable, str(_CLI_CHILD), *args],
            cwd=tmp_path if cwd is None else cwd,
            input=input,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=300,
        )

    return run


class GitWorkspace:
    """A git repository whose `main` branch tracks a local bare `origin`."""

    def __init__(self, root: Path, origin: Path) -> None:
        self.root = root
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(origin)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "init", "-b", "main", str(root)], check=True, capture_output=True
        )
        self.git("remote", "add", "origin", str(origin))

    def git(self, *args: str) -> None:
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=test@example.com",
                "-c",
                "user.name=Test",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )

    def publish(self, files: Mapping[str, str]) -> list[Path]:
        """Write `files` (relative path to text), commit them and push them."""
        paths = []
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            paths.append(path)
        self.git("add", *files)
        self.git("commit", "-m", "publish")
        self.git("push", "-u", "origin", "main")
        return paths


@pytest.fixture
def git_workspace(tmp_path: Path) -> GitWorkspace:
    """A repository `apply` accepts plans from once they are published."""
    return GitWorkspace(tmp_path / "work", tmp_path / "origin.git")


@pytest.fixture
def instance_factory(instance, tmp_path):
    """Create additional local instances without changing the parent's connection."""

    def create(name: str):
        storage = tmp_path / name / "storage"
        script = """
import json
import runpy
import sys
from pathlib import Path

support, storage, name = sys.argv[1:]
helpers = runpy.run_path(support)
sys.addaudithook(helpers["_audit_hook"]([]))
import lamindb_setup as setup
setup.init(storage=Path(storage), name=name, modules="bionty,pertdb")
from django.db import connections
print(json.dumps({"slug": setup.settings.instance.slug,
                  "path": str(connections["default"].settings_dict["NAME"])}))
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(_CLI_CHILD),
                str(storage),
                f"schema-evolution-{name}",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        info = json.loads(result.stdout.strip().splitlines()[-1])
        return SimpleNamespace(slug=info["slug"], path=Path(info["path"]))

    return create
