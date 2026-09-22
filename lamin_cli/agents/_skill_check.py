from __future__ import annotations

import re
from pathlib import Path

from lamin_cli.agents import _common

_AGENTS_SKILL = Path(".agents") / "skills" / "lamindb"
_CLAUDE_SKILL = Path(".claude") / "skills" / "lamindb"
_VERSION_RE = re.compile(r"^\s*version:\s*[\"']?([^\"'\s#]+)", re.MULTILINE)


def warn_skill_freshness(*, claude: bool = False) -> None:
    """Warn when the project LaminDB skill is missing, broken, or a stale copy.

    Only runs when cwd is the configured dev-dir or a folder under it.
    """
    try:
        from lamindb_setup import settings as ln_setup_settings
    except Exception:
        return

    dev_dir = ln_setup_settings.dev_dir
    if dev_dir is None:
        return
    dev_dir = dev_dir.resolve()
    cwd = Path.cwd().resolve()
    if cwd != dev_dir and not cwd.is_relative_to(dev_dir):
        return

    rel = _CLAUDE_SKILL if claude else _AGENTS_SKILL
    skill_dir = cwd / rel
    package_skill_dir, package_version = _package_skill()
    status = _classify(skill_dir, package_skill_dir, package_version)

    if status == "current":
        return
    if status == "stale":
        _common.warn(
            f"LaminDB skill is a stale copy. Delete {rel.as_posix()}, "
            f"then run: {_install_cmd(claude)}"
        )
        return
    if status == "broken":
        _common.warn(
            "LaminDB skill symlink is broken. "
            f"Install project dependencies, then run: {_install_cmd(claude)}"
        )
        return
    _common.warn(
        f"LaminDB skill not found in dev-dir. Run: {_install_cmd(claude)}"
    )


def _install_cmd(claude: bool) -> str:
    if claude:
        return "uvx library-skills --all --claude"
    return "uvx library-skills --all"


def _package_skill() -> tuple[Path | None, str | None]:
    try:
        import lamindb
    except Exception:
        return None, None
    skill_dir = Path(lamindb.__file__).resolve().parent / _AGENTS_SKILL
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None, None
    version = getattr(lamindb, "__skill_version__", None)
    if version is not None:
        version = str(version).strip() or None
    if version is None:
        version = _skill_file_version(skill_md)
    return skill_dir, version


def _classify(
    skill_dir: Path, package_skill_dir: Path | None, package_version: str | None
) -> str:
    if skill_dir.is_symlink():
        try:
            resolved = skill_dir.resolve()
        except OSError:
            return "broken"
        if not skill_dir.exists() or not (resolved / "SKILL.md").is_file():
            return "broken"
        if package_skill_dir is not None and resolved == package_skill_dir.resolve():
            return "current"
        if _is_stale(resolved / "SKILL.md", package_version):
            return "stale"
        return "current"
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return "missing"
    if _is_stale(skill_md, package_version):
        return "stale"
    return "current"


def _is_stale(skill_md: Path, package_version: str | None) -> bool:
    if not package_version:
        return False
    return _skill_file_version(skill_md) != package_version


def _skill_file_version(skill_md: Path) -> str | None:
    try:
        text = skill_md.read_text()
    except OSError:
        return None
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    return match.group(1).strip()
