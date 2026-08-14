"""Shared rotating-file logger, used in place of bare print()/silent
exception-swallowing across the app. The dashboard's process iteration,
the optimizer, and - once built - the force-delete pipeline and blacklist
watchdog all hit the same class of expected-but-noteworthy failure: a
process that exited mid-scan, a file Windows won't let go of. Logging it
beats losing it silently and beats crashing the UI over it.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_APP_NAME = "MemoryMaster"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 3

_configured = False


def _log_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        # Never actually shipped for non-Windows use, but keeps this
        # module (and anything that imports it, like core/optimizer.py)
        # importable and testable on this Linux dev environment.
        base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return os.path.join(base, _APP_NAME, "logs")


def _configure_root() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger(_APP_NAME)
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        log_dir = _log_dir()
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "memory_master.log"),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # No writable log directory - console-only logging still works, so
        # this shouldn't block the app from running.
        pass


def get_logger(name: str) -> logging.Logger:
    _configure_root()
    return logging.getLogger(f"{_APP_NAME}.{name}")
