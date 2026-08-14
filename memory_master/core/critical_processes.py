"""Process identities that must never be targeted by the force-delete kill
list or the Protection page's blacklist watchdog, regardless of any path or
name match. Killing any of these can crash or force-reboot the machine.

Pure data + a pure predicate - no OS-specific imports - so this is
unit-testable on any OS, same reasoning as path_guard.py.
"""
from __future__ import annotations

from typing import Optional

CRITICAL_PROCESS_NAMES = frozenset(
    {
        "system",
        "system idle process",
        "csrss.exe",
        "wininit.exe",
        "winlogon.exe",
        "services.exe",
        "lsass.exe",
        "smss.exe",
    }
)

# PID 0 = System Idle Process, PID 4 = System, on every Windows version.
CRITICAL_PIDS = frozenset({0, 4})


def is_critical(pid: int, name: Optional[str], self_pid: int) -> bool:
    """True if this process must never be killed - the app's own process,
    a fixed PID (0/4), or a name in CRITICAL_PROCESS_NAMES. Checked
    unconditionally by both the force-delete kill list and the Protection
    page's blacklist watchdog, so a user can never configure their way
    around it (e.g. by blacklisting "lsass.exe").
    """
    if pid == self_pid:
        return True
    if pid in CRITICAL_PIDS:
        return True
    if name and name.strip().lower() in CRITICAL_PROCESS_NAMES:
        return True
    return False
