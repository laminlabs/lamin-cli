"""Integration boundaries specific to the installed Lamin CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SUPPORT = Path(__file__).parent / "_support" / "cli_child.py"
_PLAN = """from lamindb_setup.evolve import FeatureType, PlanContext, create_plan
PLAN_TRANSFORM_STEM_UID = "testUID00001"
@create_plan
def demo(ctx: PlanContext, exports: None) -> None:
    FeatureType("Measurements")
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
@pytest.mark.parametrize("namespace", ["lamindb_setup.evolve", "lamindb.evolve"])
def test_validate_uses_click_and_masks_default(tmp_path, run_cli, plain, namespace):
    plan = tmp_path / "plan_demo.py"
    plan.write_text(_PLAN.replace("lamindb_setup.evolve", namespace))
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
    payload = json.loads(result.stdout)
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


def test_introspect_generates_and_verifies_a_native_plan(
    instance, ln, run_cli, tmp_path
):
    ln.Feature(name="NativeMeasurements", is_type=True).save()

    result = run_cli("introspect", "captured", "-i", instance.slug, "-d", str(tmp_path))

    assert result.returncode == 0, result.stderr
    plan = tmp_path / "plan_captured.py"
    assert plan.exists()
    assert "lamindb_setup.evolve" in plan.read_text()
    assert "NativeMeasurements" in plan.read_text()
    validated = run_cli("validate", str(plan), "-f", "json")
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout)


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
