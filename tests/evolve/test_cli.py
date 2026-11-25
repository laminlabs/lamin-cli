# Adapted source tests use deliberately invalid inputs and lightweight stand-ins.
# mypy: disable-error-code="arg-type"
"""Tests for lamin_cli.evolve._commands."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

_CLI_LAUNCHER = Path(__file__).resolve().parent / "_support" / "cli_child.py"

import pytest
from _support.output import command_output
from lamin_cli.evolve._commands import _prune_lock_after_destroy
from lamindb_setup.evolve import Feature, FeatureType, LaminDType
from lamindb_setup.evolve.authoring._plan import PlanContext, create_plan
from lamindb_setup.evolve.engine._connect import (
    CURRENT_INSTANCE_ENV_VAR,
    current_user_label,
    echo_current_user,
    no_default_instance,
    reset_user_announcement,
)
from lamindb_setup.evolve.engine._lock import LockEntry, LockFile, lock_path
from lamindb_setup.evolve.introspect._template import plan_slug

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _collapse_rich_output(text: str) -> str:
    plain = _ANSI_ESCAPE_RE.sub("", text)
    return " ".join(plain.replace("\u2502", " ").split())


_PLAN_CODE = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import Schema
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan

PLAN_TRANSFORM_STEM_UID = "testUID00001"


@create_plan
def my_plan(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("cli_ft")
    f = Feature("cli_col", dtype=LaminDType(str), type=ft)
    Schema(name="CliSchema", features=[f])
"""

_PLAN_CODE_NO_UID = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import Schema
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan


@create_plan
def my_plan(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("cli_ft")
    f = Feature("cli_col", dtype=LaminDType(str), type=ft)
    Schema(name="CliSchema", features=[f])
"""

_SECOND_PLAN_CODE = """

@create_plan
def other_plan(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("cli_ft2")
    f = Feature("cli_col2", dtype=LaminDType(str), type=ft)
    Schema(name="CliSchema2", features=[f])
"""


def test_lock_path_is_per_instance() -> None:
    assert lock_path(Path("dir/plan_x.py"), "acme-prod") == Path(
        "dir/plan_x.acme-prod.lock"
    )


def test_no_default_instance_masks_the_configured_instance() -> None:
    # The native package is already imported; simulate the pre-connection state.
    # Inside the context lamindb sees the empty 'none/none' instance instead of
    # whatever `lamin connect` selected, and the environment variable that
    # would override it is unset; both are restored on exit.
    import lamindb_setup as ln_setup

    before = ln_setup.settings._instance_settings
    os.environ[CURRENT_INSTANCE_ENV_VAR] = "acme/prod"
    try:
        with no_default_instance(setup=_FakeSetup()):
            assert ln_setup.settings.instance.slug == "none/none"
            assert ln_setup.settings.is_configured is False
            assert ln_setup.settings.instance.modules == {"bionty", "pertdb"}
            assert CURRENT_INSTANCE_ENV_VAR not in os.environ
        assert ln_setup.settings._instance_settings is before
        assert os.environ[CURRENT_INSTANCE_ENV_VAR] == "acme/prod"
    finally:
        os.environ.pop(CURRENT_INSTANCE_ENV_VAR, None)
        ln_setup.settings._instance_settings = before


def test_no_default_instance_restores_on_error() -> None:
    import lamindb_setup as ln_setup

    before = ln_setup.settings._instance_settings
    with pytest.raises(RuntimeError):
        with no_default_instance(setup=_FakeSetup()):
            raise RuntimeError("boom")
    assert ln_setup.settings._instance_settings is before


def test_no_default_instance_keeps_a_connection_opened_inside_it() -> None:
    # `plan` / `apply` connect while the context is open; that connection
    # must survive the context exit rather than be masked away again.
    import lamindb_setup as ln_setup
    from lamindb_setup.core import InstanceSettings

    before = ln_setup.settings._instance_settings
    connected = InstanceSettings(id=uuid.UUID(int=1), owner="acme", name="prod")
    try:
        with no_default_instance(setup=_FakeSetup()):
            ln_setup.settings._instance_settings = connected
        assert ln_setup.settings._instance_settings is connected
    finally:
        ln_setup.settings._instance_settings = before


class _FakeSetup:
    """A stand-in :class:`LaminSetup` that records what the connect path did."""

    def __init__(
        self, *, user: object = None, connected: bool = False, slug: str = "acme/prod"
    ) -> None:
        self._user = user
        self._connected = connected
        self._slug = slug
        self.modules: object = "original"
        self.calls: list[str] = []

    def user(self) -> object:
        self.calls.append("echoed")
        if isinstance(self._user, Exception):
            raise self._user
        return self._user

    def get_modules(self) -> object:
        return self.modules

    def set_modules(self, value: str) -> None:
        self.modules = value

    def connect(self, instance: str) -> None:
        self.calls.append("connected")

    def instance(self) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(slug=self._slug)

    def is_connected(self) -> bool:
        return self._connected


def test_no_default_instance_is_a_noop_once_connected() -> None:
    # Embedding a command in an already-connected process (as the fixture-backed
    # tests do) must not swap that instance out from under it.
    import lamindb_setup as ln_setup

    before = ln_setup.settings._instance_settings
    with no_default_instance(setup=_FakeSetup(connected=True)):
        assert ln_setup.settings._instance_settings is before


def test_current_user_label_reports_handle_and_name() -> None:
    from types import SimpleNamespace

    user = SimpleNamespace(handle="lbot", name="Lamin Bot")
    assert current_user_label(setup=_FakeSetup(user=user)) == "lbot (Lamin Bot)"

    anonymous = SimpleNamespace(handle="lbot", name="")
    assert current_user_label(setup=_FakeSetup(user=anonymous)) == "lbot"


def test_current_user_label_falls_back_when_not_logged_in() -> None:
    # `settings.user` raises when nobody is logged in; naming the account is
    # advisory, so that must not be what fails the command.
    failing = _FakeSetup(user=RuntimeError("Need to login, first: lamin login"))
    assert "lamin login" in current_user_label(setup=failing)


def test_echo_current_user_prints_once_per_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from types import SimpleNamespace

    setup = _FakeSetup(user=SimpleNamespace(handle="lbot", name=""))
    reset_user_announcement()

    echo_current_user(setup=setup)
    echo_current_user(setup=setup)

    # On stderr, so a Markdown or JSON stdout stays machine-readable, and only
    # once even though a run may connect to several instances.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("Lamin user: lbot") == 1

    reset_user_announcement()
    echo_current_user(setup=setup)
    assert "Lamin user: lbot" in capsys.readouterr().err


def test_safe_connect_announces_the_user() -> None:
    from types import SimpleNamespace

    from lamindb_setup.evolve.engine._connect import safe_connect

    setup = _FakeSetup(user=SimpleNamespace(handle="lbot", name=""))
    reset_user_announcement()

    assert safe_connect("acme/prod", setup=setup) == "acme-prod"
    # The announcement happens before connecting, so the account is named even
    # when the connection then fails for lack of access.
    assert setup.calls == ["echoed", "connected"]


def test_safe_connect_restores_modules_when_connect_fails() -> None:
    from lamindb_setup.evolve.engine._connect import safe_connect

    class _FailingSetup(_FakeSetup):
        def connect(self, instance: str) -> None:
            raise RuntimeError("connection failed")

    setup = _FailingSetup()
    reset_user_announcement()

    with pytest.raises(RuntimeError, match="connection failed"):
        safe_connect("acme/missing", modules="bionty", setup=setup)

    assert setup.modules == "original"


def test_cli_callback_resets_the_user_announcement(tmp_path: Path) -> None:
    # The announcement is per run, not per process: a second command invoked in
    # the same process announces the user again.
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app
    from lamindb_setup.evolve.engine import _connect

    _connect._user_announced = True
    result = CliRunner().invoke(app, ["init", "resetcheck", "-d", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert _connect._user_announced is False


def test_validate_command_renders_plan_offline(tmp_path: Path) -> None:
    # Run in a clean interpreter: plan construction requires that lamindb has
    # not been imported yet, which a subprocess guarantees.
    plan_file = tmp_path / "plan_cli.py"
    plan_file.write_text(_PLAN_CODE)

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "validate",
            f"{plan_file}:my_plan",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "CliSchema" in result.stdout
    assert "cli_ft" in result.stdout


def _write_fake_default_instance(home: Path, slug: str) -> None:
    """Configure `slug` as the default instance of a throwaway lamin home.

    Writes the settings file `lamin connect` would leave behind, so a
    subprocess pointed at `home` via `LAMIN_SETTINGS_DIR` behaves like a
    machine where the user has selected an instance.
    """
    owner, _, name = slug.partition("/")
    storage = home / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    settings_dir = home / ".lamin"
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / "current_instance.env").write_text(
        "\n".join(
            [
                f"lamindb_instance_owner={owner}",
                f"lamindb_instance_name={name}",
                f"lamindb_instance_storage_root={storage}",
                "lamindb_instance_storage_region=null",
                "lamindb_instance_db=null",
                "lamindb_instance_schema_str=null",
                f"lamindb_instance_id={uuid.UUID(int=7)}",
                "lamindb_instance_git_repo=null",
                "lamindb_instance_keep_artifacts_local=false",
            ]
        )
        + "\n"
    )


_PLAN_CODE_IMPORTING_LAMINDB = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan

PLAN_TRANSFORM_STEM_UID = "testUID00002"


@create_plan
def my_plan(ctx: PlanContext, exports: None) -> None:
    import lamindb as ln

    ft = FeatureType("cli_ft")
    Feature("cli_col", dtype=LaminDType(ln.ULabel), type=ft)
    print(f"OBSERVED_SLUG={ln.setup.settings.instance.slug}")
"""


def test_validate_does_not_connect_to_the_users_default_instance(
    tmp_path: Path,
) -> None:
    # A plan whose body imports lamindb -- which every categorical dtype needs --
    # used to auto-connect to whatever instance `lamin connect` had selected,
    # even though 'validate' is entirely offline. The default instance must be
    # masked for the run instead.
    home = tmp_path / "lamin_home"
    _write_fake_default_instance(home, "fake-owner/fake-instance")
    plan_file = tmp_path / "plan_cli.py"
    plan_file.write_text(_PLAN_CODE_IMPORTING_LAMINDB)

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "validate",
            f"{plan_file}:my_plan",
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "LAMIN_SETTINGS_DIR": str(home),
            # the env var takes precedence over the settings file, so mask both
            CURRENT_INSTANCE_ENV_VAR: "fake-owner/fake-instance",
        },
    )

    assert result.returncode == 0, result.stderr
    # the plan body ran (and imported lamindb) against no instance at all
    assert "OBSERVED_SLUG=none/none" in result.stdout
    assert "fake-instance" not in result.stdout + result.stderr
    assert "cli_col" in result.stdout


def test_validate_command_markdown_format(tmp_path: Path) -> None:
    # '--format markdown' prints a GitHub-flavored fragment and '--output'
    # additionally writes it to a file.
    plan_file = tmp_path / "plan_cli.py"
    plan_file.write_text(_PLAN_CODE)
    out_file = tmp_path / "summary.md"

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "validate",
            f"{plan_file}:my_plan",
            "--format",
            "markdown",
            "--output",
            str(out_file),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert command_output(result.stdout).lstrip().startswith("## Plan summary: my_plan")
    assert "**Schema** `CliSchema`" in result.stdout
    # the same Markdown is written to the output file
    written = out_file.read_text()
    assert "## Plan summary: my_plan" in written
    assert "**Schema** `CliSchema`" in written

    # With exactly one plan in the file, the ':plan_variable' suffix is optional.
    plan_file = tmp_path / "plan_cli.py"
    plan_file.write_text(_PLAN_CODE)

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "validate",
            str(plan_file),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "CliSchema" in result.stdout


def test_validate_command_rejects_ambiguous_plan(tmp_path: Path) -> None:
    # Two plans in the file: omitting the suffix is ambiguous and must fail.
    plan_file = tmp_path / "plan_cli.py"
    plan_file.write_text(_PLAN_CODE + _SECOND_PLAN_CODE)

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "validate",
            str(plan_file),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"},
    )

    assert result.returncode != 0
    assert "several plans" in result.stderr


def test_validate_command_all_plans_includes_every_plan(tmp_path: Path) -> None:
    # --all-plans renders every plan defined in the file, so the ambiguity that
    # would otherwise reject a two-plan file is resolved by including both.
    plan_file = tmp_path / "plan_cli.py"
    plan_file.write_text(_PLAN_CODE + _SECOND_PLAN_CODE)

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "validate",
            "--all-plans",
            str(plan_file),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "CliSchema" in result.stdout
    assert "CliSchema2" in result.stdout


def test_validate_command_all_plans_rejects_variable_suffix(tmp_path: Path) -> None:
    # --all-plans expands whole files, so an explicit ':plan_variable' suffix is
    # contradictory and must be rejected.
    plan_file = tmp_path / "plan_cli.py"
    plan_file.write_text(_PLAN_CODE + _SECOND_PLAN_CODE)

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "validate",
            "--all-plans",
            f"{plan_file}:my_plan",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"},
    )

    assert result.returncode != 0
    assert "--all-plans" in result.stderr

    # A plan imported from another module must not count towards inference: the
    # file defines exactly one plan, so the suffix stays optional.
    other = tmp_path / "plan_other.py"
    other.write_text(_PLAN_CODE.replace("my_plan", "other_plan"))
    plan_file = tmp_path / "plan_cli.py"
    plan_file.write_text(
        f"from plan_other import other_plan  # noqa: F401\n\n{_PLAN_CODE}"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "validate",
            str(plan_file),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    assert "CliSchema" in result.stdout


def test_validate_command_resolves_sibling_import(tmp_path: Path) -> None:
    # A plan that imports a sibling plan by module name resolves without any
    # PYTHONPATH setup: loading puts the plan's own directory on sys.path so
    # cross-plan imports (one plan reusing another's objects) work.
    other = tmp_path / "plan_other.py"
    other.write_text(_PLAN_CODE.replace("my_plan", "other_plan"))
    plan_file = tmp_path / "plan_cli.py"
    plan_file.write_text(
        "from plan_other import other_plan  # noqa: F401\n\n" + _PLAN_CODE
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "validate",
            str(plan_file),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "CliSchema" in result.stdout


def test_apply_command_requires_git_committed_plan(tmp_path: Path) -> None:
    # tmp_path is not inside a git repository, so apply must refuse before doing
    # any work (it never connects to the instance).
    plan_file = tmp_path / "plan_cli.py"
    plan_file.write_text(_PLAN_CODE)

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "apply",
            f"{plan_file}:my_plan",
            "--instance",
            "account/instance",
            "--yes",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "not inside a git repository" in result.stderr


def test_validate_command_resolves_import_from_project_root(tmp_path: Path) -> None:
    # A plan in one directory imports a plan in another by its full dotted path,
    # with nothing on PYTHONPATH: both are imported relative to the project
    # root, found here by walking up to the lamin_schemas.yml.
    (tmp_path / "lamin_schemas.yml").write_text(
        "instances:\n  - slug: a/b\n    plans: [plans/wetlab/plan_other.py]\n",
        encoding="utf-8",
    )
    other = tmp_path / "plans" / "wetlab" / "plan_other.py"
    other.parent.mkdir(parents=True)
    other.write_text(_PLAN_CODE.replace("my_plan", "other_plan"))
    plan_file = tmp_path / "plans" / "composite" / "plan_cli.py"
    plan_file.parent.mkdir(parents=True)
    plan_file.write_text(
        "from plans.wetlab.plan_other import other_plan  # noqa: F401\n\n" + _PLAN_CODE
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "validate",
            str(plan_file),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "CliSchema" in result.stdout


def test_validate_command_honors_project_root_option(tmp_path: Path) -> None:
    # Plans with no marker above them: --project-root names the directory their
    # module paths are relative to.
    other = tmp_path / "vendored" / "wetlab" / "plan_other.py"
    other.parent.mkdir(parents=True)
    other.write_text(_PLAN_CODE.replace("my_plan", "other_plan"))
    plan_file = tmp_path / "vendored" / "composite" / "plan_cli.py"
    plan_file.parent.mkdir(parents=True)
    plan_file.write_text(
        "from wetlab.plan_other import other_plan  # noqa: F401\n\n" + _PLAN_CODE
    )

    def run(*extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(_CLI_LAUNCHER),
                *extra,
                "validate",
                str(plan_file),
            ],
            capture_output=True,
            text=True,
        )

    assert run().returncode != 0
    result = run("--project-root", str(tmp_path / "vendored"))
    assert result.returncode == 0, result.stderr
    assert "CliSchema" in result.stdout


def test_plan_slug_normalizes_names() -> None:
    assert plan_slug("MyNewPlan") == "mynewplan"
    assert plan_slug("my new-plan!") == "my_new_plan"
    assert plan_slug("plan_sandbox") == "plan_sandbox"


def test_plan_slug_rejects_empty() -> None:
    with pytest.raises(Exception):  # noqa: B017 - preserve source assertion
        plan_slug("---")


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_CLI_LAUNCHER), *args],
        capture_output=True,
        text=True,
    )


def test_evolve_rejects_version_option() -> None:
    result = _run_cli("--version")

    assert result.returncode == 2, result.stderr
    assert "No such option" in result.stderr
    assert "--version" in result.stderr


def test_log_queries_option_is_accepted_by_an_offline_command(
    tmp_path: Path,
) -> None:
    # An offline command issues no statements, so the flag must be a silent
    # no-op: nothing logged, and no summary line either.
    plan_file = tmp_path / "plan_cli.py"
    plan_file.write_text(_PLAN_CODE)

    result = _run_cli("--log-queries", "validate", str(plan_file))

    assert result.returncode == 0, result.stderr
    assert "sql" not in result.stderr


def test_init_command_scaffolds_plan(tmp_path: Path) -> None:
    from lamindb_setup.evolve.engine._track import (
        PLAN_STEM_UID_VAR,
        is_valid_stem_uid,
    )

    result = _run_cli("init", "mynewplan", "--output-dir", str(tmp_path))

    assert result.returncode == 0, result.stderr
    plan_file = tmp_path / "plan_mynewplan.py"
    assert plan_file.exists()
    text = plan_file.read_text()
    assert f"{PLAN_STEM_UID_VAR} = " in text

    namespace: dict[str, object] = {}
    exec(compile(text, str(plan_file), "exec"), namespace)
    uid = namespace[PLAN_STEM_UID_VAR]
    assert is_valid_stem_uid(uid)
    assert "def mynewplan(" in text


def test_init_generated_plan_validates(tmp_path: Path) -> None:
    init_result = _run_cli("init", "sample", "--output-dir", str(tmp_path))
    assert init_result.returncode == 0, init_result.stderr

    plan_file = tmp_path / "plan_sample.py"
    result = _run_cli("validate", f"{plan_file}:sample")
    assert result.returncode == 0, result.stderr


def test_init_rich_scaffold_has_imports_and_examples(tmp_path: Path) -> None:
    result = _run_cli("init", "rich", "--output-dir", str(tmp_path))
    assert result.returncode == 0, result.stderr

    text = (tmp_path / "plan_rich.py").read_text()
    # All building-block imports are present at the top, ready to use.
    for name in (
        "Feature",
        "FeatureType",
        "LaminDType",
        "Record",
        "RecordType",
        "Schema",
        "SchemaType",
        "Sheet",
    ):
        assert f"from lamindb_setup.evolve import {name}" in text, name
    # lamindb / bionty are referenced only inside the (commented) body.
    assert "import lamindb as ln" in text
    assert "import bionty as bt" in text
    # Commented examples for the major building blocks are included.
    assert "# --- grouping types" in text
    assert "# --- composite (file-format) schemas" in text
    assert "# --- sharing objects between plans" in text
    assert 'slots={"obs": obs_schema, "var": var_schema}' in text


def test_init_minimal_scaffold_omits_examples(tmp_path: Path) -> None:
    result = _run_cli("init", "lean", "--minimal", "--output-dir", str(tmp_path))
    assert result.returncode == 0, result.stderr

    text = (tmp_path / "plan_lean.py").read_text()
    assert "def lean(" in text
    # The rich example tour is absent in minimal mode.
    assert "# --- grouping types" not in text
    assert "# --- sharing objects between plans" not in text
    assert "import bionty as bt" not in text
    # The minimal pointer comment is present.
    assert "Declare your schema as a SchemaModel" in text


def test_init_minimal_generated_plan_validates(tmp_path: Path) -> None:
    init_result = _run_cli("init", "lean", "--minimal", "--output-dir", str(tmp_path))
    assert init_result.returncode == 0, init_result.stderr

    plan_file = tmp_path / "plan_lean.py"
    result = _run_cli("validate", f"{plan_file}:lean")
    assert result.returncode == 0, result.stderr


def test_render_plan_rich_and_minimal_compile() -> None:
    from lamindb_setup.evolve.introspect._template import render_plan

    for minimal in (False, True):
        src = render_plan("demo", "demo", "abcdEF12abcd", minimal=minimal)
        namespace: dict[str, object] = {}
        exec(compile(src, "<demo>", "exec"), namespace)
        assert "demo" in namespace
        assert "__FUNC__" not in src
        assert "__DESCRIPTION__" not in src
        assert 'PLAN_TRANSFORM_STEM_UID = "abcdEF12abcd"' in src


def test_init_command_refuses_existing_file(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan_dup.py"
    plan_file.write_text("x = 1\n")

    result = _run_cli("init", "dup", "--output-dir", str(tmp_path))
    assert result.returncode == 1
    assert "already exists" in result.stderr


def test_validate_errors_when_stem_uid_missing(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan_no_uid.py"
    plan_file.write_text(_PLAN_CODE_NO_UID)

    result = _run_cli("validate", f"{plan_file}:my_plan")
    assert result.returncode == 1
    assert "does not define PLAN_TRANSFORM_STEM_UID" in result.stderr
    assert "utils add-uid" in result.stderr


def test_utils_add_uid_to_existing_plan(tmp_path: Path) -> None:
    from lamindb_setup.evolve.engine._track import (
        PLAN_STEM_UID_VAR,
        is_valid_stem_uid,
    )

    plan_file = tmp_path / "plan_existing.py"
    plan_file.write_text(_PLAN_CODE_NO_UID)

    result = _run_cli("utils", "add-uid", str(plan_file))
    assert result.returncode == 0, result.stderr

    text = plan_file.read_text()
    assert f"{PLAN_STEM_UID_VAR} = " in text
    # The variable is declared above the decorator.
    assert text.index(PLAN_STEM_UID_VAR) < text.index("@create_plan")

    namespace: dict[str, object] = {}
    exec(compile(text, str(plan_file), "exec"), namespace)
    assert is_valid_stem_uid(namespace[PLAN_STEM_UID_VAR])

    # The patched plan now validates.
    validate_result = _run_cli("validate", f"{plan_file}:my_plan")
    assert validate_result.returncode == 0, validate_result.stderr


def test_utils_add_uid_refuses_duplicate(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan_has_uid.py"
    plan_file.write_text(_PLAN_CODE)  # already declares the stem uid

    result = _run_cli("utils", "add-uid", str(plan_file))
    assert result.returncode == 1
    assert "already defines" in result.stderr


def test_init_prints_the_workflow_for_the_file_it_wrote(
    run_cli, tmp_path: Path
) -> None:
    # The hint names the file where -d put it, and walks the validate -> plan
    # -> commit and push -> apply flow 'apply' requires, in that order.
    result = run_cli("init", "newthing", "-d", "plans")

    assert result.returncode == 0, result.stderr
    plan_file = Path("plans") / "plan_newthing.py"
    assert (tmp_path / plan_file).exists()
    target = f"{plan_file}:newthing"
    lines = result.stdout.splitlines()
    assert f"Created plan: {plan_file}" in lines
    assert lines[-4:] == [
        "Next steps:",
        f"  lamin evolve validate {target}",
        f"  lamin evolve plan {target} -i <instance>",
        f"  commit and push it, then: lamin evolve apply {target} -i <instance>",
    ]

    # the hint is runnable as printed
    validated = run_cli("validate", target)
    assert validated.returncode == 0, validated.stderr


def test_a_plan_variable_not_found_lists_the_available_ones(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan_two.py"
    plan_file.write_text(
        _PLAN_CODE
        + "\n\n@create_plan\n"
        + "def child(ctx: PlanContext, exports: None) -> None:\n"
        + '    FeatureType("cli_child_ft")\n'
    )

    result = _run_cli("validate", f"{plan_file}:nope")

    assert result.returncode == 2
    output = _collapse_rich_output(result.stderr)
    assert "Invalid value: plan variable 'nope' not found in" in output
    assert "available: child, my_plan" in output


def _rendered_help_pages():
    """Yield `(path, command, rendered --help)` for every command in the CLI."""
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app

    def walk(command, path):
        yield path, command
        for name, sub in getattr(command, "commands", {}).items():
            yield from walk(sub, [*path, name])

    runner = CliRunner()
    for path, command in walk(app, []):
        result = runner.invoke(app, [*path, "--help"], env={"COLUMNS": "200"})
        assert result.exit_code == 0, (path, result.output)
        yield path, command, result.output


def test_no_help_page_shows_restructuredtext_markup() -> None:
    # Click prints a docstring as written: reST literals and roles would show
    # up verbatim, and a developer-only note belongs after a '\f' line.
    for path, _command, output in _rendered_help_pages():
        for markup in ("`", ":func:", ":class:", ":meth:", ":data:", ":attr:"):
            assert markup not in output, (" ".join(path), markup)


def test_no_help_text_is_swallowed_as_rich_markup() -> None:
    # Rich help can render in markup mode, where '[word ...]' is a style
    # tag: the text inside it silently disappears from the page.
    from rich.text import Text

    for path, command, _output in _rendered_help_pages():
        texts = [(command.help or "").partition("\f")[0]]
        texts += [getattr(param, "help", None) or "" for param in command.params]
        for text in texts:
            assert Text.from_markup(text).plain == text, (" ".join(path), text)


@pytest.mark.parametrize(
    "command",
    [
        ("repair", "dangling"),
        ("repair", "duplicates"),
        ("repair", "dtypes"),
        ("repair", "stranded-values"),
        ("repair", "sheet-schemas"),
        ("repair", "align-uids", "plan_x.py", "--source", "acc/src"),
    ],
    ids=lambda command: " ".join(command[:2]),
)
def test_repair_dry_run_cannot_be_combined_with_yes(command: tuple[str, ...]) -> None:
    # Refused before any instance is connected: there is none named acc/inst.
    result = _run_cli(*command, "-i", "acc/inst", "--dry-run", "--yes")

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    collapsed = _collapse_rich_output(result.stderr)
    assert "'--dry-run': cannot be combined with --yes" in collapsed


def test_utils_generate_stem_uid_prints_valid_uid() -> None:
    from lamindb_setup.evolve.engine._track import is_valid_stem_uid

    result = _run_cli("utils", "generate-stem-uid")

    assert result.returncode == 0, result.stderr
    uid = command_output(result.stdout).strip()
    assert is_valid_stem_uid(uid)


def test_utils_generate_stem_uid_count_yields_distinct_uids() -> None:
    from lamindb_setup.evolve.engine._track import is_valid_stem_uid

    result = _run_cli("utils", "generate-stem-uid", "--count", "5")

    assert result.returncode == 0, result.stderr
    uids = command_output(result.stdout).split()
    assert len(uids) == 5
    assert len(set(uids)) == 5
    assert all(is_valid_stem_uid(uid) for uid in uids)


def test_utils_generate_stem_uid_checks_every_given_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # --instance is repeatable: a plan applied to dev *and* prod needs a uid
    # that is unclaimed on both, so every one of them reaches the generator.
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app
    from lamindb_setup.evolve import engine

    recorded: dict[str, object] = {}

    def fake_generate_stem_uids(count: int = 1, **kwargs: object) -> list[str]:
        recorded.update(count=count, **kwargs)
        return ["fakeUID00001", "fakeUID00002"][:count]

    # the command reads the name off the engine front door, which binds it once
    # at import -- patching `engine._track` would not reach it
    monkeypatch.setattr(engine, "generate_stem_uids", fake_generate_stem_uids)

    result = CliRunner().invoke(
        app,
        ["utils", "generate-stem-uid", "-i", "acme/dev", "-i", "acme/prod", "-n", "2"],
    )

    assert result.exit_code == 0, result.output
    assert list(recorded["instances"]) == ["acme/dev", "acme/prod"]  # type: ignore[call-overload]
    assert recorded["count"] == 2
    assert result.stdout.split() == ["fakeUID00001", "fakeUID00002"]


def test_utils_add_uid_checks_every_given_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app
    from lamindb_setup.evolve.introspect import _template

    plan_file = tmp_path / "plan_multi_instance.py"
    plan_file.write_text(_PLAN_CODE_NO_UID)
    recorded: dict[str, object] = {}

    def fake_generate_stem_uids(count: int = 1, **kwargs: object) -> list[str]:
        recorded.update(kwargs)
        return ["fakeUID00001"]

    monkeypatch.setattr(_template, "generate_stem_uids", fake_generate_stem_uids)

    result = CliRunner().invoke(
        app, ["utils", "add-uid", str(plan_file), "-i", "acme/dev", "-i", "acme/prod"]
    )

    assert result.exit_code == 0, result.output
    assert list(recorded["instances"]) == ["acme/dev", "acme/prod"]  # type: ignore[call-overload]
    assert 'PLAN_TRANSFORM_STEM_UID = "fakeUID00001"' in plan_file.read_text()


# A monolithic plan declaring two independent schemas, and the two single-schema
# plans it splits into. The split is drift-free: together the two plans create
# exactly the same objects as the monolith.
_MONO_PLAN_CODE = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import Schema
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan


@create_plan
def mono(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("fp_ft")
    Schema(name="FpSchema", features=[Feature("fp_col", dtype=LaminDType(str), type=ft)])
    ft2 = FeatureType("fp_ft2")
    Schema(name="FpSchema2", features=[Feature("fp_col2", dtype=LaminDType(str), type=ft2)])
"""

_SPLIT_PLAN_A_CODE = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import Schema
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan


@create_plan
def part_a(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("fp_ft")
    Schema(name="FpSchema", features=[Feature("fp_col", dtype=LaminDType(str), type=ft)])
"""

_SPLIT_PLAN_B_CODE = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import Schema
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan


@create_plan
def part_b(ctx: PlanContext, exports: None) -> None:
    ft2 = FeatureType("fp_ft2")
    Schema(name="FpSchema2", features=[Feature("fp_col2", dtype=LaminDType(str), type=ft2)])
"""


def test_utils_fingerprint_capture_writes_json(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan_mono.py"
    plan_file.write_text(_MONO_PLAN_CODE)
    out = tmp_path / "baseline.json"

    result = _run_cli("utils", "fingerprint", str(plan_file), "-o", str(out))

    assert result.returncode == 0, result.stderr
    assert out.exists()
    data = json.loads(out.read_text())
    # Two feature types, two features, two schemas.
    assert sum(data.values()) == 6
    assert "Captured 6 objects" in result.stdout


def test_utils_fingerprint_capture_prints_json_without_output(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan_mono.py"
    plan_file.write_text(_MONO_PLAN_CODE)

    result = _run_cli("utils", "fingerprint", str(plan_file))

    assert result.returncode == 0, result.stderr
    data = json.loads(command_output(result.stdout))
    assert sum(data.values()) == 6


def test_utils_fingerprint_split_is_drift_free(tmp_path: Path) -> None:
    mono = tmp_path / "plan_mono.py"
    mono.write_text(_MONO_PLAN_CODE)
    baseline = tmp_path / "baseline.json"
    assert (
        _run_cli("utils", "fingerprint", str(mono), "-o", str(baseline)).returncode == 0
    )

    plan_a = tmp_path / "plan_a.py"
    plan_a.write_text(_SPLIT_PLAN_A_CODE)
    plan_b = tmp_path / "plan_b.py"
    plan_b.write_text(_SPLIT_PLAN_B_CODE)

    result = _run_cli(
        "utils", "fingerprint", str(plan_a), str(plan_b), "-b", str(baseline)
    )

    assert result.returncode == 0, result.stderr
    assert "IDENTICAL" in result.stdout


def test_utils_fingerprint_reports_drift(tmp_path: Path) -> None:
    mono = tmp_path / "plan_mono.py"
    mono.write_text(_MONO_PLAN_CODE)
    baseline = tmp_path / "baseline.json"
    _run_cli("utils", "fingerprint", str(mono), "-o", str(baseline))

    # Only one of the two schemas is reproduced -> the other is missing.
    plan_a = tmp_path / "plan_a.py"
    plan_a.write_text(_SPLIT_PLAN_A_CODE)

    result = _run_cli("utils", "fingerprint", str(plan_a), "-b", str(baseline))

    assert result.returncode == 1
    assert "DRIFT" in result.stdout
    assert "MISSING" in result.stdout


def test_utils_fingerprint_reports_changed_fields(tmp_path: Path) -> None:
    plan_a = tmp_path / "plan_a.py"
    plan_a.write_text(_SPLIT_PLAN_A_CODE)
    baseline = tmp_path / "baseline.json"
    _run_cli("utils", "fingerprint", str(plan_a), "-o", str(baseline))

    # Same objects, one changed property -> a field-level CHANGED report.
    changed = tmp_path / "plan_a_changed.py"
    changed.write_text(_SPLIT_PLAN_A_CODE.replace("LaminDType(str)", "LaminDType(int)"))

    result = _run_cli("utils", "fingerprint", str(changed), "-b", str(baseline))

    assert result.returncode == 1
    assert "CHANGED: FeatureType:fp_ft/Feature:fp_col" in result.stdout
    assert "dtype: 'str' -> 'int'" in result.stdout


# The same categorical feature written in both supported dtype notations: the
# object wrapper (serialized as the registry import path) and the annotation form
# (serialized as lamindb's canonical `cat[...]` string).
_CAT_OBJECT_FORM_PLAN = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan


@create_plan
def cat_object(ctx: PlanContext, exports: None) -> None:
    import lamindb as ln

    ft = FeatureType("fp_cat_ft")
    Feature("fp_cat_col", dtype=LaminDType(ln.ULabel), type=ft)
"""

_CAT_ANNOTATION_FORM_PLAN = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import CatDType
from lamindb_setup.evolve import ULabelCat
from lamindb_setup.evolve import get_dtype
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan


@create_plan
def cat_annotation(ctx: PlanContext, exports: None) -> None:
    import lamindb as ln

    ft = FeatureType("fp_cat_ft")
    Feature("fp_cat_col", dtype=get_dtype(CatDType[ln.ULabel, ULabelCat()]), type=ft)
"""


def test_utils_fingerprint_ignores_dtype_notation(tmp_path: Path) -> None:
    # Rewriting a categorical from the object wrapper to the annotation form is
    # the same lamindb dtype in different notation and must not read as drift.
    object_form = tmp_path / "plan_object_form.py"
    object_form.write_text(_CAT_OBJECT_FORM_PLAN)
    baseline = tmp_path / "baseline.json"
    captured = _run_cli("utils", "fingerprint", str(object_form), "-o", str(baseline))
    assert captured.returncode == 0, captured.stderr

    annotation_form = tmp_path / "plan_annotation_form.py"
    annotation_form.write_text(_CAT_ANNOTATION_FORM_PLAN)

    result = _run_cli("utils", "fingerprint", str(annotation_form), "-b", str(baseline))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "IDENTICAL" in result.stdout


def test_utils_fingerprint_sees_a_list_element_change(tmp_path: Path) -> None:
    # Every list dtype used to fingerprint as a bare `list`: IDENTICAL.
    plan_a = tmp_path / "plan_a.py"
    plan_a.write_text(
        _SPLIT_PLAN_A_CODE.replace("LaminDType(str)", "LaminDType(list[int])")
    )
    baseline = tmp_path / "baseline.json"
    _run_cli("utils", "fingerprint", str(plan_a), "-o", str(baseline))

    changed = tmp_path / "plan_a_changed.py"
    changed.write_text(
        _SPLIT_PLAN_A_CODE.replace("LaminDType(str)", "LaminDType(list[str])")
    )

    result = _run_cli("utils", "fingerprint", str(changed), "-b", str(baseline))

    assert result.returncode == 1
    assert "dtype: 'list[int]' -> 'list[str]'" in result.stdout


def _make_lock_with(*keys: str) -> LockFile:
    lock = LockFile(plan="p")
    for key in keys:
        lock.add(LockEntry(key=key, kind="Feature", name=key, uid="abc123456789"))
    return lock


def test_prune_lock_after_destroy_rewrites_remaining_entries(tmp_path: Path) -> None:
    lock = _make_lock_with("Feature:a", "Feature:b", "Feature:c")
    path = tmp_path / "p.acc-inst.lock"
    lock.write(path)

    pruned = _prune_lock_after_destroy(lock, path, ["Feature:a", "Feature:b"])

    assert pruned == 2
    reloaded = LockFile.read(path)
    assert reloaded is not None
    assert set(reloaded.objects) == {"Feature:c"}


def test_prune_lock_after_destroy_removes_emptied_lock_file(tmp_path: Path) -> None:
    lock = _make_lock_with("Feature:a", "Feature:b")
    path = tmp_path / "p.acc-inst.lock"
    lock.write(path)

    pruned = _prune_lock_after_destroy(lock, path, ["Feature:a", "Feature:b"])

    assert pruned == 2
    assert not path.exists()


def test_prune_lock_after_destroy_no_matches_leaves_file_untouched(
    tmp_path: Path,
) -> None:
    lock = _make_lock_with("Feature:a")
    path = tmp_path / "p.acc-inst.lock"
    lock.write(path)
    before = path.read_text(encoding="utf-8")

    pruned = _prune_lock_after_destroy(lock, path, ["Feature:missing"])

    assert pruned == 0
    assert path.read_text(encoding="utf-8") == before


def test_destroy_command_is_registered() -> None:
    result = subprocess.run(
        [sys.executable, str(_CLI_LAUNCHER), "destroy", "--help"],
        capture_output=True,
        text=True,
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
    )
    assert result.returncode == 0, result.stderr
    assert "Tear down" in result.stdout
    assert "--force" in result.stdout
    assert "--trash-references" in result.stdout


def test_destroy_rejects_force_with_trash_references() -> None:
    result = _run_cli(
        "destroy", "plan_x.py", "-i", "acc/inst", "--force", "--trash-references"
    )
    assert result.returncode == 2
    assert "--force and --trash-references cannot be combined" in result.stderr


def test_destroy_rejects_permanent_with_trash_references() -> None:
    result = _run_cli(
        "destroy", "plan_x.py", "-i", "acc/inst", "--permanent", "--trash-references"
    )
    assert result.returncode == 2
    assert "--permanent and --trash-references cannot be combined" in result.stderr


def _apply_cli_plan(instance, run_cli, git_workspace) -> Path:
    """Publish and apply `_PLAN_CODE`; return the plan file."""
    (plan_file,) = git_workspace.publish({"plan_cli.py": _PLAN_CODE})
    applied = run_cli("apply", str(plan_file), "-i", instance.slug, "-y")
    assert applied.returncode == 0, applied.stderr
    return plan_file


@pytest.mark.parametrize(("answer", "applied"), [("n\n", False), ("y\n", True)])
def test_apply_prompts_unless_yes(
    instance, ln, run_cli, git_workspace, answer: str, applied: bool
) -> None:
    (plan_file,) = git_workspace.publish({"plan_cli.py": _PLAN_CODE})

    result = run_cli("apply", str(plan_file), "-i", instance.slug, input=answer)

    assert "change(s)? [y/N]" in result.stdout
    locks = list(plan_file.parent.glob("plan_cli.*.lock"))
    if applied:
        assert result.returncode == 0, result.stderr
        assert len(locks) == 1
        assert ln.Feature.filter(name="cli_col").one_or_none() is not None
    else:
        assert result.returncode == 1
        assert "Aborted." in result.stdout
        assert "Error" not in result.stderr
        assert locks == []
        assert ln.Feature.filter(name="cli_col").one_or_none() is None


@pytest.mark.parametrize("output_format", ["markdown", "json"])
def test_apply_with_a_text_format_previews_on_stderr_before_prompting(
    instance, ln, run_cli, git_workspace, tmp_path: Path, output_format: str
) -> None:
    (plan_file,) = git_workspace.publish({"plan_cli.py": _PLAN_CODE})
    out = tmp_path / f"applied.{output_format}"

    result = run_cli(
        "apply",
        str(plan_file),
        "-i",
        instance.slug,
        "-f",
        output_format,
        "-o",
        str(out),
        input="y\n",
    )

    assert result.returncode == 0, result.stderr
    # The diff being confirmed is shown -- on stderr, above the prompt.
    preview = result.stderr.index("+ Feature 'cli_col'")
    prompt = result.stderr.index("Apply 3 change(s)? [y/N]")
    assert result.stderr.index("Plan: my_plan") < preview < prompt
    # stdout and --output carry the document alone.
    document = out.read_text(encoding="utf-8")
    for text in (result.stdout, document):
        assert "change(s)?" not in text
        assert "[y/N]" not in text
    if output_format == "json":
        assert json.loads(document)["title"] == "Applied: my_plan"
        assert document in result.stdout
    else:
        assert document.startswith("### Applied: my_plan")
        assert document in result.stdout
    assert ln.Feature.filter(name="cli_col").one_or_none() is not None


def test_apply_with_a_text_format_declined_writes_nothing_to_stdout(
    instance, ln, run_cli, git_workspace
) -> None:
    (plan_file,) = git_workspace.publish({"plan_cli.py": _PLAN_CODE})

    result = run_cli(
        "apply", str(plan_file), "-i", instance.slug, "-f", "json", input="n\n"
    )

    assert result.returncode == 1
    assert "+ Feature 'cli_col'" in result.stderr
    assert "change(s)? [y/N]" in result.stderr
    assert "Aborted." in result.stderr
    assert "Aborted." not in result.stdout
    assert "[y/N]" not in result.stdout
    assert ln.Feature.filter(name="cli_col").one_or_none() is None


def test_apply_with_a_text_format_and_yes_does_not_preview(
    instance, ln, run_cli, git_workspace, tmp_path: Path
) -> None:
    (plan_file,) = git_workspace.publish({"plan_cli.py": _PLAN_CODE})
    out = tmp_path / "applied.json"

    result = run_cli(
        "apply", str(plan_file), "-i", instance.slug, "-f", "json", "-y", "-o", str(out)
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["title"] == "Applied: my_plan"
    assert "Plan: my_plan" not in result.stderr
    assert "+ Feature 'cli_col'" not in result.stderr
    assert "[y/N]" not in result.stderr + result.stdout


_UP_TO_DATE = "No changes. Instance is up to date."
_UNCHANGED_LINES = ("    FeatureType 'cli_ft'  ", "    Schema 'CliSchema'  ")
_CLI_NAMES = ("'cli_ft'", "'cli_col'", "'CliSchema'")


def _describe_cli_col(plan_file: Path, description: str = "probe") -> str:
    """Give `cli_col` a description in `plan_file`; return the new plan code."""
    code = _PLAN_CODE.replace("type=ft)", f"type=ft, description={description!r})")
    plan_file.write_text(code, encoding="utf-8")
    return code


def test_plan_lists_only_what_changes_unless_show_unchanged(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    _describe_cli_col(plan_file)
    plan = ("plan", str(plan_file), "-i", instance.slug)

    changed = run_cli(*plan)
    every = run_cli(*plan, "--show-unchanged")
    markdown = run_cli(*plan, "-f", "markdown")
    markdown_every = run_cli(*plan, "-f", "markdown", "--show-unchanged")

    assert changed.returncode == 0, changed.stderr
    assert "  ~ Feature 'cli_col'  " in changed.stdout
    assert not any(line in changed.stdout for line in _UNCHANGED_LINES)
    assert "Plan: 0 to create, 1 to update, 0 to delete, 2 unchanged" in (
        changed.stdout
    )
    assert _UP_TO_DATE not in changed.stdout
    assert every.returncode == 0, every.stderr
    assert "  ~ Feature 'cli_col'  " in every.stdout
    assert all(line in every.stdout for line in _UNCHANGED_LINES)
    # The flag is about the terminal diff: the Markdown one is the same.
    assert markdown.stdout == markdown_every.stdout
    assert "CliSchema" not in markdown.stdout


def test_plan_without_changes_says_the_instance_is_up_to_date(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    plan = ("plan", str(plan_file), "-i", instance.slug)

    settled = run_cli(*plan)
    every = run_cli(*plan, "--show-unchanged")

    assert settled.returncode == 0, settled.stderr
    assert "Plan: 0 to create, 0 to update, 0 to delete, 3 unchanged" in (
        settled.stdout
    )
    assert settled.stdout.count(_UP_TO_DATE) == 1
    assert not any(name in settled.stdout for name in _CLI_NAMES)
    assert every.returncode == 0, every.stderr
    assert all(line in every.stdout for line in _UNCHANGED_LINES)
    assert every.stdout.count(_UP_TO_DATE) == 1


def test_apply_lists_only_what_changes_unless_show_unchanged(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    apply = ("apply", str(plan_file), "-i", instance.slug, "-y")

    settled = run_cli(*apply)
    git_workspace.publish({"plan_cli.py": _describe_cli_col(plan_file)})
    changed = run_cli(*apply)
    git_workspace.publish({"plan_cli.py": _describe_cli_col(plan_file, "again")})
    every = run_cli(*apply, "--show-unchanged")

    # Nothing to apply: said once, by the diff, with nothing listed.
    assert settled.returncode == 0, settled.stderr
    assert settled.stdout.count(_UP_TO_DATE) == 1
    assert not any(name in settled.stdout for name in _CLI_NAMES)
    # The preview and the applied diff list the change alone ...
    assert changed.returncode == 0, changed.stderr
    assert changed.stdout.count("  ~ Feature 'cli_col'  ") == 2
    assert not any(line in changed.stdout for line in _UNCHANGED_LINES)
    assert changed.stdout.count("1 to update, 0 to delete, 2 unchanged") == 2
    assert _UP_TO_DATE not in changed.stdout
    # ... and every object with --show-unchanged.
    assert every.returncode == 0, every.stderr
    assert every.stdout.count("  ~ Feature 'cli_col'  ") == 2
    assert all(every.stdout.count(line) == 2 for line in _UNCHANGED_LINES)
    assert ln.Feature.get(name="cli_col", is_type=False).description == "again"


_DTYPE_HINT_PLAN = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import RecordType
from lamindb_setup.evolve import create_plan

PLAN_TRANSFORM_STEM_UID = "testUID00271"


@create_plan
def dtype_hint_plan(ctx: PlanContext, exports: None) -> None:
    import lamindb as ln

    ft = FeatureType("dh_ft")
    vocabulary = RecordType("DhVocab")
    Feature("dh_str", dtype=LaminDType(str), type=ft)
    Feature("dh_int", dtype=LaminDType(int), type=ft)
    Feature("dh_ulabel", dtype=LaminDType(ln.ULabel), type=ft)
    Feature("dh_record", dtype=LaminDType(ln.Record, type__uid=vocabulary.uid), type=ft)
    Feature("dh_list", dtype=LaminDType(list[ln.ULabel]), type=ft)
"""


def test_apply_does_not_relay_lamindb_dtype_string_hint(
    instance, ln, run_cli, git_workspace
) -> None:
    # The engine builds every feature from a dtype string the plan author never
    # wrote, so lamindb's "pass a Python object" hint is noise.
    (plan_file,) = git_workspace.publish({"plan_dh.py": _DTYPE_HINT_PLAN})

    result = run_cli("apply", str(plan_file), "-i", instance.slug, "-y")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "rather than passing a string" not in result.stdout + result.stderr
    vocabulary = ln.Record.get(name="DhVocab", is_type=True)
    dtypes = {
        f.name: f._dtype_str
        for f in ln.Feature.filter(name__startswith="dh_", is_type=False)
    }
    assert dtypes == {
        "dh_str": "str",
        "dh_int": "int",
        "dh_ulabel": "cat[ULabel]",
        "dh_record": f"cat[Record[{vocabulary.uid}]]",
        "dh_list": "list[cat[ULabel]]",
    }


# `_PLAN_CODE` with `cli_col` narrowed to `int` by a migration that fails,
# so the apply pass raises and is rolled back after the run was tracked.
_FAILING_MIGRATION_PLAN = """
from lamindb_setup.evolve import DataMigration
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import Schema
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan

PLAN_TRANSFORM_STEM_UID = "testUID00001"


def _fail(ctx) -> None:
    raise RuntimeError("migration boom")


@create_plan
def my_plan(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("cli_ft")
    f = Feature("cli_col", dtype=LaminDType(int), type=ft)
    Schema(name="CliSchema", features=[f])
    ctx.migrate(f, DataMigration(name="fail", reason="fails", migrate=_fail))
"""


def test_apply_keeps_its_failure_report_as_the_run_report(
    instance, ln, run_cli, git_workspace
) -> None:
    from lamindb_setup.evolve.engine import TrackConfig

    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    git_workspace.publish({"plan_cli.py": _FAILING_MIGRATION_PLAN})

    result = run_cli("apply", str(plan_file), "-i", instance.slug, "-y")

    assert result.returncode == 1
    assert "apply failed and was rolled back" in result.stderr
    # `ln.finish` no longer takes `run.report` over, nor says so.
    assert "overwriting run.report" not in result.stdout + result.stderr
    config = TrackConfig.resolve()
    applied, failed = ln.Run.filter(
        transform__key=config.key_for("plan_cli.py")
    ).order_by("id")
    # The CLI reports the failure itself, so lamindb's exception hook never
    # marks the run; the tracking does.
    assert failed.status == "errored"
    assert applied.status == "completed"
    slug = instance.slug.replace("/", "-")
    assert failed.report.key == f"{config.prefix}/apply-failure-reports/{slug}.txt"
    assert failed.report.description == f"apply report for {instance.slug}"
    assert failed.report.run == failed
    report = failed.report.cache().read_text()
    assert report.startswith("Failed: my_plan\n")
    assert "RuntimeError: migration boom" in report
    assert f"Run('{failed.uid}'" in _run_logs(ln, failed)
    # A run that attached no report of its own keeps lamindb's logs as its report.
    assert applied.report.kind == "__lamindb_run__"
    assert applied.report.description == f"log streams of run {applied.uid}"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["validate", "{tmp}/nope.py"], "Invalid value: plan file not found"),
        (["validate", "{tmp}/plan_empty.py"], "Invalid value: no Plan defined in"),
        (
            ["init", "!!!", "-d", "{tmp}"],
            "Invalid value: plan name must contain alphanumeric characters",
        ),
    ],
)
def test_a_bad_argument_the_engine_finds_is_a_usage_error(
    tmp_path: Path, args: list[str], message: str
) -> None:
    # The engine raises InvalidArgumentError; the command reports it against
    # its own usage, with exit code 2, as it did when the engine raised the CLI's.
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app

    (tmp_path / "plan_empty.py").write_text("x = 1\n")
    result = CliRunner().invoke(
        app,
        [arg.format(tmp=tmp_path) for arg in args],
        terminal_width=200,
        env={"COLUMNS": "200"},
    )

    assert result.exit_code == 2
    output = _collapse_rich_output(result.output + getattr(result, "stderr", ""))
    assert message in output
    assert "Usage:" in output
    assert f"{args[0]} [OPTIONS]" in output


def _reference_from_outside(ln) -> str:
    """Save a schema no plan manages that uses the plan's feature; return its uid."""
    stray = ln.Feature(name="outside_col", dtype=str).save()
    member = ln.Feature.get(name="cli_col")
    return ln.Schema(name="OutsideSchema", features=[member, stray]).save().uid


def _branch_of(registry, uid: str) -> int:
    """The branch a record sits on, trash (-1) included."""
    return registry.objects.get(uid=uid).branch_id


def test_destroy_inline_prompt_switches_to_trash_references(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    outside = _reference_from_outside(ln)
    feature = ln.Feature.get(name="cli_col").uid

    result = run_cli("destroy", str(plan_file), "-i", instance.slug, input="y\n")

    assert result.returncode == 0, result.stderr
    assert "reference objects they manage" in result.stderr
    # Accepting the inline prompt takes the recoverable path without a restart:
    # the reference is trashed rather than deleted (--force), and so are the
    # plan's own objects (not --permanent).
    assert _branch_of(ln.Schema, outside) == -1
    assert _branch_of(ln.Feature, feature) == -1


def test_destroy_inline_prompt_declined_aborts(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    outside = _reference_from_outside(ln)
    feature = ln.Feature.get(name="cli_col").uid

    result = run_cli("destroy", str(plan_file), "-i", instance.slug, input="n\n")

    assert result.returncode == 1
    assert "Nothing was destroyed" in result.stderr
    assert _branch_of(ln.Schema, outside) == 1
    assert _branch_of(ln.Feature, feature) == 1


def test_destroy_permanent_refuses_blocking_references(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    outside = _reference_from_outside(ln)
    feature = ln.Feature.get(name="cli_col").uid
    (lock,) = plan_file.parent.glob("plan_cli.*.lock")
    lock_before = lock.read_text(encoding="utf-8")

    result = run_cli(
        "destroy", str(plan_file), "-i", instance.slug, "--permanent", input="y\n"
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    # The recoverable trash offer contradicts --permanent, so it is not made.
    assert "[y/N]" not in result.stdout
    assert "to trash" not in result.stdout
    stderr = " ".join(result.stderr.split())
    assert "reference objects they manage" in stderr
    assert "OutsideSchema" in stderr
    assert (
        "Nothing was destroyed. Re-run with --force to permanently delete the "
        "blocking references as well, or drop --permanent and pass "
        "--trash-references to move them to the trash (recoverable)."
    ) in stderr
    assert _branch_of(ln.Schema, outside) == 1
    assert _branch_of(ln.Feature, feature) == 1
    assert lock.read_text(encoding="utf-8") == lock_before


def _destroy_runs(ln) -> list:
    """The runs recorded for the `destroy` operation, oldest first."""
    from lamindb_setup.evolve.engine import TrackConfig

    key = TrackConfig.resolve().key_for("destroy")
    return list(ln.Run.filter(transform__key=key).order_by("id"))


def _destroy_report(run) -> str:
    """The text of the report the `destroy` `run` saved as `run.report`."""
    assert run.report.description.startswith("destroy report")
    return run.report.cache().read_text()


def _run_logs(ln, run) -> str:
    """The log streams `ln.finish` saved for `run`, one of its outputs."""
    (logs,) = ln.Artifact.filter(run=run, kind="__lamindb_run__")
    assert logs.description == f"log streams of run {run.uid}"
    return logs.cache().read_text()


def test_destroy_records_a_run_with_its_report(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    (lock,) = plan_file.parent.glob("plan_cli.*.lock")

    result = run_cli("destroy", str(plan_file), "-i", instance.slug, "-y")

    assert result.returncode == 0, result.stderr
    assert f"Removed now-empty lock file {lock}." in result.stdout
    assert "Destroyed 3 object(s) (to trash)." in result.stdout
    assert "overwriting run.report" not in result.stdout + result.stderr
    (run,) = _destroy_runs(ln)
    assert run.status == "completed"
    assert run.params == {
        "instance": instance.slug,
        "targets": [str(plan_file)],
        "permanent": False,
        "force": False,
        "trash_references": False,
        "yes": True,
    }
    report = _destroy_report(run)
    assert report.startswith("Destroy: my_plan\n")
    for line in ("FeatureType 'cli_ft'", "Feature 'cli_col'", "Schema 'CliSchema'"):
        assert line in report
    assert "Blocking references" not in report
    assert report.endswith(
        f"Removed now-empty lock file {lock}.\n\nDestroyed 3 object(s) (to trash).\n"
    )
    assert run.report.run == run
    assert f"Run('{run.uid}'" in _run_logs(ln, run)


@pytest.mark.parametrize(
    ("args", "answer", "switch", "acted_on", "outcome"),
    [
        (
            ("--force", "-y"),
            None,
            "force",
            "Blocking references deleted permanently:",
            "referencing record(s) permanently.",
        ),
        (
            ("--trash-references", "-y"),
            None,
            "trash_references",
            "Blocking references moved to the trash",
            "Moved the trashable blocking references to the trash",
        ),
        # Accepting the inline offer is a --trash-references teardown.
        (
            (),
            "y\n",
            "trash_references",
            "Blocking references moved to the trash",
            "Moved the trashable blocking references to the trash",
        ),
    ],
)
def test_destroy_report_lists_the_blocking_references_acted_on(
    instance,
    ln,
    run_cli,
    git_workspace,
    args: tuple[str, ...],
    answer: str | None,
    switch: str,
    acted_on: str,
    outcome: str,
) -> None:
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    _reference_from_outside(ln)

    result = run_cli(
        "destroy", str(plan_file), "-i", instance.slug, *args, input=answer
    )

    assert result.returncode == 0, result.stderr
    (run,) = _destroy_runs(ln)
    assert run.params[switch] is True
    report = _destroy_report(run)
    _plan, references = report.split(f"\n{acted_on}", 1)
    assert "OutsideSchema" in references.split("\n\n", 1)[0]
    assert outcome in report
    assert report.endswith("Destroyed 3 object(s) (to trash).\n")


def test_destroy_records_no_run_unless_it_destroys(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    args = ("destroy", str(plan_file), "-i", instance.slug)
    runs = ln.Run.filter().count()

    declined = run_cli(*args, input="n\n")

    assert declined.returncode == 1
    assert "Aborted." in declined.stdout
    assert ln.Run.filter().count() == runs
    assert ln.Feature.filter(name="cli_col").exists()

    destroyed = run_cli(*args, "-y")
    again = run_cli(*args, "-y")

    assert destroyed.returncode == 0, destroyed.stderr
    assert ln.Run.filter().count() == runs + 1
    assert again.returncode == 0, again.stderr
    assert "Nothing to do." in again.stdout
    assert ln.Run.filter().count() == runs + 1
    assert len(_destroy_runs(ln)) == 1


def test_destroy_records_no_run_when_it_refuses(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    _reference_from_outside(ln)
    runs = ln.Run.filter().count()

    result = run_cli("destroy", str(plan_file), "-i", instance.slug, "-y")

    assert result.returncode == 1
    assert "Nothing was destroyed." in result.stderr
    assert ln.Run.filter().count() == runs


def test_destroy_config_records_the_run_in_the_track_space(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    space = ln.Space(name="DestroyMaintenance", uid="spaceUID0274").save()
    # An artifact saved in a space needs a storage location managed through it;
    # `Storage.save` would ask the hub, so the location is moved in place.
    ln.Storage.filter(id=ln.settings.storage.record.id).update(space=space)
    (git_workspace.root / "lamin_schemas.yml").write_text(
        "track_prefix: org/schemas\n"
        f"instances:\n  - slug: {instance.slug}\n    plans: [plan_cli.py]\n"
        "    track_space: DestroyMaintenance\n",
        encoding="utf-8",
    )

    result = run_cli(
        "destroy",
        plan_file.name,
        "-i",
        instance.slug,
        "-y",
        "-c",
        "lamin_schemas.yml",
        cwd=git_workspace.root,
    )

    assert result.returncode == 0, result.stderr
    assert not ln.Feature.filter(name="cli_col").exists()
    run = ln.Run.get(transform__key="org/schemas/destroy")
    assert run.params["targets"] == ["plan_cli.py"]
    report = run.report
    assert report.description.startswith("destroy report")
    assert report.key == (
        f"org/schemas/destroy-reports/{instance.slug.replace('/', '-')}.txt"
    )
    for record in (run, run.transform, report):
        assert record.space.name == "DestroyMaintenance"


def test_destroy_failure_is_raised_and_recorded_on_its_run(
    instance, ln, run_cli, git_workspace, tmp_path: Path
) -> None:
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    (lock,) = plan_file.parent.glob("plan_cli.*.lock")
    lock_before = lock.read_text(encoding="utf-8")
    # --force deletes this artifact for the schema it validates against, and
    # with it the file in storage: removing that file fails mid-teardown.
    data = tmp_path / "blocking.txt"
    data.write_text("blocking\n", encoding="utf-8")
    artifact = ln.Artifact(data, description="blocking").save()
    artifact.schema = ln.Schema.get(name="CliSchema")
    artifact.save()

    result = run_cli(
        "destroy",
        str(plan_file),
        "-i",
        instance.slug,
        "--force",
        "-y",
        faults=[
            {
                "event": "os.remove",
                "match": artifact.uid[:16],
                "raise": "PermissionError",
            }
        ],
    )

    assert result.returncode == 1
    assert "PermissionError" in result.stderr
    assert "Destroyed" not in result.stdout
    (run,) = _destroy_runs(ln)
    assert run.status == "errored"
    assert not ln.Artifact.filter(run=run, description__startswith="destroy report")
    # The teardown was rolled back.
    assert ln.Artifact.filter(uid=artifact.uid).exists()
    assert ln.Feature.filter(name="cli_col").exists()
    assert lock.read_text(encoding="utf-8") == lock_before


def test_prune_dangling_command_is_registered() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "repair",
            "dangling",
            "--help",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
    )
    assert result.returncode == 0, result.stderr
    assert "reference deleted" in result.stdout
    assert "--permanent" in result.stdout
    assert "--space" in result.stdout
    assert "--exclude-space" in result.stdout


def test_repair_sheet_schemas_command_is_registered() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "repair",
            "sheet-schemas",
            "--help",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
    )
    assert result.returncode == 0, result.stderr
    assert "schema type" in result.stdout
    assert "--space" in result.stdout
    assert "--exclude-space" in result.stdout


def test_remove_duplicates_command_is_registered() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(_CLI_LAUNCHER),
            "repair",
            "duplicates",
            "--help",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
    )
    assert result.returncode == 0, result.stderr
    assert "duplicate" in result.stdout
    assert "--permanent" in result.stdout
    assert "--space" in result.stdout
    assert "--exclude-space" in result.stdout


def test_every_user_facing_error_derives_from_the_shared_base() -> None:
    """The CLI's clean rendering is opt-in by class, so the opt-in must be complete."""
    from lamindb_setup.evolve.authoring._migrations import MigrationError
    from lamindb_setup.evolve.ci._config import ConfigError
    from lamindb_setup.evolve.core import LaminSchemasError
    from lamindb_setup.evolve.engine._apply import (
        DriftError,
        MissingPrerequisiteError,
        PlanChangeRefusedError,
        SpaceNotFoundError,
    )
    from lamindb_setup.evolve.engine._loader import PlanImportError
    from lamindb_setup.evolve.engine._track import TrackError
    from lamindb_setup.evolve.introspect import IntrospectionError

    for error in (
        ConfigError,
        DriftError,
        IntrospectionError,
        MigrationError,
        MissingPrerequisiteError,
        PlanChangeRefusedError,
        PlanImportError,
        SpaceNotFoundError,
        TrackError,
    ):
        assert issubclass(error, LaminSchemasError), error
        # RuntimeError stays a base so existing `except RuntimeError` still works.
        assert issubclass(error, RuntimeError), error
    # A refused plan change is not drift, but `except DriftError` still catches it.
    assert issubclass(PlanChangeRefusedError, DriftError)


def _drifted_plan(instance, ln, run_cli, git_workspace) -> Path:
    """Apply `_PLAN_CODE`, then trash a managed object behind the plan's back."""
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    ln.Schema.get(name="CliSchema").delete()
    return plan_file


def test_plan_renders_a_drift_error_without_a_traceback(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _drifted_plan(instance, ln, run_cli, git_workspace)

    result = run_cli("plan", str(plan_file), "-i", instance.slug)

    assert result.returncode == 1
    collapsed = _collapse_rich_output(result.stderr)
    assert "Error: drift for 'Schema:CliSchema'" in collapsed
    assert "soft-deleted" in collapsed
    assert "Traceback" not in collapsed


def test_traceback_env_var_re_raises_the_original_error(
    instance, ln, run_cli, git_workspace
) -> None:
    from lamin_cli.evolve._commands import TRACEBACK_ENV_VAR

    plan_file = _drifted_plan(instance, ln, run_cli, git_workspace)

    result = run_cli(
        "plan",
        str(plan_file),
        "-i",
        instance.slug,
        env={TRACEBACK_ENV_VAR: "1", "_TYPER_STANDARD_TRACEBACK": "1"},
    )

    assert result.returncode == 1
    assert "Traceback (most recent call last)" in result.stderr
    assert "lamindb_setup.evolve.engine._apply.DriftError: drift for" in result.stderr


def test_validate_format_json_emits_a_parseable_document(tmp_path: Path) -> None:
    """--format json used to fall through to the terminal view and write nothing."""
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app

    runner = CliRunner()
    assert runner.invoke(app, ["init", "jsonplan", "-d", str(tmp_path)]).exit_code == 0
    out = tmp_path / "summary.json"

    result = runner.invoke(
        app,
        [
            "validate",
            str(tmp_path / "plan_jsonplan.py"),
            "--format",
            "json",
            "-o",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["plan"]["name"] == "jsonplan"
    assert payload["objects"] == []
    assert payload["declarations"] == []
    # ...and the same document went to stdout
    assert json.loads(result.output)["heading"] == "Plan summary: jsonplan"


_DECLARING_PLAN = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import Schema
from lamindb_setup.evolve import create_plan
from lamindb_setup.evolve import drop_values
from lamindb_setup.evolve import fill_missing
from lamindb_setup.evolve import map_values

PLAN_TRANSFORM_STEM_UID = "testUID00269"


@create_plan
def m(ctx: PlanContext, exports: None) -> None:
    ft = FeatureType("m_ft")
    a = Feature("m_a", dtype=LaminDType(int), type=ft)
    s = Schema(name="MSchema", features=[a])
    DECLARATIONS
"""


@pytest.mark.parametrize(
    ("declaration", "message"),
    [
        (
            'ctx.migrate(a, fill_missing("x"))',
            "fill_missing() answers a Schema (a member it makes required), "
            "not Feature 'm_a'.",
        ),
        (
            "ctx.migrate(s, map_values())",
            "map_values() answers a Feature (a change to its dtype), "
            "not Schema 'MSchema'.",
        ),
        (
            'ctx.migrate("this is not a key", drop_values())',
            "ctx.migrate('this is not a key', drop_values()) names no object: "
            "'this is not a key' is not a natural key",
        ),
        (
            'ctx.migrate("FeatureType:m_ft/Feature:m_a", drop_values())',
            "drop_values() is declared for 'FeatureType:m_ft/Feature:m_a', which "
            "the plan still declares.",
        ),
    ],
)
def test_validate_refuses_a_declaration_that_can_never_do_what_it_says(
    run_cli, tmp_path: Path, declaration: str, message: str
) -> None:
    """Each of these used to validate with exit 0."""
    plan_file = tmp_path / "plan_m.py"
    plan_file.write_text(_DECLARING_PLAN.replace("DECLARATIONS", declaration))

    result = run_cli("validate", f"{plan_file}:m")

    assert result.returncode == 1, result.stdout
    assert f"Error: plan 'm': {message}" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Plan summary" not in result.stdout


def test_validate_lists_the_declarations_in_every_format(
    run_cli, tmp_path: Path
) -> None:
    """The summary used to show neither `ctx.migrate` nor `ctx.moved`."""
    plan_file = tmp_path / "plan_m.py"
    plan_file.write_text(
        _DECLARING_PLAN.replace(
            "DECLARATIONS",
            "ctx.migrate(a, map_values())\n"
            '    ctx.migrate("FeatureType:m_ft/Feature:gone", drop_values())\n'
            '    ctx.moved(s, "OldSchema")',
        )
    )
    labels = [
        "migrate Feature 'm_a' ← map_values()",
        "migrate 'FeatureType:m_ft/Feature:gone' ← drop_values()",
        "moved Schema 'MSchema' ← 'OldSchema'",
    ]

    terminal = run_cli("validate", f"{plan_file}:m")
    markdown = run_cli("validate", f"{plan_file}:m", "--format", "markdown")
    out = tmp_path / "summary.json"
    document = run_cli("validate", f"{plan_file}:m", "--format", "json", "-o", str(out))

    assert terminal.returncode == 0, terminal.stderr
    assert "Declarations (3)" in terminal.stdout
    for label in labels:
        assert label in terminal.stdout
    assert markdown.returncode == 0, markdown.stderr
    assert "### Declarations (3)\n" in markdown.stdout
    assert "- migrate **Feature** `m_a` ← `map_values()` (" in markdown.stdout
    assert "- moved **Schema** `MSchema` ← `'OldSchema'`\n" in markdown.stdout
    assert document.returncode == 0, document.stderr
    declarations = json.loads(out.read_text(encoding="utf-8"))["declarations"]
    assert [d["label"] for d in declarations] == labels
    assert declarations[1]["key"] == "FeatureType:m_ft/Feature:gone"
    assert declarations[0]["reason"] == "rewrite scalar values for the new dtype"


def test_output_without_a_document_format_is_refused(tmp_path: Path) -> None:
    """Silently writing nothing is what this replaces."""
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app

    runner = CliRunner()
    assert runner.invoke(app, ["init", "outplan", "-d", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        ["validate", str(tmp_path / "plan_outplan.py"), "-o", str(tmp_path / "x.txt")],
        terminal_width=200,
        env={"COLUMNS": "200"},
    )

    assert result.exit_code == 2
    collapsed = _collapse_rich_output(result.output + getattr(result, "stderr", ""))
    assert "needs a format that produces a document" in collapsed
    assert not (tmp_path / "x.txt").exists()


def _help_text(*command: str) -> str:
    """`lamin evolve <command> --help`, collapsed to one line of plain text."""
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app

    result = CliRunner().invoke(
        app, [*command, "--help"], terminal_width=200, env={"COLUMNS": "200"}
    )
    assert result.exit_code == 0, result.output
    return _collapse_rich_output(result.output)


@pytest.mark.parametrize(
    "command", [["validate"], ["plan"], ["apply"], ["ci", "apply"]], ids=" ".join
)
def test_output_help_names_the_formats_that_write_a_document(
    command: list[str],
) -> None:
    # It claimed to work for every --format, then refused the default one.
    help_text = _help_text(*command)

    assert "Works for every --format" not in help_text
    assert "Needs --format markdown or --format json." in help_text


def test_several_plans_render_as_one_json_array(tmp_path: Path) -> None:
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app

    runner = CliRunner()
    for name in ("one", "two"):
        assert runner.invoke(app, ["init", name, "-d", str(tmp_path)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "validate",
            str(tmp_path / "plan_one.py"),
            str(tmp_path / "plan_two.py"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [entry["plan"]["name"] for entry in payload] == ["one", "two"]


def test_json_object_ids_are_dense_and_not_python_ids(tmp_path: Path) -> None:
    """Raw id() values would make two runs of the same plan diff spuriously."""
    from lamindb_setup.evolve.authoring._plan import create_plan
    from lamindb_setup.evolve.render import JsonPlanSummary

    @create_plan
    def _json_plan(ctx: PlanContext, exports: None) -> None:
        ft = FeatureType("json_ft")
        Feature("json_f", dtype=LaminDType(str), type=ft)

    _json_plan.reset()
    _json_plan.finalize()
    payload = json.loads(JsonPlanSummary().render(_json_plan))

    ids = [obj["obj_id"] for obj in payload["objects"]]
    assert ids == list(range(len(ids)))
    feature = next(o for o in payload["objects"] if o["kind"] == "Feature")
    ftype = next(o for o in payload["objects"] if o["kind"] == "FeatureType")
    assert feature["parent_id"] == ftype["obj_id"]
    assert feature["label"].startswith("Feature 'json_f'")


def _make_plan_file(tmp_path: Path, name: str) -> Path:
    """Write a standalone plan file exporting one feature type."""
    path = tmp_path / f"plan_{name}.py"
    path.write_text(
        "from __future__ import annotations\n\n"
        "from lamindb_setup.evolve import FeatureType\n"
        "from lamindb_setup.evolve import PlanContext\n"
        "from lamindb_setup.evolve import PlanExports\n"
        "from lamindb_setup.evolve import create_plan\n\n"
        'PLAN_TRANSFORM_STEM_UID = "stemUID00001"\n\n\n'
        "class Exports(PlanExports):\n"
        "    ft: FeatureType\n\n\n"
        "@create_plan\n"
        f"def {name}_plan(ctx: PlanContext, exports: Exports) -> None:\n"
        f'    exports.ft = FeatureType("{name}_ft")\n',
        encoding="utf-8",
    )
    return path


def _make_dependent_plan_file(tmp_path: Path, name: str, *, depends_on: str) -> Path:
    """Write a plan file that borrows a feature type from `depends_on`."""
    path = tmp_path / f"plan_{name}.py"
    path.write_text(
        "from __future__ import annotations\n\n"
        "from lamindb_setup.evolve import FeatureType\n"
        "from lamindb_setup.evolve import Plan\n"
        "from lamindb_setup.evolve import PlanContext\n"
        "from lamindb_setup.evolve import create_plan\n"
        f"from plan_{depends_on} import Exports\n"
        f"from plan_{depends_on} import {depends_on}_plan\n\n"
        'PLAN_TRANSFORM_STEM_UID = "stemUID00002"\n\n\n'
        "@create_plan\n"
        f"def {name}_plan(\n"
        "    ctx: PlanContext,\n"
        "    exports: None,\n"
        f"    up: Plan[Exports] = {depends_on}_plan,\n"
        ") -> None:\n"
        f'    FeatureType("{name}_sub", type=up.exports.ft)\n',
        encoding="utf-8",
    )
    return path


def test_apply_orders_listed_plans_dependencies_first(
    instance, ln, run_cli, git_workspace, tmp_path: Path
) -> None:
    """Listing both plans states they belong together, not what order to use."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    upstream = _make_plan_file(scratch, "up")
    downstream = _make_dependent_plan_file(scratch, "down", depends_on="up")
    downstream, upstream = git_workspace.publish(
        {path.name: path.read_text() for path in (downstream, upstream)}
    )

    # The dependent plan is listed *first*.  Applied in that order, it would
    # borrow a feature type that does not exist yet and be refused.
    result = run_cli("apply", str(downstream), str(upstream), "-i", instance.slug, "-y")

    assert result.returncode == 0, result.stderr
    assert result.stdout.index("Plan: up_plan") < result.stdout.index("Plan: down_plan")
    assert ln.Feature.get(name="down_sub").type.name == "up_ft"


_PREREQUISITE_BASE = """
from __future__ import annotations

from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import PlanExports
from lamindb_setup.evolve import create_plan

PLAN_TRANSFORM_STEM_UID = "prereqBASE01"


class BaseExports(PlanExports):
    ft: FeatureType
    a: Feature


@create_plan
def base(ctx: PlanContext, exports: BaseExports) -> None:
    exports.ft = FeatureType("p2_ft")
    exports.a = Feature("p2_a", dtype=LaminDType(str), type=exports.ft)
"""

_PREREQUISITE_CHILD = """
from __future__ import annotations

from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import Plan
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import Schema
from lamindb_setup.evolve import create_plan

from plans.plan_base import BaseExports
from plans.plan_base import base

PLAN_TRANSFORM_STEM_UID = "prereqCHLD01"


@create_plan
def child(ctx: PlanContext, exports: None, p: Plan[BaseExports] = base) -> None:
    b = Feature("p2_b", dtype=LaminDType(str), type=p.exports.ft)
    Schema(name="P2Schema", features=[p.exports.a, b])
"""


def _publish_prerequisite_plans(git_workspace, **extra: str) -> None:
    """Publish `plans/plan_child.py`, which borrows from `plans/plan_base.py`."""
    git_workspace.publish(
        {
            "pyproject.toml": "[project]\nname = 'prerequisites'\n",
            "plans/plan_base.py": _PREREQUISITE_BASE,
            "plans/plan_child.py": _PREREQUISITE_CHILD,
            **extra,
        }
    )


def test_apply_refuses_before_tracking_when_a_prerequisite_is_not_applied(
    instance, ln, run_cli, git_workspace
) -> None:
    _publish_prerequisite_plans(git_workspace)
    tracked = (ln.Transform.filter().count(), ln.Run.filter().count())

    result = run_cli(
        "apply",
        "plans/plan_child.py",
        "-i",
        instance.slug,
        "-y",
        cwd=git_workspace.root,
    )

    assert result.returncode == 1
    error = _collapse_rich_output(result.stderr)
    assert (
        "Error: refusing to apply 'child': 2 object(s) it borrows from prerequisite "
        f"plan 'base' (plans/plan_base.py) do not exist on {instance.slug}: "
        "FeatureType 'p2_ft', Feature 'p2_a' Apply that plan first (lamin evolve "
        f"apply plans/plan_base.py -i {instance.slug}), or list both plans in one "
        "apply -- they are applied dependencies-first."
    ) in error
    # Refused from the preview: no transform version, no errored run, no lock.
    assert (ln.Transform.filter().count(), ln.Run.filter().count()) == tracked
    assert "Tracking run" not in result.stdout
    assert not list((git_workspace.root / "plans").glob("*.lock"))
    assert not ln.Feature.filter(name__in=["p2_ft", "p2_a", "p2_b"]).exists()

    # Listing both -- the dependent one first -- applies them dependencies-first.
    together = run_cli(
        "apply",
        "plans/plan_child.py",
        "plans/plan_base.py",
        "-i",
        instance.slug,
        "-y",
        cwd=git_workspace.root,
    )

    assert together.returncode == 0, together.stderr
    assert together.stdout.index("Plan: base") < together.stdout.index("Plan: child")
    assert ln.Feature.get(name="p2_b").type.name == "p2_ft"
    members = {feature.name for feature in ln.Schema.get(name="P2Schema").members}
    assert members == {"p2_a", "p2_b"}


def test_plan_refuses_a_plan_whose_prerequisite_is_not_applied_as_apply_does(
    instance, ln, run_cli, git_workspace
) -> None:
    _publish_prerequisite_plans(git_workspace)

    result = run_cli(
        "plan", "plans/plan_child.py", "-i", instance.slug, cwd=git_workspace.root
    )

    # The post-merge apply would refuse it, so the plan says so.
    assert result.returncode == 1
    error = _collapse_rich_output(result.stderr)
    assert (
        "Error: refusing to apply 'child': 2 object(s) it borrows from prerequisite "
        f"plan 'base' (plans/plan_base.py) do not exist on {instance.slug}: "
        "FeatureType 'p2_ft', Feature 'p2_a' Apply that plan first (lamin evolve "
        f"apply plans/plan_base.py -i {instance.slug}), or list both plans in one "
        "apply -- they are applied dependencies-first."
    ) in error
    # The diff is still shown in full.
    output = _collapse_rich_output(result.stdout)
    # Marked as borrowed rather than created: the plan never creates it.
    assert "\u2190 FeatureType 'p2_ft'" in output
    assert "+ FeatureType 'p2_ft'" not in output
    assert (
        "WARNING: owned by a prerequisite plan that has not been applied to this "
        "instance; apply that plan first, or this apply will refuse."
    ) in output
    # Bannered as the missing prerequisite it is, not as drift.
    assert (
        "! 2 object(s) are owned by a prerequisite plan that is not applied to "
        "this instance."
    ) in output
    assert "changed outside the plan" not in output
    # Counted apart from the two objects the plan does create.
    assert (
        "Plan: 2 to create, 0 to update, 0 to delete, 0 unchanged, 2 to be created "
        "by prerequisite plans"
    ) in output
    assert not ln.Feature.filter(name__in=["p2_ft", "p2_a", "p2_b"]).exists()


def test_plan_refuses_a_missing_prerequisite_once_every_plan_is_shown(
    instance, ln, run_cli, git_workspace
) -> None:
    _publish_prerequisite_plans(
        git_workspace, **{"plans/plan_other.py": _PREREQUISITE_OTHER}
    )

    result = run_cli(
        "plan",
        "plans/plan_child.py",
        "plans/plan_other.py",
        "-i",
        instance.slug,
        cwd=git_workspace.root,
    )

    assert result.returncode == 1
    # Both are refused, each after both diffs, as renames are.
    assert "Plan: child" in result.stdout
    assert "Plan: other" in result.stdout
    error = _collapse_rich_output(result.stderr)
    assert error.count("Error: refusing to apply") == 2
    assert "refusing to apply 'child'" in error
    assert "refusing to apply 'other'" in error


def test_plan_preview_migrations_fails_on_a_missing_prerequisite(
    instance, ln, run_cli, git_workspace
) -> None:
    _publish_prerequisite_plans(git_workspace)

    result = run_cli(
        "plan",
        "plans/plan_child.py",
        "-i",
        instance.slug,
        "--preview-migrations",
        cwd=git_workspace.root,
    )

    # The rehearsed apply cannot adopt what is missing, so it stops there.
    assert result.returncode == 1
    error = _collapse_rich_output(result.stderr)
    assert "is owned by a plan this one depends on" in error
    assert "Apply that plan first" in error
    assert not ln.Feature.filter(name__in=["p2_ft", "p2_a", "p2_b"]).exists()


def test_plan_documents_categorise_a_missing_prerequisite(
    instance, ln, run_cli, git_workspace, tmp_path: Path
) -> None:
    _publish_prerequisite_plans(git_workspace)
    documents = {}
    for output_format in ("markdown", "json"):
        out = tmp_path / f"plan.{output_format}"
        result = run_cli(
            "plan",
            "plans/plan_child.py",
            "-i",
            instance.slug,
            "-f",
            output_format,
            "-o",
            str(out),
            cwd=git_workspace.root,
        )
        # Refused as apply refuses it, once the document is written.
        assert result.returncode == 1
        assert "Error: refusing to apply 'child'" in result.stderr
        documents[output_format] = out.read_text(encoding="utf-8")

    assert (
        "> [!WARNING]\n> 2 object(s) are owned by a prerequisite plan that is not "
        "applied to this instance.\n"
    ) in documents["markdown"]
    assert "changed outside the plan" not in documents["markdown"]
    changes = {c["name"]: c for c in json.loads(documents["json"])["changes"]}
    assert changes["p2_ft"]["warning_categories"] == ["prerequisite"]
    assert changes["p2_a"]["warning_categories"] == ["prerequisite"]
    assert changes["p2_b"]["warnings"] == []
    assert changes["p2_b"]["warning_categories"] == []
    assert (changes["p2_ft"]["borrowed"], changes["p2_b"]["borrowed"]) == (True, False)
    counts = json.loads(documents["json"])["counts"]
    assert (counts["create"], counts["borrowed_create"]) == (2, 2)
    assert (
        "**Plan:** 2 to create, 0 to update, 0 to delete, 0 unchanged, 2 to be "
        "created by prerequisite plans."
    ) in documents["markdown"]


def test_ci_apply_applies_a_configured_prerequisite_first(
    instance, ln, run_cli, git_workspace
) -> None:
    # The dependent plan is configured first; the order is derived, not read.
    config = (
        f"instances:\n  - slug: {instance.slug}\n"
        "    plans: [plans/plan_child.py, plans/plan_base.py]\n"
    )
    _publish_prerequisite_plans(git_workspace, **{"lamin_schemas.yml": config})

    result = run_cli("ci", "apply", "-c", "lamin_schemas.yml", cwd=git_workspace.root)

    assert result.returncode == 0, result.stderr
    assert ln.Feature.get(name="p2_b").type.name == "p2_ft"
    assert ln.Schema.filter(name="P2Schema").exists()


_CREATED_BY_BASE = "managed by prerequisite plan 'base' (created by it in this run)"


def _assert_child_credits_base(output: str) -> None:
    """`child`'s diff credits `base` with its borrowed objects, unwarned."""
    child = output[output.index("Plan: child") :]
    for kind, name in (("FeatureType", "p2_ft"), ("Feature", "p2_a")):
        credited = f"\u2190 {kind} '{name}' (known after apply) {_CREATED_BY_BASE}"
        assert credited in child
    assert (
        "Plan: 2 to create, 0 to update, 0 to delete, 0 unchanged, 2 to be created "
        "by prerequisite plans"
    ) in child
    assert "WARNING" not in output
    assert "prerequisite plan that is not applied" not in output


@pytest.mark.parametrize("preview_migrations", [False, True])
def test_plan_credits_a_listed_prerequisite_with_what_it_creates(
    instance, ln, run_cli, git_workspace, preview_migrations: bool
) -> None:
    _publish_prerequisite_plans(git_workspace)
    flags = ["--preview-migrations"] if preview_migrations else []

    result = run_cli(
        "plan",
        "plans/plan_base.py",
        "plans/plan_child.py",
        "-i",
        instance.slug,
        *flags,
        cwd=git_workspace.root,
    )

    assert result.returncode == 0, result.stderr
    output = _collapse_rich_output(result.stdout)
    _assert_child_credits_base(output)
    base = output[output.index("Plan: base") : output.index("Plan: child")]
    assert "Plan: 2 to create, 0 to update, 0 to delete, 0 unchanged" in base
    assert not ln.Feature.filter(name__in=["p2_ft", "p2_a", "p2_b"]).exists()


def test_plan_credits_a_listed_prerequisite_in_any_order_and_format(
    instance, ln, run_cli, git_workspace, tmp_path: Path
) -> None:
    _publish_prerequisite_plans(git_workspace)
    out = tmp_path / "plan.json"

    result = run_cli(
        "plan",
        "plans/plan_child.py",
        "plans/plan_base.py",
        "-i",
        instance.slug,
        "-f",
        "json",
        "-o",
        str(out),
        cwd=git_workspace.root,
    )

    assert result.returncode == 0, result.stderr
    child, base = json.loads(out.read_text(encoding="utf-8"))
    assert child["title"] == "Plan: child"
    assert (child["counts"]["create"], child["counts"]["borrowed_create"]) == (2, 2)
    assert (base["counts"]["create"], base["counts"]["borrowed_create"]) == (2, 0)
    borrowed = {c["name"]: c for c in child["changes"] if c["borrowed"]}
    assert set(borrowed) == {"p2_ft", "p2_a"}
    for change in borrowed.values():
        assert change["action"] == "create"
        assert change["details"] == [_CREATED_BY_BASE]
        assert change["warnings"] == []


@pytest.mark.parametrize("preview_migrations", [False, True])
def test_ci_plan_credits_a_configured_prerequisite_with_what_it_creates(
    instance, ln, run_cli, git_workspace, tmp_path: Path, preview_migrations: bool
) -> None:
    # The dependent plan is configured first; a rehearsal runs base first.
    config = (
        f"instances:\n  - slug: {instance.slug}\n"
        "    plans: [plans/plan_child.py, plans/plan_base.py]\n"
    )
    _publish_prerequisite_plans(git_workspace, **{"lamin_schemas.yml": config})
    out = tmp_path / "report.md"
    flags = ["--preview-migrations"] if preview_migrations else []

    result = run_cli(
        "ci",
        "plan",
        "-c",
        "lamin_schemas.yml",
        "-o",
        str(out),
        *flags,
        cwd=git_workspace.root,
    )

    assert result.returncode == 0, result.stderr
    report = out.read_text(encoding="utf-8")
    start = report.index("`plans/plan_child.py`")
    child = report[start : report.index("`plans/plan_base.py` \u2192")]
    assert (
        "2 to create, 0 to update, 0 to delete, 2 to be created by prerequisite "
        "plans</summary>"
    ) in child
    for kind, name in (("FeatureType", "p2_ft"), ("Feature", "p2_a")):
        credited = f"\n  {kind} '{name}'  (known after apply)\n    {_CREATED_BY_BASE}\n"
        assert credited in child
    assert "+ Feature 'p2_b'" in child
    assert "WARNING" not in report
    assert "2 to create, 0 to update, 0 to delete</summary>" in report
    assert not ln.Feature.filter(name__in=["p2_ft", "p2_a", "p2_b"]).exists()


def test_ci_plan_fails_a_plan_whose_prerequisite_is_not_configured(
    instance, ln, run_cli, git_workspace, tmp_path: Path
) -> None:
    config = (
        f"instances:\n  - slug: {instance.slug}\n    plans: [plans/plan_child.py]\n"
    )
    _publish_prerequisite_plans(git_workspace, **{"lamin_schemas.yml": config})
    out = tmp_path / "report.md"

    result = run_cli(
        "ci", "plan", "-c", "lamin_schemas.yml", "-o", str(out), cwd=git_workspace.root
    )

    # Failed without --fail-on-diff: the post-merge ci apply would refuse.
    assert result.returncode == 1
    report = out.read_text(encoding="utf-8")
    assert "Validation failed" in report
    refused = report[report.index("#### \u274c `plans/plan_child.py` \u2192") :]
    # The diff refused, then the refusal apply gives, under it.
    diff = refused.index("<summary>Diff \u2014 2 to create, 0 to update, 0 to delete")
    refusal = refused.index(
        "```\nrefusing to apply 'child': 2 object(s) it borrows from prerequisite "
        f"plan 'base' (plans/plan_base.py) do not exist on {instance.slug}:\n"
        "  FeatureType 'p2_ft', Feature 'p2_a'\n"
        "Apply that plan first (lamin evolve apply plans/plan_base.py -i "
        f"{instance.slug}), or list both plans in one apply -- they are applied "
        "dependencies-first.\n```"
    )
    assert diff < refusal
    assert "! WARNING: owned by a prerequisite plan" in refused[:refusal]


def _apply_prerequisite_plans(instance, run_cli, git_workspace) -> dict[str, Path]:
    """Publish and apply `base` and `child`; return their lock files by plan."""
    _publish_prerequisite_plans(git_workspace)
    applied = run_cli(
        "apply",
        "plans/plan_child.py",
        "plans/plan_base.py",
        "-i",
        instance.slug,
        "-y",
        cwd=git_workspace.root,
    )
    assert applied.returncode == 0, applied.stderr
    plans = git_workspace.root / "plans"
    return {name: next(plans.glob(f"plan_{name}.*.lock")) for name in ("base", "child")}


def test_destroy_refuses_references_a_dependent_plan_holds(
    instance, ln, run_cli, git_workspace
) -> None:
    locks = _apply_prerequisite_plans(instance, run_cli, git_workspace)
    recorded = {name: path.read_text(encoding="utf-8") for name, path in locks.items()}

    # Agreeing to every prompt changes nothing: no inline trash offer is made.
    result = run_cli(
        "destroy",
        "plans/plan_base.py",
        "-i",
        instance.slug,
        input="y\ny\n",
        cwd=git_workspace.root,
    )

    assert result.returncode == 1
    together = f"lamin evolve destroy plans/plan_child.py plans/plan_base.py -i {instance.slug}"
    assert (
        "Error: 2 object(s) managed by plan 'child' (plans/plan_child.py) reference "
        "objects this plan manages.\n"
    ) in result.stderr
    assert "name='p2_b'" in result.stderr
    assert "name='P2Schema'" in result.stderr
    assert f"Destroy them together: {together}\nNothing was destroyed.\n" in (
        result.stderr
    )
    assert "other reference(s)" not in result.stderr
    assert "[y/N]" not in result.stdout + result.stderr
    assert "trash" not in result.stdout + result.stderr
    for name in ("p2_ft", "p2_a", "p2_b"):
        assert ln.Feature.filter(name=name).one_or_none() is not None
    assert ln.Schema.filter(name="P2Schema").one_or_none() is not None
    assert {n: p.read_text(encoding="utf-8") for n, p in locks.items()} == recorded

    # The command it suggests tears both plans down.
    command = shlex.split(together)[2:]
    destroyed = run_cli(*command, "-y", cwd=git_workspace.root)

    assert destroyed.returncode == 0, destroyed.stderr
    assert "Destroyed 4 object(s) (to trash)." in destroyed.stdout
    assert not ln.Feature.filter(name__in=["p2_ft", "p2_a", "p2_b"]).exists()
    assert not ln.Schema.filter(name="P2Schema").exists()
    assert not any(path.exists() for path in locks.values())


@pytest.mark.parametrize(
    ("option", "action"),
    [
        ("--trash-references", "--trash-references moves them to the trash"),
        ("--force", "--force deletes them permanently"),
    ],
)
def test_destroy_warns_of_the_lock_a_dependent_plan_is_left_with(
    instance, ln, run_cli, git_workspace, option: str, action: str
) -> None:
    locks = _apply_prerequisite_plans(instance, run_cli, git_workspace)
    child_lock = locks["child"].read_text(encoding="utf-8")

    result = run_cli(
        "destroy",
        "plans/plan_base.py",
        "-i",
        instance.slug,
        option,
        "-y",
        cwd=git_workspace.root,
    )

    assert result.returncode == 0, result.stderr
    warning = _collapse_rich_output(result.stderr)
    assert (
        "WARNING: 2 object(s) managed by plan 'child' (plans/plan_child.py) "
        f"reference objects this plan manages. {action} under that plan, leaving "
        f"its lock file stale: plans/{locks['child'].name}. To keep "
        "that plan in sync, destroy them together instead: lamin evolve destroy "
        f"plans/plan_child.py plans/plan_base.py -i {instance.slug}"
    ) in warning
    assert "Destroyed 2 object(s) (to trash)." in result.stdout
    assert not ln.Feature.filter(name__in=["p2_ft", "p2_a", "p2_b"]).exists()
    # The base plan's lock is pruned; the child's is left as it was -- stale.
    assert not locks["base"].exists()
    assert locks["child"].read_text(encoding="utf-8") == child_lock


_PREREQUISITE_OTHER = """
from __future__ import annotations

from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import Plan
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan

from plans.plan_base import BaseExports
from plans.plan_base import base

PLAN_TRANSFORM_STEM_UID = "prereqOTHR01"


@create_plan
def other(ctx: PlanContext, exports: None, p: Plan[BaseExports] = base) -> None:
    Feature("p2_c", dtype=LaminDType(str), type=p.exports.ft)
"""


def test_destroy_names_every_dependent_plan_holding_references(
    instance, ln, run_cli, git_workspace
) -> None:
    _publish_prerequisite_plans(
        git_workspace, **{"plans/plan_other.py": _PREREQUISITE_OTHER}
    )
    applied = run_cli(
        "apply",
        "plans/plan_child.py",
        "plans/plan_other.py",
        "plans/plan_base.py",
        "-i",
        instance.slug,
        "-y",
        cwd=git_workspace.root,
    )
    assert applied.returncode == 0, applied.stderr

    result = run_cli(
        "destroy",
        "plans/plan_base.py",
        "-i",
        instance.slug,
        "--permanent",
        cwd=git_workspace.root,
    )

    assert result.returncode == 1
    (error,) = [line for line in result.stderr.splitlines() if "Error" in line]
    assert error.startswith("Error: 3 object(s) managed by plans ")
    assert error.endswith(" reference objects this plan manages.")
    assert "'child' (plans/plan_child.py)" in error
    assert "'other' (plans/plan_other.py)" in error
    # With several plans each reference names the one holding it.
    assert "name='p2_c'" in result.stderr
    for name, plan in (("p2_b", "child"), ("p2_c", "other"), ("P2Schema", "child")):
        (line,) = [line for line in result.stderr.splitlines() if f"'{name}'" in line]
        assert line.endswith(f"  [plan '{plan}']")
    # The suggestion keeps --permanent.
    assert (
        "Destroy them together: lamin evolve destroy plans/plan_child.py "
        f"plans/plan_other.py plans/plan_base.py -i {instance.slug} --permanent\n"
    ) in result.stderr
    assert "--force" not in result.stderr
    assert ln.Feature.filter(name__in=["p2_ft", "p2_a", "p2_b", "p2_c"]).count() == 4


def test_destroy_still_offers_to_trash_references_no_plan_holds(
    instance, ln, run_cli, git_workspace
) -> None:
    _apply_prerequisite_plans(instance, run_cli, git_workspace)
    stray = ln.Feature(name="p2_stray", dtype=str).save()
    outside = ln.Schema(
        name="P2Outside", features=[ln.Feature.get(name="p2_a"), stray]
    ).save()

    # Destroying the base plan alone: the child's references refuse it, and
    # the one no plan holds is counted alongside.
    refused = run_cli(
        "destroy",
        "plans/plan_base.py",
        "-i",
        instance.slug,
        input="y\n",
        cwd=git_workspace.root,
    )

    assert refused.returncode == 1
    assert "Error: 2 object(s) managed by plan 'child'" in refused.stderr
    assert "1 other reference(s) come from objects no plan manages." in (refused.stderr)
    assert "[y/N]" not in refused.stdout + refused.stderr
    assert _branch_of(ln.Schema, outside.uid) == 1

    # Together with the child, only the reference no plan holds remains, and
    # it gets the inline trash offer as before.
    result = run_cli(
        "destroy",
        "plans/plan_child.py",
        "plans/plan_base.py",
        "-i",
        instance.slug,
        input="y\n",
        cwd=git_workspace.root,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "Error: 1 object(s) outside these plans reference objects they manage."
    ) in result.stderr
    assert "Move up to 1 referencing record(s) to the trash" in result.stdout
    assert "managed by plan" not in result.stderr
    assert _branch_of(ln.Schema, outside.uid) == -1
    assert not ln.Feature.filter(name__in=["p2_ft", "p2_a", "p2_b"]).exists()


_RENAME_PLAN = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan

PLAN_TRANSFORM_STEM_UID = "renameP3UID1"


@create_plan
def p3(ctx: PlanContext, exports: None) -> None:
    Feature("{name}", dtype=LaminDType(str), type=FeatureType("p3_ft"))
"""

_RENAME_SIBLING_PLAN = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan

PLAN_TRANSFORM_STEM_UID = "renameP3UID2"


@create_plan
def p3_sibling(ctx: PlanContext, exports: None) -> None:
    Feature("p3_sibling_col", dtype=LaminDType(int))
"""

_RENAME_REFUSAL = (
    "Error: refusing to apply 'p3': 1 change(s) look like a rename but were not "
    "declared with ctx.moved, so applying would delete the old object and create "
    "a new one (losing its uid and everything validated against it): Feature "
    "'p3_a' -> 'p3_a_renamed'"
)


def _rename_without_moved(instance, run_cli, git_workspace, **extra: str) -> Path:
    """Apply plan `p3` with `p3_a`, then rename it locally without ctx.moved."""
    (plan_file, *_) = git_workspace.publish(
        {"plan_p3.py": _RENAME_PLAN.replace("{name}", "p3_a"), **extra}
    )
    applied = run_cli("apply", str(plan_file), "-i", instance.slug, "-y")
    assert applied.returncode == 0, applied.stderr
    plan_file.write_text(
        _RENAME_PLAN.replace("{name}", "p3_a_renamed"), encoding="utf-8"
    )
    return plan_file


def _assert_rename_not_applied(ln) -> None:
    assert ln.Feature.filter(name="p3_a", is_type=False).exists()
    assert not ln.Feature.filter(name="p3_a_renamed").exists()


def test_plan_fails_where_apply_refuses_an_undeclared_rename(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _rename_without_moved(instance, run_cli, git_workspace)

    result = run_cli("plan", str(plan_file), "-i", instance.slug)

    assert result.returncode == 1
    output = _collapse_rich_output(result.stdout)
    assert "+ Feature 'p3_a_renamed'" in output
    assert "- Feature 'p3_a'" in output
    assert "looks like a rename of deleted Feature 'p3_a'" in output
    refusal = _collapse_rich_output(result.stderr)
    assert _RENAME_REFUSAL in refusal
    assert "Traceback" not in result.stderr

    # It is the refusal the apply it predicts raises.
    git_workspace.publish({"plan_p3.py": plan_file.read_text(encoding="utf-8")})
    applied = run_cli("apply", str(plan_file), "-i", instance.slug, "-y")

    assert applied.returncode == 1
    assert _collapse_rich_output(applied.stderr) == refusal
    _assert_rename_not_applied(ln)


def test_plan_no_strict_renames_only_warns_of_an_undeclared_rename(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _rename_without_moved(instance, run_cli, git_workspace)

    result = run_cli("plan", str(plan_file), "-i", instance.slug, "--no-strict-renames")

    assert result.returncode == 0, result.stderr
    output = _collapse_rich_output(result.stdout)
    assert "+ Feature 'p3_a_renamed'" in output
    assert "WARNING: looks like a rename of deleted Feature 'p3_a'" in output
    assert "refusing" not in output + result.stderr
    # Bannered as the rename it looks like, not as drift.
    assert (
        "! 1 change(s) look like a rename not declared with ctx.moved; review the "
        "WARNING lines above."
    ) in output
    assert "changed outside the plan" not in output


def test_plan_preview_migrations_refuses_an_undeclared_rename(
    instance, ln, run_cli, git_workspace
) -> None:
    plan_file = _rename_without_moved(instance, run_cli, git_workspace)

    result = run_cli(
        "plan", str(plan_file), "-i", instance.slug, "--preview-migrations"
    )

    assert result.returncode == 1
    assert "+ Feature 'p3_a_renamed'" in _collapse_rich_output(result.stdout)
    assert _RENAME_REFUSAL in _collapse_rich_output(result.stderr)
    # The rehearsal applied the rename and rolled it back.
    _assert_rename_not_applied(ln)


@pytest.mark.parametrize("output_format", ["markdown", "json"])
def test_plan_writes_every_plan_before_failing_on_an_undeclared_rename(
    instance, ln, run_cli, git_workspace, tmp_path: Path, output_format: str
) -> None:
    plan_file = _rename_without_moved(
        instance,
        run_cli,
        git_workspace,
        **{"plan_p3_sibling.py": _RENAME_SIBLING_PLAN},
    )
    sibling = plan_file.with_name("plan_p3_sibling.py")
    out = tmp_path / f"plan.{output_format}"

    result = run_cli(
        "plan",
        str(plan_file),
        str(sibling),
        "-i",
        instance.slug,
        "-f",
        output_format,
        "-o",
        str(out),
    )

    assert result.returncode == 1
    document = out.read_text(encoding="utf-8")
    assert document in result.stdout
    if output_format == "json":
        titles = [section["title"] for section in json.loads(document)]
        assert titles == ["Plan: p3", "Plan: p3_sibling"]
    else:
        assert "### Plan: p3\n" in document
        assert "### Plan: p3_sibling" in document
    assert "p3_a_renamed" in document
    assert "p3_sibling_col" in document
    assert _RENAME_REFUSAL in _collapse_rich_output(result.stderr)
    assert "p3_sibling" not in result.stderr


@pytest.mark.parametrize(("flags", "code"), [((), 1), (("--no-strict-renames",), 0)])
def test_ci_plan_fails_where_ci_apply_refuses_an_undeclared_rename(
    instance,
    ln,
    run_cli,
    git_workspace,
    tmp_path: Path,
    flags: tuple[str, ...],
    code: int,
) -> None:
    config = f"instances:\n  - slug: {instance.slug}\n    plans: [plan_p3.py]\n"
    _rename_without_moved(
        instance, run_cli, git_workspace, **{"lamin_schemas.yml": config}
    )
    out = tmp_path / "report.md"

    result = run_cli(
        "ci",
        "plan",
        "-c",
        "lamin_schemas.yml",
        "-o",
        str(out),
        *flags,
        cwd=git_workspace.root,
    )

    assert result.returncode == code, result.stderr
    report = out.read_text(encoding="utf-8")
    assert "+ Feature 'p3_a_renamed'" in report
    assert "! WARNING: looks like a rename of deleted Feature 'p3_a'" in report
    if code:
        assert "Validation failed" in report
        heading = f"#### \u274c `plan_p3.py` \u2192 `{instance.slug}`"
        refusal = "refusing to apply 'p3': 1 change(s) look like a rename"
        # The refused diff is shown, and the refusal quoted below it.
        assert report.index(heading) < report.index("p3_a_renamed")
        assert report.index("p3_a_renamed") < report.index(refusal)
        assert "Feature 'p3_a' -> 'p3_a_renamed'" in report
    else:
        assert "Plan shows pending changes" in report
        assert "refusing" not in report
    _assert_rename_not_applied(ln)


def test_strict_renames_false_in_the_config_lets_ci_plan_and_ci_apply_rename(
    instance, ln, run_cli, git_workspace, tmp_path: Path
) -> None:
    config = (
        "strict_renames: false\n"
        f"instances:\n  - slug: {instance.slug}\n    plans: [plan_p3.py]\n"
    )
    plan_file = _rename_without_moved(
        instance, run_cli, git_workspace, **{"lamin_schemas.yml": config}
    )
    out = tmp_path / "report.md"

    planned = run_cli(
        "ci", "plan", "-c", "lamin_schemas.yml", "-o", str(out), cwd=git_workspace.root
    )

    assert planned.returncode == 0, planned.stderr
    report = out.read_text(encoding="utf-8")
    assert "! WARNING: looks like a rename of deleted Feature 'p3_a'" in report
    assert "Plan shows pending changes" in report
    assert "refusing" not in report

    git_workspace.publish({"plan_p3.py": plan_file.read_text(encoding="utf-8")})
    applied = run_cli("ci", "apply", "-c", "lamin_schemas.yml", cwd=git_workspace.root)

    assert applied.returncode == 0, applied.stderr
    assert ln.Feature.filter(name="p3_a_renamed", is_type=False).exists()
    assert not ln.Feature.filter(name="p3_a", is_type=False).exists()


def test_apply_config_strict_renames_false_applies_an_undeclared_rename(
    instance, ln, run_cli, git_workspace
) -> None:
    config = (
        "strict_renames: false\n"
        f"instances:\n  - slug: {instance.slug}\n    plans: [plan_p3.py]\n"
    )
    plan_file = _rename_without_moved(
        instance, run_cli, git_workspace, **{"lamin_schemas.yml": config}
    )
    git_workspace.publish({"plan_p3.py": plan_file.read_text(encoding="utf-8")})
    args = ("apply", str(plan_file), "-i", instance.slug, "-y")

    refused = run_cli(*args)
    _assert_rename_not_applied(ln)
    applied = run_cli(*args, "-c", str(git_workspace.root / "lamin_schemas.yml"))

    assert refused.returncode == 1
    assert _RENAME_REFUSAL in _collapse_rich_output(refused.stderr)
    assert applied.returncode == 0, applied.stderr
    assert ln.Feature.filter(name="p3_a_renamed", is_type=False).exists()
    assert not ln.Feature.filter(name="p3_a", is_type=False).exists()


_NARROW_PLAN = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan

PLAN_TRANSFORM_STEM_UID = "narrowP4UID1"


@create_plan
def p4(ctx: PlanContext, exports: None) -> None:
    Feature("p4_col", dtype=LaminDType({dtype}))
"""

_NARROWING_HINT = (
    "could be applied with --allow-data-validated-changes: feature has no stored values"
)
_NARROWING = ("~ Feature 'p4_col'", "dtype 'str' \u2192 'int'")


def _narrow_without_values(instance, run_cli, git_workspace, **extra: str) -> Path:
    """Apply plan `p4` with `p4_col: str`, then narrow it to `int` locally."""
    (plan_file, *_) = git_workspace.publish(
        {"plan_p4.py": _NARROW_PLAN.replace("{dtype}", "str"), **extra}
    )
    applied = run_cli("apply", str(plan_file), "-i", instance.slug, "-y")
    assert applied.returncode == 0, applied.stderr
    plan_file.write_text(_NARROW_PLAN.replace("{dtype}", "int"), encoding="utf-8")
    return plan_file


def _p4_dtype(ln) -> str:
    return ln.Feature.get(name="p4_col", is_type=False).dtype_as_str


@pytest.mark.parametrize("flags", [(), ("--preview-migrations",)])
def test_plan_allow_data_validated_changes_shows_the_narrowing_apply_applies(
    instance, ln, run_cli, git_workspace, flags: tuple[str, ...]
) -> None:
    plan_file = _narrow_without_values(instance, run_cli, git_workspace)
    args = ("plan", str(plan_file), "-i", instance.slug, *flags)

    blocked = run_cli(*args)
    allowed = run_cli(*args, "--allow-data-validated-changes")

    assert blocked.returncode == 1
    assert _NARROWING_HINT in _collapse_rich_output(blocked.stderr)
    assert allowed.returncode == 0, allowed.stderr
    diff = _collapse_rich_output(allowed.stdout)
    assert all(line in diff for line in _NARROWING), diff
    assert _p4_dtype(ln) == "str"

    # It is the diff the apply it predicts shows, and applies.
    git_workspace.publish({"plan_p4.py": plan_file.read_text(encoding="utf-8")})
    applied = run_cli(
        "apply",
        str(plan_file),
        "-i",
        instance.slug,
        "-y",
        "--allow-data-validated-changes",
    )

    assert applied.returncode == 0, applied.stderr
    assert all(line in _collapse_rich_output(applied.stdout) for line in _NARROWING)
    assert _p4_dtype(ln) == "int"


@pytest.mark.parametrize("command", ["plan", "apply"])
def test_a_refused_narrowing_reads_as_a_plan_change_not_as_drift(
    instance, ln, run_cli, git_workspace, command: str
) -> None:
    # It used to read "configuration drift for 'Feature:p4_col': ... dtype: lock
    # has 'builtins.str', plan wants 'builtins.int'": nothing changed on
    # the instance, and the diff spells the same change "dtype 'str' -> 'int'".
    plan_file = _narrow_without_values(instance, run_cli, git_workspace)
    git_workspace.publish({"plan_p4.py": plan_file.read_text(encoding="utf-8")})
    yes = ("-y",) if command == "apply" else ()

    result = run_cli(command, str(plan_file), "-i", instance.slug, *yes)

    assert result.returncode == 1
    stderr = _collapse_rich_output(result.stderr)
    assert (
        "Error: cannot change Feature 'p4_col' (Feature:p4_col) in place: "
        f"dtype 'str' \u2192 'int' ({_NARROWING_HINT})"
    ) in stderr
    assert "drift" not in stderr
    assert "builtins" not in stderr
    assert "Traceback" not in stderr
    assert _p4_dtype(ln) == "str"


@pytest.mark.parametrize("command", ["plan", "apply"])
def test_a_refusal_checked_against_the_instance_reads_as_a_plan_change(
    instance, ln, run_cli, git_workspace, tmp_path: Path, command: str
) -> None:
    # The lock check lets a set flag through to the diff against the instance,
    # which used to word its refusal "drift for ...: the instance diverges from
    # the plan ... ordered_set: instance has False, plan wants True".
    plan_file = _apply_cli_plan(instance, run_cli, git_workspace)
    data = tmp_path / "frame.txt"
    data.write_text("frame\n", encoding="utf-8")
    artifact = ln.Artifact(data, description="validated").save()
    artifact.schema = ln.Schema.get(name="CliSchema")
    artifact.save()
    ordered = _PLAN_CODE.replace(
        'Schema(name="CliSchema", features=[f])',
        'Schema(name="CliSchema", features=[f], ordered_set=True)',
    )
    git_workspace.publish({"plan_cli.py": ordered})
    yes = ("-y",) if command == "apply" else ()

    result = run_cli(command, str(plan_file), "-i", instance.slug, *yes)

    assert result.returncode == 1
    stderr = _collapse_rich_output(result.stderr)
    assert (
        "Error: cannot change Schema 'CliSchema' (Schema:CliSchema) in place: "
        "ordered_set False \u2192 True (the column order of the data it validated "
        "is not recorded; declare a ctx.migrate with accept_ordered_set(reason=...))"
    ) in stderr
    assert "drift" not in stderr
    assert "diverges" not in stderr
    assert "Traceback" not in stderr
    assert ln.Schema.get(name="CliSchema").ordered_set is False


def test_plan_fail_on_diff_fails_only_while_changes_are_pending(
    instance, ln, run_cli, git_workspace, tmp_path: Path
) -> None:
    (plan_file,) = git_workspace.publish(
        {"plan_p4.py": _NARROW_PLAN.replace("{dtype}", "str")}
    )
    out = tmp_path / "plan.md"
    plan = ("plan", str(plan_file), "-i", instance.slug)

    pending = run_cli(*plan, "--fail-on-diff", "-f", "markdown", "-o", str(out))
    tolerated = run_cli(*plan)
    applied = run_cli("apply", str(plan_file), "-i", instance.slug, "-y")
    settled = run_cli(*plan, "--fail-on-diff")

    assert pending.returncode == 1
    # The diff is still printed and written before the run fails.
    document = out.read_text(encoding="utf-8")
    assert "+ Feature 'p4_col'" in document
    assert document in pending.stdout
    assert "Traceback" not in pending.stderr
    assert tolerated.returncode == 0, tolerated.stderr
    assert applied.returncode == 0, applied.stderr
    assert settled.returncode == 0, settled.stderr
    assert "0 to create, 0 to update, 0 to delete" in settled.stdout


@pytest.mark.parametrize(("flags", "code"), [((), 1), (("--no-strict-uids",), 0)])
def test_plan_fail_on_diff_counts_a_misaligned_uid_under_strict_uids(
    instance,
    ln,
    run_cli,
    git_workspace,
    flags: tuple[str, ...],
    code: int,
) -> None:
    (plan_file,) = git_workspace.publish(
        {"plan_p4.py": _NARROW_PLAN.replace("{dtype}", "str")}
    )
    applied = run_cli("apply", str(plan_file), "-i", instance.slug, "-y")
    assert applied.returncode == 0, applied.stderr
    _misalign_p4(plan_file)

    result = run_cli(
        "plan", str(plan_file), "-i", instance.slug, "--fail-on-diff", *flags
    )

    assert result.returncode == code, result.stderr
    output = _collapse_rich_output(result.stdout)
    assert "0 to create, 0 to update, 0 to delete" in output
    assert "1 uid(s) misaligned" in output


def test_plan_still_lists_an_unchanged_object_whose_uid_is_misaligned(
    instance, ln, run_cli, git_workspace
) -> None:
    (plan_file,) = git_workspace.publish(
        {"plan_p4.py": _NARROW_PLAN.replace("{dtype}", "str")}
    )
    applied = run_cli("apply", str(plan_file), "-i", instance.slug, "-y")
    assert applied.returncode == 0, applied.stderr
    _misalign_p4(plan_file)

    result = run_cli("plan", str(plan_file), "-i", instance.slug, "--no-strict-uids")

    assert result.returncode == 0, result.stderr
    assert "\n    Feature 'p4_col'  " in result.stdout
    assert "\u2260 UID: " in result.stdout
    assert "0 to delete, 1 unchanged, 1 uid(s) misaligned" in result.stdout
    assert _UP_TO_DATE not in result.stdout


def _misalign_p4(plan_file: Path) -> None:
    """Record another uid for `p4_col` in a lock for a second instance."""
    (own,) = plan_file.parent.glob("plan_p4.*.lock")
    entry = LockFile.read(own).objects["Feature:p4_col"]
    other = LockFile(plan="p4")
    other.add(
        LockEntry(key=entry.key, kind=entry.kind, name=entry.name, uid="G1misalign00")
    )
    other.write(lock_path(plan_file, "acme-general"))


def test_apply_config_strict_uids_false_applies_despite_a_misaligned_uid(
    instance, ln, run_cli, git_workspace
) -> None:
    config = (
        "strict_uids: false\n"
        f"instances:\n  - slug: {instance.slug}\n    plans: [plan_p4.py]\n"
    )
    (plan_file, config_file) = git_workspace.publish(
        {"plan_p4.py": _NARROW_PLAN.replace("{dtype}", "str"), "c.yml": config}
    )
    applied = run_cli("apply", str(plan_file), "-i", instance.slug, "-y")
    assert applied.returncode == 0, applied.stderr
    _misalign_p4(plan_file)
    args = ("apply", str(plan_file), "-i", instance.slug, "-y")

    refused = run_cli(*args)
    relaxed = run_cli(*args, "-c", str(config_file))

    assert refused.returncode == 1
    assert "repair align-uids" in _collapse_rich_output(refused.stderr)
    assert relaxed.returncode == 0, relaxed.stderr
    # Nothing to apply but a misaligned uid: listed, and said once.
    assert "\n    Feature 'p4_col'  " in relaxed.stdout
    assert relaxed.stdout.count(_UP_TO_DATE) == 1


def test_apply_config_allow_data_validated_changes_applies_a_narrowing(
    instance, ln, run_cli, git_workspace
) -> None:
    config = (
        "allow_data_validated_changes: true\n"
        f"instances:\n  - slug: {instance.slug}\n    plans: [plan_p4.py]\n"
    )
    plan_file = _narrow_without_values(
        instance, run_cli, git_workspace, **{"lamin_schemas.yml": config}
    )
    git_workspace.publish({"plan_p4.py": plan_file.read_text(encoding="utf-8")})
    args = ("apply", str(plan_file), "-i", instance.slug, "-y")

    blocked = run_cli(*args)
    assert _p4_dtype(ln) == "str"
    applied = run_cli(*args, "-c", str(git_workspace.root / "lamin_schemas.yml"))

    assert blocked.returncode == 1
    assert _NARROWING_HINT in _collapse_rich_output(blocked.stderr)
    assert applied.returncode == 0, applied.stderr
    assert all(line in _collapse_rich_output(applied.stdout) for line in _NARROWING)
    assert _p4_dtype(ln) == "int"


@pytest.mark.parametrize(
    ("flags", "code"), [((), 1), (("--allow-data-validated-changes",), 0)]
)
def test_ci_plan_allow_data_validated_changes_lifts_a_data_proven_block(
    instance,
    ln,
    run_cli,
    git_workspace,
    tmp_path: Path,
    flags: tuple[str, ...],
    code: int,
) -> None:
    config = f"instances:\n  - slug: {instance.slug}\n    plans: [plan_p4.py]\n"
    _narrow_without_values(
        instance, run_cli, git_workspace, **{"lamin_schemas.yml": config}
    )
    out = tmp_path / "report.md"

    result = run_cli(
        "ci",
        "plan",
        "-c",
        "lamin_schemas.yml",
        "-o",
        str(out),
        *flags,
        cwd=git_workspace.root,
    )

    assert result.returncode == code, result.stderr
    report = out.read_text(encoding="utf-8")
    if code:
        assert _NARROWING_HINT in " ".join(report.split())
    else:
        assert "~ Feature 'p4_col'" in report
        assert "dtype 'str' \u2192 'int'" in report
        assert "Plan shows pending changes" in report
    assert _p4_dtype(ln) == "str"


def test_the_whole_authoring_surface_is_reachable_from_the_top_level() -> None:
    """A plan file should have one import source, not six."""
    import lamindb_setup.evolve as pkg

    # the two authoring styles, the plan machinery, and the dtype vocabulary
    for name in (
        "Feature",
        "FeatureType",
        "Schema",
        "SchemaType",
        "Record",
        "RecordType",
        "Sheet",
        "SchemaModel",
        "SchemaConfig",
        "FeatureGroup",
        "GroupConfig",
        "feature",
        "Plan",
        "PlanContext",
        "PlanExports",
        "create_plan",
        "SpaceUID",
        "CatDType",
        "RecordCat",
        "CellTypeCat",
        "UnionCat",
        "url",
        "LaminSchemasError",
    ):
        assert name in pkg.__all__, f"{name} missing from __all__"
        assert getattr(pkg, name, None) is not None, f"{name} not importable"


def test_the_submodule_paths_still_work() -> None:
    """The top level is the interface: every re-export is the submodule's object."""
    import lamindb_setup.evolve as pkg
    from lamindb_setup.evolve.authoring._declarative import SchemaModel
    from lamindb_setup.evolve.authoring._dtypes import RecordCat
    from lamindb_setup.evolve.core import SpaceUID

    assert pkg.SchemaModel is SchemaModel
    assert pkg.RecordCat is RecordCat
    assert pkg.create_plan is create_plan
    assert pkg.SpaceUID is SpaceUID


def test_every_dtype_alias_is_re_exported() -> None:
    """The per-registry Cat aliases are the vocabulary a declarative plan uses."""
    import lamindb_setup.evolve as pkg
    from lamindb_setup.evolve.authoring import _dtypes as dtypes

    missing = sorted(set(dtypes.__all__) - set(pkg.__all__))
    assert missing == []


def test_the_scaffolded_plan_imports_only_from_the_top_level(tmp_path: Path) -> None:
    from click.testing import CliRunner
    from lamin_cli.evolve._commands import app

    result = CliRunner().invoke(app, ["init", "onesource", "-d", str(tmp_path)])
    assert result.exit_code == 0, result.output

    source = (tmp_path / "plan_onesource.py").read_text(encoding="utf-8")
    sources = {
        line.split(" import ")[0].removeprefix("from ")
        for line in source.splitlines()
        if line.startswith("from lamindb_setup.evolve")
    }
    # only the package itself, plus the summary helper the __main__ block uses
    assert sources == {"lamindb_setup.evolve", "lamindb_setup.evolve.render"}


_PREVIEW_PLAN_V1 = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import create_plan

PLAN_TRANSFORM_STEM_UID = "testUID00077"


@create_plan
def preview_plan(ctx: PlanContext, exports: None) -> None:
    Feature("pv_cli", dtype=LaminDType(str), type=FeatureType("pv_cli_ft"))
"""

_PREVIEW_PLAN_V2 = """
from lamindb_setup.evolve import Feature
from lamindb_setup.evolve import FeatureType
from lamindb_setup.evolve import LaminDType
from lamindb_setup.evolve import PlanContext
from lamindb_setup.evolve import Record
from lamindb_setup.evolve import RecordType
from lamindb_setup.evolve import create_plan
from lamindb_setup.evolve import map_to_records

PLAN_TRANSFORM_STEM_UID = "testUID00077"


@create_plan
def preview_plan(ctx: PlanContext, exports: None) -> None:
    import lamindb as ln

    vocabulary = RecordType("PvCliVocab")
    Record("alpha", type=vocabulary)
    feature = Feature(
        "pv_cli",
        dtype=LaminDType(ln.Record, type__uid=vocabulary.uid),
        type=FeatureType("pv_cli_ft"),
    )
    ctx.migrate(feature, map_to_records())
"""


def test_plan_preview_migrations_rehearses_a_target_the_plan_creates(
    instance, ln, run_cli, git_workspace
) -> None:
    # The record type and its record are new, which a per-migration dry-run
    # could not reach.
    (plan_file,) = git_workspace.publish({"plan_pv.py": _PREVIEW_PLAN_V1})
    applied = run_cli("apply", str(plan_file), "-i", instance.slug, "-y")
    assert applied.returncode == 0, applied.stderr
    sheet = ln.Record(name="PvCliSheet", is_type=True).save()
    ln.Record(name="row", type=sheet, features={"pv_cli": "alpha"}).save()
    git_workspace.publish({"plan_pv.py": _PREVIEW_PLAN_V2})

    result = run_cli(
        "plan", str(plan_file), "-i", instance.slug, "--preview-migrations"
    )

    assert result.returncode == 0, result.stderr
    assert "map_to_records: 1 matched" in _collapse_rich_output(result.stdout)
    assert ln.Feature.get(name="pv_cli", is_type=False).dtype_as_str == "str"
    assert ln.Record.filter(name="PvCliVocab", is_type=True).one_or_none() is None


def test_plan_preview_migrations_honours_no_strict_uids(
    instance, ln, run_cli, tmp_path: Path
) -> None:
    """The rehearsal refuses a uid held here as `apply` does, and lifts it the same way."""
    plan_file = _make_plan_file(tmp_path, "su")
    # A plain feature, so it cannot be the feature type the uid belongs to.
    holder = ln.Feature(name="su_other", dtype=str).save()
    other = LockFile(plan="su_plan")
    other.add(
        LockEntry(
            key="FeatureType:su_ft", kind="FeatureType", name="su_ft", uid=holder.uid
        )
    )
    other.write(lock_path(plan_file, "acme-general"))
    args = ("plan", str(plan_file), "-i", instance.slug, "--preview-migrations")

    strict = run_cli(*args)
    relaxed = run_cli(*args, "--no-strict-uids")

    assert strict.returncode != 0
    assert "--no-strict-uids" in _collapse_rich_output(strict.stderr)
    assert relaxed.returncode == 0, relaxed.stderr
    assert not ln.Feature.filter(name="su_ft").exists()


def test_plan_no_strict_uids_shows_the_fresh_uid_caveat_apply_previews(
    instance, ln, run_cli, tmp_path: Path
) -> None:
    plan_file = _make_plan_file(tmp_path, "su")
    holder = ln.Feature(name="su_other", dtype=str).save()
    other = LockFile(plan="su_plan")
    other.add(
        LockEntry(
            key="FeatureType:su_ft", kind="FeatureType", name="su_ft", uid=holder.uid
        )
    )
    other.write(lock_path(plan_file, "acme-general"))
    args = ("plan", str(plan_file), "-i", instance.slug)

    strict = run_cli(*args)
    relaxed = run_cli(*args, "--no-strict-uids")

    caveat = "WARNING: created with a fresh uid instead (--no-strict-uids)"
    assert strict.returncode == 0, strict.stderr
    assert "1 uid(s) misaligned" in _collapse_rich_output(strict.stdout)
    assert caveat not in _collapse_rich_output(strict.stdout)
    assert relaxed.returncode == 0, relaxed.stderr
    assert caveat in _collapse_rich_output(relaxed.stdout)


def test_plan_preview_migrations_rehearses_listed_plans_together(
    instance, ln, run_cli, tmp_path: Path
) -> None:
    """A plan adopts what its prerequisite's rehearsal created, as in `apply`."""
    upstream = _make_plan_file(tmp_path, "up")
    downstream = _make_dependent_plan_file(tmp_path, "down", depends_on="up")

    alone = run_cli(
        "plan", str(downstream), "-i", instance.slug, "--preview-migrations"
    )
    together = run_cli(
        "plan",
        str(downstream),
        str(upstream),
        "-i",
        instance.slug,
        "--preview-migrations",
    )

    # Rehearsed on its own, the dependent plan fails as its apply would.
    assert alone.returncode != 0
    assert "Apply that plan first" in _collapse_rich_output(alone.stderr)
    assert together.returncode == 0, together.stderr
    assert together.stdout.index("Plan: up_plan") < together.stdout.index(
        "Plan: down_plan"
    )
    assert not ln.Feature.filter(name__in=["up_ft", "down_sub"]).exists()


def test_profile_option_writes_text_report(tmp_path: Path) -> None:
    report = tmp_path / "profile.txt"
    plan_file = tmp_path / "plan_cli.py"
    # Give the sampling profiler time to observe the plan during validation.
    plan_file.write_text("import time\ntime.sleep(0.05)\n" + _PLAN_CODE)

    result = _run_cli("--profile", str(report), "validate", str(plan_file))

    assert result.returncode == 0, result.stderr
    assert report.exists()
    text = report.read_text()
    assert "Duration:" in text
    assert f"{plan_file.name}:" in text


def test_profile_option_writes_html_for_html_suffix(tmp_path: Path) -> None:
    report = tmp_path / "profile.html"

    result = _run_cli("--profile", str(report), "utils", "generate-stem-uid")

    assert result.returncode == 0, result.stderr
    assert report.read_text().lstrip().startswith("<!DOCTYPE html>")


def test_profile_option_writes_report_when_command_fails(tmp_path: Path) -> None:
    # A slow run is usually a failing one, so the report has to survive a
    # non-zero exit -- it is written from a context-close hook for this reason.
    report = tmp_path / "nested" / "profile.txt"

    result = _run_cli("--profile", str(report), "validate", str(tmp_path / "nope.py"))

    assert result.returncode != 0
    assert report.exists()
