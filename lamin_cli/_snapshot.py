from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import lamindb_setup as ln_setup

if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click


@click.group()
def snapshot():
    """Manage snapshots."""


@snapshot.command("create")
@click.option(
    "--upload/--no-upload",
    is_flag=True,
    help="Whether to upload the snapshot.",
    default=True,
)
def create(upload: bool):
    """Create a SQLite snapshot of the current instance."""
    if not ln_setup.settings._instance_exists:
        raise click.ClickException(
            "Not connected to an instance. Please run: lamin connect account/name"
        )

    instance_owner = ln_setup.settings.instance.owner
    instance_name = ln_setup.settings.instance.name
    export_dir = f"{instance_name}_export"

    ln_setup.connect(f"{instance_owner}/{instance_name}", use_root_db_user=True)

    modules_without_lamindb = ln_setup.settings.instance.modules
    modules_complete = modules_without_lamindb.copy()
    modules_complete.add("lamindb")

    import lamindb as ln

    ln.track()
    ln_setup.io.export_db(module_names=modules_complete, output_dir=export_dir)
    ln.finish()

    script_path = (
        Path(__file__).parent / "clone" / "create_sqlite_clone_and_import_db.py"
    )
    subprocess.run([sys.executable, str(script_path)], check=True)

    ln_setup.connect(f"{instance_owner}/{instance_name}", use_root_db_user=True)
    if upload:
        ln_setup.core._clone.upload_sqlite_clone(
            local_sqlite_path=f"{instance_name}-clone/.lamindb/lamin.db",
            compress=True,
        )

    shutil.rmtree(export_dir)

    ln_setup.disconnect()
    ln_setup.connect(f"{instance_owner}/{instance_name}")
