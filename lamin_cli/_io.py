from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import lamindb_setup as ln_setup

from lamin_cli.clone._clone_verification import (
    _count_instance_records,
)

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
@click.option("--track/--no-track", is_flag=True, help="Whether to track snapshot generation.", default=True)
# fmt: on
def snapshot(upload: bool, track: bool) -> None:
    """Create a SQLite snapshot of the connected instance."""
    if not ln_setup.settings._instance_exists:
        raise click.ClickException(
            "Not connected to an instance. Please run: lamin connect account/name"
        )

    instance_owner = ln_setup.settings.instance.owner
    instance_name = ln_setup.settings.instance.name

    ln_setup.connect(f"{instance_owner}/{instance_name}", use_root_db_user=True)

    import lamindb as ln

    original_counts = _count_instance_records()

    modules_without_lamindb = ln_setup.settings.instance.modules
    modules_complete = modules_without_lamindb.copy()
    modules_complete.add("lamindb")


    with tempfile.TemporaryDirectory() as export_dir:
        if track:
            ln.track()
        ln_setup.io.export_db(module_names=modules_complete, output_dir=export_dir)
        if track:
            ln.finish()

        script_path = (
            Path(__file__).parent / "clone" / "create_sqlite_clone_and_import_db.py"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--instance-name",
                instance_name,
                "--export-dir",
                export_dir,
                "--modules",
                ",".join(modules_without_lamindb),
                "--original-counts",
                json.dumps(original_counts),
            ],
            check=False,
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path.cwd(),
        )
        if result.returncode != 0:
            try:
                mismatches = json.loads(result.stderr.strip())
                error_msg = "Record count mismatch detected:\n" + "\n".join(
                    [f"  {table}: original={orig}, clone={clone}"
                    for table, (orig, clone) in mismatches.items()]
                )
                raise click.ClickException(error_msg)
            except (json.JSONDecodeError, AttributeError, ValueError, TypeError):
                raise click.ClickException(f"Clone verification failed:\n{result.stderr}") from None


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
