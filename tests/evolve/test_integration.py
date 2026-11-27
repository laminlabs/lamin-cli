"""Integration boundaries specific to the installed Lamin CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _support.output import command_output

_SUPPORT = Path(__file__).parent / "_support" / "cli_child.py"
_PLAN = """from {setup_package} import evolve
PLAN_TRANSFORM_STEM_UID = "testUID00001"
@evolve.create_plan
def demo(ctx: evolve.PlanContext, exports: None) -> None:
    evolve.FeatureType("Measurements")
"""


def _python(code: str, tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    bootstrap = f"""import runpy, sys
helpers = runpy.run_path({str(_SUPPORT)!r})
sys.addaudithook(helpers["_audit_hook"]([]))
"""
    return subprocess.run(
        [sys.executable, "-c", bootstrap + code],
        cwd=tmp_path,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("plain", [False, True])
def test_root_help_does_not_import_lamindb(tmp_path, plain):
    result = _python(
        """from lamin_cli.__main__ import main
main.main(args=["--help"], prog_name="lamin", standalone_mode=False)
assert "evolve" in main.commands
assert "schema" not in main.commands
assert "lamindb" not in sys.modules
assert "lamin_cli.evolve._commands" not in sys.modules
assert "typer" not in sys.modules
""",
        tmp_path,
        NO_RICH="1" if plain else "",
        COLUMNS="200",
        LAMIN_CURRENT_INSTANCE="unconfigured/default",
    )
    assert result.returncode == 0, result.stderr
    assert "evolve" in result.stdout
    assert "connect" in result.stdout


@pytest.mark.parametrize("plain", [False, True])
@pytest.mark.parametrize("setup_package", ["lamindb_setup", "lamindb.setup"])
def test_validate_uses_click_and_masks_default(tmp_path, run_cli, plain, setup_package):
    plan = tmp_path / "plan_demo.py"
    plan.write_text(_PLAN.format(setup_package=setup_package))
    result = run_cli(
        "validate",
        str(plan),
        "-f",
        "json",
        env={
            "NO_RICH": "1" if plain else "",
            "LAMIN_CURRENT_INSTANCE": "unconfigured/default",
        },
        faults=[{"event": "import", "match": "typer", "raise": "AssertionError"}],
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(command_output(result.stdout))
    assert payload
    assert "Measurements" in result.stdout


def test_missing_profile_dependency_has_actionable_error(tmp_path, run_cli):
    result = run_cli(
        "--profile",
        str(tmp_path / "profile.txt"),
        "utils",
        "generate-stem-uid",
        faults=[{"event": "import", "match": "pyinstrument", "raise": "ImportError"}],
    )
    assert result.returncode == 1
    assert "--profile needs pyinstrument" in result.stderr
    assert "lamin-cli[profile]" in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_backend_has_actionable_error(tmp_path):
    result = _python(
        """class MissingBackend:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "lamindb_setup.evolve":
            raise ModuleNotFoundError("No schema evolution backend", name=fullname)
sys.meta_path.insert(0, MissingBackend())
from lamin_cli.__main__ import main
main.main(args=["evolve", "--help"], prog_name="lamin")
""",
        tmp_path,
    )
    assert result.returncode == 1
    assert "requires a lamindb-setup build with evolve" in result.stderr
    assert "Traceback" not in result.stderr


def test_evolve_option_completion(tmp_path):
    result = _python(
        """from lamin_cli.__main__ import main
main(prog_name="lamin")
""",
        tmp_path,
        _LAMIN_COMPLETE="bash_complete",
        COMP_WORDS="lamin evolve plan --",
        COMP_CWORD="3",
        LAMIN_CURRENT_INSTANCE="unconfigured/default",
    )
    assert result.returncode == 0, result.stderr
    assert "--instance" in result.stdout
    assert "--preview-migrations" in result.stdout


def test_plan_apply_then_check_for_pending_changes(
    instance, ln, run_cli, git_workspace, tmp_path
):
    (plan,) = git_workspace.publish(
        {"plan_demo.py": _PLAN.format(setup_package="lamindb_setup")}
    )
    args = (str(plan), "-i", instance.slug)

    preview = run_cli("plan", *args, "--fail-on-diff")
    assert preview.returncode == 1, preview.stderr
    assert "Measurements" in preview.stdout

    report = tmp_path / "applied.json"
    applied = run_cli("apply", *args, "-f", "json", "-o", str(report), input="y\n")
    assert applied.returncode == 0, applied.stderr
    assert "[y/N]" in applied.stderr
    document = report.read_text()
    assert json.loads(document)["title"] == "Applied: demo"
    assert document in applied.stdout
    assert "[y/N]" not in applied.stdout
    assert ln.Feature.get(name="Measurements").is_type
    assert len(list(plan.parent.glob("plan_demo.*.lock"))) == 1

    settled = run_cli("plan", *args, "--fail-on-diff")
    assert settled.returncode == 0, settled.stderr
    assert "0 to create, 0 to update, 0 to delete" in settled.stdout


@pytest.mark.parametrize("plain", [False, True])
def test_evolve_help_keeps_settings_and_does_not_import_lamindb(tmp_path, plain):
    result = _python(
        """import os
import lamindb_setup as setup
from lamindb_setup.core import django
before = setup.settings._instance_settings
from lamin_cli.__main__ import main
main.main(args=["evolve", "--help"], prog_name="lamin", standalone_mode=False)
assert setup.settings._instance_settings is before
assert os.environ["LAMIN_CURRENT_INSTANCE"] == "unconfigured/default"
assert not django.IS_SETUP
assert "lamindb" not in sys.modules
assert "typer" not in sys.modules
""",
        tmp_path,
        NO_RICH="1" if plain else "",
        COLUMNS="200",
        LAMIN_CURRENT_INSTANCE="unconfigured/default",
    )
    assert result.returncode == 0, result.stderr
    assert "validate" in result.stdout
    assert "apply" in result.stdout


def test_generated_help_includes_lazy_evolve_commands(tmp_path):
    result = _python(
        """from lamin_cli.__main__ import _generate_help
helps = _generate_help()
assert helps["main evolve"]["docstring"]
for command in ("init", "validate", "plan", "apply", "introspect"):
    assert helps[f"main evolve {command}"]["docstring"]
assert "--preview-migrations" in helps["main evolve plan"]["help"]
assert "main evolve ci plan" in helps
assert "lamindb" not in sys.modules
""",
        tmp_path,
        NO_RICH="1",
        LAMIN_CURRENT_INSTANCE="unconfigured/default",
    )
    assert result.returncode == 0, result.stderr


def test_rich_help_configuration_lists_management_commands(tmp_path):
    result = _python(
        """from lamin_cli.evolve._click import Group, configure_help
group = Group(name="evolve")
configure_help(group)
config = group.context_settings["rich_help_config"]
assert config.show_arguments
assert "apply" in config.command_groups["*evolve"][0]["commands"]
""",
        tmp_path,
        NO_RICH="",
    )
    assert result.returncode == 0, result.stderr
