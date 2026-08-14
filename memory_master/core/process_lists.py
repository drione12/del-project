"""Process whitelist/blacklist - whitelist entries are process names the
force-delete kill-list scan should never target (an addition on top of
core/critical_processes.py's fixed, always-protected set); blacklist
entries get auto-killed by BlacklistWatchdog the moment they're seen
running. Both lists persist to a small JSON file under
%LOCALAPPDATA%\\MemoryMaster so they survive an app restart - kept
self-contained here rather than waiting on a general config module, since
this is the first feature in the app that actually needs to remember
anything between runs.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Set

import psutil

from core.critical_processes import is_critical
from core.logging_setup import get_logger

logger = get_logger(__name__)

_WATCHDOG_POLL_SECONDS = 2.0


def _config_path() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return os.path.join(base, "MemoryMaster", "process_lists.json")


@dataclass
class ProcessLists:
    whitelist: Set[str] = field(default_factory=set)
    blacklist: Set[str] = field(default_factory=set)


def load_process_lists(path: Optional[str] = None) -> ProcessLists:
    path = path or _config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ProcessLists(
            whitelist={n.lower() for n in data.get("whitelist", [])},
            blacklist={n.lower() for n in data.get("blacklist", [])},
        )
    except (OSError, json.JSONDecodeError):
        return ProcessLists()


def save_process_lists(lists: ProcessLists, path: Optional[str] = None) -> bool:
    path = path or _config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"whitelist": sorted(lists.whitelist), "blacklist": sorted(lists.blacklist)}, f, indent=2)
        return True
    except OSError:
        logger.warning("failed to save process lists to %s", path, exc_info=True)
        return False


def is_whitelisted(name: Optional[str], lists: ProcessLists) -> bool:
    return bool(name) and name.lower() in lists.whitelist


def running_blacklisted_processes(lists: ProcessLists, self_pid: int) -> List[psutil.Process]:
    """Processes currently running whose name is on the blacklist - the
    critical-process set is excluded even if a user manages to add e.g.
    "lsass.exe" to the blacklist through the UI, the same defense-in-depth
    principle already applied to the force-delete kill list.
    """
    matches: List[psutil.Process] = []
    if not lists.blacklist:
        return matches
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pid = proc.info["pid"]
            name = proc.info.get("name") or ""
            if is_critical(pid, name, self_pid):
                continue
            if name.lower() in lists.blacklist:
                matches.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return matches


class BlacklistWatchdog:
    """Polls every poll_seconds (2s by default, matching the reference
    script's own cadence) and kills anything on the blacklist the moment
    it's seen. Runs on a plain background thread rather than a QThread so
    this module stays Qt-agnostic and independently testable without a
    QApplication; ui/pages/protection_page.py is what actually needs Qt,
    and it wraps this watchdog's on_kill callback in a thread-safe queue
    rather than touching widgets from this background thread directly.
    """

    def __init__(
        self,
        get_lists: Callable[[], ProcessLists],
        on_kill: Optional[Callable[[str, int], None]] = None,
        poll_seconds: float = _WATCHDOG_POLL_SECONDS,
    ):
        self._get_lists = get_lists
        self._on_kill = on_kill
        self._poll_seconds = poll_seconds
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=self._poll_seconds + 2)
            self._thread = None

    def _run(self) -> None:
        self_pid = os.getpid()
        while self._running:
            try:
                lists = self._get_lists()
                for proc in running_blacklisted_processes(lists, self_pid):
                    try:
                        name = proc.name()
                        pid = proc.pid
                        proc.kill()
                        logger.info("blacklist watchdog killed %s (pid=%s)", name, pid)
                        if self._on_kill is not None:
                            self._on_kill(name, pid)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except Exception:
                logger.warning("blacklist watchdog scan failed", exc_info=True)
            time.sleep(self._poll_seconds)
