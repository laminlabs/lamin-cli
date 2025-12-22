import os
import sys
from pathlib import Path

import click
from lamin_utils import logger
from lamindb_setup.core._settings_store import settings_dir


def get_current_run_file() -> Path:
    """Get the path to the file storing the current run UID."""
    return settings_dir / "current_shell_run.txt"


def is_interactive_shell() -> bool:
    """Check if running in an interactive terminal."""
    return sys.stdin.isatty() and sys.stdout.isatty() and os.isatty(0)


def get_script_filename() -> Path:
    """Try to get the filename of the calling shell script."""
    import psutil

    parent = psutil.Process(os.getppid())
    cmdline = parent.cmdline()

    # For shells like bash, sh, zsh
    if parent.name() in ["bash", "sh", "zsh", "dash"]:
        # cmdline is typically: ['/bin/bash', 'script.sh', ...]
        if len(cmdline) > 1 and not cmdline[1].startswith("-"):
            return Path(cmdline[1])
    raise click.ClickException(
        "Cannot determine script filename. Please run in an interactive shell."
    )


def track():
    import lamindb as ln

    if not ln.setup.settings._instance_exists:
        raise click.ClickException(
            "Not connected to an instance. Please run: lamin connect account/name"
        )
    path = get_script_filename()
    source_code = path.read_text()
    transform = ln.Transform(
        key=path.name, source_code=source_code, type="script"
    ).save()
    run = ln.Run(transform=transform).save()
    current_run_file = get_current_run_file()
    current_run_file.parent.mkdir(parents=True, exist_ok=True)
    current_run_file.write_text(run.uid)
    logger.important(f"started tracking shell run: {run.uid}")


def finish():
    from datetime import datetime, timezone

    import lamindb as ln

    if not ln.setup.settings._instance_exists:
        raise click.ClickException(
            "Not connected to an instance. Please run: lamin connect account/name"
        )

    current_run_file = get_current_run_file()
    if not current_run_file.exists():
        raise click.ClickException(
            "No active run to finish. Please run `lamin track` first."
        )
    run = ln.Run.get(uid=current_run_file.read_text().strip())
    run._status_code = 0
    run.finished_at = datetime.now(timezone.utc)
    run.save()
    current_run_file.unlink()
    logger.important(f"finished tracking shell run: {run.uid}")
