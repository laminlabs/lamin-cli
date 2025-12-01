from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import lamindb_setup as ln_setup
from lamindb._finish import save_run_logs

if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click


@click.group()
def io():
    """Import and export instances."""


# fmt: off
@io.command("snapshot")
@click.option("--upload/--no-upload", is_flag=True, help="Whether to upload the snapshot.", default=True)
# fmt: on
def snapshot(upload: bool):
    """Create and optionally upload a SQLite snapshot of the current instance."""
    if not ln_setup.settings._instance_exists:
        raise click.ClickException(
            "Not connected to an instance. Please run: lamin connect account/name"
        )

    instance_owner = ln_setup.settings.instance.owner
    instance_name = ln_setup.settings.instance.name

    ln_setup.connect(f"{instance_owner}/{instance_name}", use_root_db_user=True)

    modules_without_lamindb = ln_setup.settings.instance.modules
    modules_complete = modules_without_lamindb.copy()
    modules_complete.add("lamindb")

    import lamindb as ln

    with tempfile.TemporaryDirectory() as export_dir:
        transform = ln.Transform(
            uid="snapXYZ90pQr0004",
            key="__lamindb_snapshot__",
            type="function"
        ).save()
        ln.track(transform=transform)
        ln_setup.io.export_db(module_names=modules_complete, output_dir=export_dir)
        ln.finish()

        script_path = (
            Path(__file__).parent / "clone" / "create_sqlite_clone_and_import_db.py"
        )
        subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--instance-name",
                instance_name,
                "--export-dir",
                export_dir,
                "--modules",
                ",".join(modules_without_lamindb),
            ],
            check=True,
            cwd=Path.cwd(),
        )

        ln_setup.connect(f"{instance_owner}/{instance_name}", use_root_db_user=True)
        if upload:
            ln_setup.core._clone.upload_sqlite_clone(
                local_sqlite_path=f"{instance_name}-clone/.lamindb/lamin.db",
                compress=True,
            )

        ln_setup.disconnect()


# fmt: off
@io.command("exportdb")
@click.option("--modules", type=str, default=None, help="Comma-separated list of modules to export (e.g., 'lamindb,bionty').",)
@click.option("--output-dir", type=str, help="Output directory for exported parquet files.")
@click.option("--max-workers", type=int, default=8, help="Number of parallel workers.")
@click.option("--chunk-size", type=int, default=500_000, help="Number of rows per chunk for large tables.")
# fmt: on
def exportdb(modules: str | None, output_dir: str, max_workers: int, chunk_size: int):
    """Export registry tables to parquet files."""
    if not ln_setup.settings._instance_exists:
        raise click.ClickException(
            "Not connected to an instance. Please run: lamin connect account/name"
        )

    module_list = modules.split(",") if modules else None
    ln_setup.io.export_db(
        module_names=module_list,
        output_dir=output_dir,
        max_workers=max_workers,
        chunk_size=chunk_size,
    )


# fmt: off
@io.command("importdb")
@click.option("--modules", type=str, default=None, help="Comma-separated list of modules to import (e.g., 'lamindb,bionty').")
@click.option("--input-dir", type=str, help="Input directory containing exported parquet files.")
@click.option("--if-exists", type=click.Choice(["fail", "replace", "append"]), default="replace", help="How to handle existing data.")
# fmt: on
def importdb(modules: str | None, input_dir: str, if_exists: str):
    """Import registry tables from parquet files."""
    if not ln_setup.settings._instance_exists:
        raise click.ClickException(
            "Not connected to an instance. Please run: lamin connect account/name"
        )

    module_list = modules.split(",") if modules else None
    ln_setup.io.import_db(
        module_names=module_list,
        input_dir=input_dir,
        if_exists=if_exists,
    )
