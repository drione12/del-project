"""Finds processes holding a lock on a path (a file, or - for a directory
target - anything underneath it), so the force-delete pipeline can show
the user exactly what it's about to close before doing so.

Built on psutil.Process.open_files(), which works identically on every
platform psutil supports (including this Linux dev environment, letting
the matching/ancestor logic below be genuinely unit-tested here) - but is
a known-imperfect signal on Windows specifically: psutil's own docs note
its Windows open-handle enumeration can occasionally miss a lock that
isn't a plain open file handle (e.g. a memory-mapped file), and it
requires this process to have permission to inspect the other one.
Windows' Restart Manager API (RmStartSession/RmRegisterResources/RmGetList)
is the more complete alternative Explorer/Installer use internally, and is
a reasonable follow-up if this proves insufficient in practice - not used
here to avoid a second, entirely Windows-only, ctypes-struct-heavy path
that can't be exercised at all on this dev environment before it ever
runs for real.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import psutil

from core.path_guard import is_within_or_equal


@dataclass
class LockingProcess:
    pid: int
    name: str


def find_locking_processes(target_path: str, self_pid: int) -> List[LockingProcess]:
    """target_path may be a file or a directory - for a directory, a
    process holding open anything underneath it counts as locking it, so
    this reuses the same ancestor check path_guard.py uses for protected
    paths (already fixed against the "Downloads2 is not inside Downloads"
    prefix bug) rather than a second, separately-bug-prone string
    comparison.
    """
    results: List[LockingProcess] = []
    seen_pids = set()

    for proc in psutil.process_iter(["pid", "name"]):
        pid = proc.info["pid"]
        if pid == self_pid or pid in seen_pids:
            continue
        try:
            for f in proc.open_files():
                if is_within_or_equal(f.path, target_path):
                    results.append(LockingProcess(pid=pid, name=proc.info.get("name") or ""))
                    seen_pids.add(pid)
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return results
