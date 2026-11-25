import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lamindb_setup.core.upath import UPath


def test_snapshot():
    try:
        env = os.environ
        # testuser2 has write permissions on lamin-site-assets
        _ = subprocess.run(
            "lamin login testuser2",
            shell=True,
            capture_output=True,
            env=env,
        )

        exit_status = os.system("lamin connect laminlabs/lamin-site-assets")
        assert exit_status == 0

        before_snapshot = datetime.now(timezone.utc)
        exit_status = os.system("lamin io snapshot")
        assert exit_status == 0
        after_snapshot = datetime.now(timezone.utc)

        path = UPath.from_auth("s3://lamin-site-assets/.lamindb/lamin.db.gz")
        assert path.exists()

        stat = path.stat()
        file_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

        assert (
            before_snapshot - timedelta(seconds=15)
            <= file_mtime
            <= after_snapshot + timedelta(seconds=15)
        )
    finally:
        import lamindb_setup as ln_setup

        ln_setup.disconnect(here=True)
        _ = subprocess.run(
            "lamin login testuser1",
            shell=True,
            capture_output=True,
            env=env,
        )
        ln_setup.connect("testuser1/lamin-cli-unit-tests")


def test_io_transfer_ulabel():
    import lamindb as ln

    handle = ln.setup.settings.user.handle
    source_name = "lamin-cli-transfer-source"
    source_dir = Path(source_name)
    target = "lamin-cli-unit-tests"
    uid_file = source_dir / "lamin-cli-transfer-uid.txt"

    def run(args: list[str], cwd: Path | None = None) -> None:
        subprocess.run(args, check=True, cwd=cwd)

    source_dir.mkdir()
    try:
        run(["lamin", "init"], cwd=source_dir)
        run(
            [
                "python",
                "-c",
                "import lamindb as ln; "
                f"open({uid_file.name!r}, 'w').write(ln.ULabel(name='from-source').save().uid)",
            ],
            cwd=source_dir,
        )
        uid = uid_file.read_text().strip()
        url = f"https://lamin.ai/{handle}/{source_name}/ulabel/{uid}"
        rejected = subprocess.run(
            ["lamin", "io", "sync", url, "--limit", "-3"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert rejected.returncode != 0
        assert "none" in (rejected.stderr or "") + (rejected.stdout or "")
        run(["lamin", "connect", target])
        run(["lamin", "io", "sync", url, "--limit", "5"])
        ln.connect(target)
        synced = ln.ULabel.get(uid)
        assert synced.name == "from-source"
        assert synced.run.params["limit"] == 5
    finally:
        subprocess.run(["lamin", "connect", target], check=False)
        ln.connect(target)
        ln.setup.delete(source_name, force=True)
        shutil.rmtree(source_dir, ignore_errors=True)
