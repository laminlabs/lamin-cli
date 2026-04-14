from __future__ import annotations

from pathlib import Path

import lamindb_setup as ln_setup
from lamin_utils import logger
from lamindb_setup._connect_instance import _connect_cli, get_owner_name_from_identifier
from lamindb_setup.core._settings_store import (
    find_local_current_instance_file,
    remove_local_current_instance,
    write_local_current_instance,
)


def connect(
    instance: str, *, here: bool = False, use_root_db_user: bool = False
) -> None:
    if not here:
        _connect_cli(instance, use_root_db_user=use_root_db_user)
        return None

    _connect_cli(
        instance,
        use_root_db_user=use_root_db_user,
        write_to_disk=False,
        show_dev_dir_hint=False,
    )
    cwd = Path.cwd().resolve()
    write_local_current_instance(cwd, ln_setup.settings.instance.slug)
    previous_dev_dir = ln_setup.settings.dev_dir
    ln_setup.settings.dev_dir = cwd
    if previous_dev_dir != cwd:
        logger.important(f"set dev-dir: {cwd}")
    logger.important(f"connected lamindb: {ln_setup.settings.instance.slug}")
    return None


def disconnect(*, here: bool = False) -> None:
    if not here:
        return ln_setup.disconnect()

    marker = find_local_current_instance_file()
    if marker is None:
        logger.info("no local instance marker found")
        return None

    instance_slug = marker.read_text().strip()
    try:
        owner, name = get_owner_name_from_identifier(instance_slug)
    except ValueError:
        owner = name = ""
    if owner and name:
        dev_dir_path = ln_setup.settings.settings_dir / f"dev-dir--{owner}--{name}.txt"
        dev_dir_path.unlink(missing_ok=True)
    removed_marker = remove_local_current_instance(marker=marker)
    if removed_marker is None:
        logger.info("no local instance marker found")
        return None
    logger.success(
        f"disconnected local instance context: {instance_slug} and unset dev-dir"
    )
    return None
