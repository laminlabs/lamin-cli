"""Lamin CLI.

This is the command line interface for interacting with LaminDB & LaminHub.

The interface is defined in `__main__.py`. The root API here is used by LaminR to replicate the CLI functionality.
"""

__version__ = "1.7.0"

from ._save import save

__all__ = ["save"]
