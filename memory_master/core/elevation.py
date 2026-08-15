"""Admin-elevation helpers: checking whether this process is already
running elevated, and restarting the whole app elevated via a UAC prompt
when it isn't. Split out of core/force_delete.py - that module only ever
needed the elevation check as one gate inside its own pipeline; once the
Search page needed the same check plus a whole-*app* restart (not a single
operation), bolting that onto a module whose docstring scopes it to
force-delete specifically would have been a layering mismatch.
"""
from __future__ import annotations

import sys
from typing import List, Optional

from core.logging_setup import get_logger

logger = get_logger(__name__)


def is_running_as_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        logger.warning("IsUserAnAdmin check failed", exc_info=True)
        return False


def _build_relaunch_args(argv: List[str], extra_args: Optional[List[str]]) -> List[str]:
    """Pure list-building step split out of request_admin_restart so the
    argument-list logic is testable without ctypes/actually relaunching
    anything - mirrors the pure-logic/OS-call split core/startup_programs.py
    already uses for the same testability reason.
    """
    return list(argv) + list(extra_args or [])


def request_admin_restart(extra_args: Optional[List[str]] = None) -> bool:
    """Re-launches this app elevated via the UAC prompt (the "runas" shell
    verb), appending extra_args to this process's own argv (e.g. so the
    new, elevated instance can land back on a specific page instead of the
    default one). Does not exit the current, non-elevated instance itself
    - that's left to the caller (typically: launch elevated, then quit).
    """
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        relaunch_args = _build_relaunch_args(sys.argv, extra_args)
        args = " ".join(f'"{a}"' for a in relaunch_args)
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, None, 1)
        return result > 32  # ShellExecuteW: any return > 32 means success
    except Exception:
        logger.warning("restart-as-admin failed", exc_info=True)
        return False
