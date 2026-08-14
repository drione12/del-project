"""'Optimize RAM' / 'Clean Cache' Quick Actions. Both are safe, additive
operations - no ownership overrides, no filesystem permission changes, and
neither goes through the hardened force-delete pipeline (core/
force_delete.py, once built), because neither operation needs to touch a
locked or permission-denied file; they only trim/delete things this
process already has ordinary access to.
"""
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
from dataclasses import dataclass
from typing import Callable, List, Optional

import psutil

from core.critical_processes import is_critical
from core.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class OptimizeResult:
    trimmed_count: int
    skipped_count: int
    ram_before_percent: float
    ram_after_percent: float


def _trim_working_set_win32(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    process_query_limited_information = 0x1000
    process_set_quota = 0x0100
    handle = kernel32.OpenProcess(process_query_limited_information | process_set_quota, False, pid)
    if not handle:
        return False
    try:
        # -1, -1 asks Windows to trim the working set to its practical
        # minimum instead of setting a fixed floor/ceiling.
        return bool(kernel32.SetProcessWorkingSetSize(handle, -1, -1))
    finally:
        kernel32.CloseHandle(handle)


def optimize_ram(trim_fn: Optional[Callable[[int], bool]] = None) -> OptimizeResult:
    """Trims the working set of every reachable, non-critical process.
    Reports what actually happened (counts, before/after RAM%) rather than
    a "freed N GB!" claim - a working-set trim moves pages to disk/standby,
    it doesn't create memory Windows wasn't already able to reclaim on
    demand, so the honest framing is "asked Windows to reclaim what it
    could," not "recovered N GB the system was missing."
    """
    if trim_fn is None:
        trim_fn = _trim_working_set_win32 if sys.platform == "win32" else lambda pid: False

    ram_before = psutil.virtual_memory().percent
    self_pid = os.getpid()
    trimmed = 0
    skipped = 0

    for proc in psutil.process_iter(["pid", "name"]):
        pid = proc.info["pid"]
        name = proc.info.get("name")
        if is_critical(pid, name, self_pid):
            skipped += 1
            continue
        try:
            if trim_fn(pid):
                trimmed += 1
            else:
                skipped += 1
        except Exception:
            logger.debug("working-set trim failed for pid=%s name=%s", pid, name, exc_info=True)
            skipped += 1

    ram_after = psutil.virtual_memory().percent
    return OptimizeResult(trimmed, skipped, ram_before, ram_after)


_STANDBY_LIST_PRIVILEGE = "SeProfileSingleProcessPrivilege"
_MEMORY_PURGE_STANDBY_LIST = 4
_SYSTEM_MEMORY_LIST_INFORMATION = 0x50


def purge_standby_list() -> bool:
    """Opt-in 'advanced' cleanup - empties the whole standby list (cached,
    reclaimable pages Windows was holding on to speculatively) via the
    same undocumented-but-widely-used NtSetSystemInformation call tools
    like RAMMap/EmptyStandbyList.exe use. Genuinely slower right
    afterwards (nothing is cached anymore, so the next access to anything
    pays a fresh page-in cost) - the caller is expected to show that
    caveat before invoking this; it isn't re-stated here since this
    function's job is to do the thing honestly, not to gate the UI copy.
    """
    if sys.platform != "win32":
        return False
    from core.privileges import enable_privilege

    if not enable_privilege(_STANDBY_LIST_PRIVILEGE):
        return False
    try:
        ntdll = ctypes.windll.ntdll
        command = ctypes.c_int(_MEMORY_PURGE_STANDBY_LIST)
        status = ntdll.NtSetSystemInformation(
            _SYSTEM_MEMORY_LIST_INFORMATION, ctypes.byref(command), ctypes.sizeof(command)
        )
        return status == 0
    except Exception:
        logger.debug("standby list purge failed", exc_info=True)
        return False


@dataclass
class CleanResult:
    deleted_count: int
    skipped_count: int
    freed_bytes: int


def _delete_paths(paths: List[str]) -> CleanResult:
    """The actual deletion loop, factored out from clean_cache() so it's
    unit-testable against real temporary files instead of the live
    %TEMP%/thumbnail-cache locations - this part has no Windows-specific
    behavior at all, just os.remove with per-file error tolerance (a file
    another process currently has open is an expected, not exceptional,
    outcome here).
    """
    deleted = 0
    skipped = 0
    freed = 0
    for path in paths:
        try:
            size = os.path.getsize(path)
            os.remove(path)
            deleted += 1
            freed += size
        except OSError:
            skipped += 1
    return CleanResult(deleted, skipped, freed)


def _iter_files(directory: str) -> List[str]:
    found = []
    for root, _dirs, files in os.walk(directory):
        for name in files:
            found.append(os.path.join(root, name))
    return found


def clean_cache() -> CleanResult:
    """%TEMP% + the Explorer thumbnail cache only - deliberately not
    routed through force_delete.py's ownership-override machinery.
    Anything in these two locations that can't be removed with ordinary
    permissions (in use, no access) is just skipped, not fought with; a
    user who wants a specific locked file gone has the Cleanup page's
    drag-drop zone for that (once built).
    """
    targets: List[str] = []
    temp_dir = tempfile.gettempdir()
    if os.path.isdir(temp_dir):
        targets.extend(_iter_files(temp_dir))

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        thumb_dir = os.path.join(local_app_data, "Microsoft", "Windows", "Explorer")
        if os.path.isdir(thumb_dir):
            targets.extend(
                os.path.join(thumb_dir, name)
                for name in os.listdir(thumb_dir)
                if name.lower().startswith("thumbcache_")
            )

    return _delete_paths(targets)
