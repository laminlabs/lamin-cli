from __future__ import annotations

from pathlib import Path

import lamindb_setup as ln_setup
from lamin_utils import logger
from lamindb_setup._connect_instance import _connect_cli
from lamindb_setup.core._settings_store import (
    find_local_current_instance_file,
    remove_local_current_instance,
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
        persist_global_env=False,
        show_dev_dir_hint=False,
    )
    cwd = Path.cwd().resolve()
    ln_setup.settings.dev_dir = cwd
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
        _connect_cli(instance_slug, persist_global_env=False, show_dev_dir_hint=False)
        ln_setup.settings.dev_dir = None
        logger.success(
            f"disconnected local instance context: {instance_slug} and unset dev-dir"
        )
    except Exception:
        removed_marker = remove_local_current_instance(marker=marker)
        if removed_marker is not None:
            logger.warning(
                "removed local instance marker, but could not resolve instance settings to unset dev-dir"
            )
        else:
            logger.info("no local instance marker found")
    return None
