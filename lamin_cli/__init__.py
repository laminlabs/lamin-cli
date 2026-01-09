"""Lamin CLI.

This is the command line interface for interacting with LaminDB & LaminHub.

The interface is defined in `__main__.py`.
The root API here is used by LaminR to replicate the CLI functionality.
"""

__version__ = "1.11.0"

from lamindb_setup import disconnect, logout
from lamindb_setup._connect_instance import _connect_cli as connect
from lamindb_setup._init_instance import init
from lamindb_setup._setup_user import login

from ._delete import delete
from ._save import save

__all__ = [
    "save",
    "init",
    "connect",
    "delete",
    "login",
    "logout",
    "disconnect",
]
