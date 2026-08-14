"""Startup program manager - lists what's configured to launch at sign-in
(the HKCU Run registry key) and what's scheduled via Task Scheduler
(schtasks), with a rough boot-impact estimate for each. Windows-only for
the actual registry/schtasks access (winreg doesn't exist off Windows,
and schtasks is a Windows binary) - but the CSV-parsing and boot-impact-
classification logic is split into pure functions specifically so it
stays testable on any platform, since that's the part most likely to
actually have bugs (registry/schtasks calls themselves are thin wrappers
over well-documented, stable Windows APIs). schtasks's CSV column names
have some real-world variance across Windows versions/locales - parsing
by header name (csv.DictReader) rather than a fixed column index is
already more robust than the reference script this was ported from, but
still worth a defensive re-check once this runs against a real schtasks
output, per the plan doc.
"""
from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import psutil

from core.logging_setup import get_logger

logger = get_logger(__name__)

_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


class BootImpact(Enum):
    LOW = "낮음"
    MEDIUM = "보통"
    HIGH = "높음"


_LOW_THRESHOLD_BYTES = 20 * 1024 * 1024  # 20MB
_HIGH_THRESHOLD_BYTES = 150 * 1024 * 1024  # 150MB


def classify_boot_impact(size_bytes: int) -> BootImpact:
    """A rough proxy, not a real timing measurement: a running process's
    working set (or, if it isn't currently running, its target file's
    size on disk) stands in for how much has to be loaded at boot. Not
    precise, but directionally useful for spotting the one bloated
    startup entry among a dozen small ones.
    """
    if size_bytes >= _HIGH_THRESHOLD_BYTES:
        return BootImpact.HIGH
    if size_bytes >= _LOW_THRESHOLD_BYTES:
        return BootImpact.MEDIUM
    return BootImpact.LOW


@dataclass
class StartupEntry:
    name: str
    command: str
    source: str  # "registry" or "task_scheduler"
    enabled: bool
    boot_impact: BootImpact


def extract_exe_path(command: str) -> Optional[str]:
    """Registry Run values are often a full command line ("C:\\...\\app.exe"
    --flag), not a bare path - this pulls out just the executable part,
    handling both a quoted path and a bare unquoted one.
    """
    command = command.strip()
    if not command:
        return None
    if command.startswith('"'):
        end = command.find('"', 1)
        return command[1:end] if end != -1 else command[1:]
    return command.split(" ")[0]


def _resolve_impact_size(command: str) -> int:
    """Prefers a currently-running process's RSS (the real, live memory
    footprint) over the target file's on-disk size, when the command's
    target executable happens to already be running - a closer proxy for
    actual boot cost than file size alone, which a self-extracting/
    compressed installer stub can make misleadingly small.
    """
    exe_path = extract_exe_path(command)
    if not exe_path:
        return 0

    exe_name = os.path.basename(exe_path).lower()
    for proc in psutil.process_iter(["name", "exe", "memory_info"]):
        try:
            info = proc.info
            proc_exe_name = os.path.basename(info.get("exe") or "").lower()
            proc_name = (info.get("name") or "").lower()
            if exe_name and (proc_exe_name == exe_name or proc_name == exe_name):
                mem = info.get("memory_info")
                if mem:
                    return mem.rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    try:
        return os.path.getsize(exe_path)
    except OSError:
        return 0


def parse_scheduled_tasks_csv(csv_text: str) -> List[StartupEntry]:
    entries = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        task_name = (row.get("TaskName") or "").strip()
        if not task_name:
            continue
        command = (row.get("Task To Run") or "").strip()
        if not command or command == "N/A":
            continue
        status = (row.get("Status") or "").strip()
        size = _resolve_impact_size(command)
        entries.append(
            StartupEntry(
                name=task_name.lstrip("\\"),
                command=command,
                source="task_scheduler",
                enabled=status.lower() == "ready",
                boot_impact=classify_boot_impact(size),
            )
        )
    return entries


def _read_run_key_entries() -> List[StartupEntry]:
    if sys.platform != "win32":
        return []
    import winreg

    entries = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH) as key:
            i = 0
            while True:
                try:
                    name, value, _type = winreg.EnumValue(key, i)
                except OSError:
                    break
                size = _resolve_impact_size(value)
                entries.append(
                    StartupEntry(
                        name=name,
                        command=value,
                        source="registry",
                        enabled=True,
                        boot_impact=classify_boot_impact(size),
                    )
                )
                i += 1
    except OSError:
        logger.warning("failed to read startup Run key", exc_info=True)
    return entries


def _query_scheduled_tasks() -> str:
    result = subprocess.run(
        ["schtasks", "/query", "/fo", "csv", "/v"], capture_output=True, text=True, timeout=30, check=False
    )
    return result.stdout


def list_startup_entries() -> List[StartupEntry]:
    entries = _read_run_key_entries()
    if sys.platform == "win32":
        try:
            entries.extend(parse_scheduled_tasks_csv(_query_scheduled_tasks()))
        except (subprocess.SubprocessError, OSError):
            logger.warning("failed to query scheduled tasks", exc_info=True)
    return entries


def add_startup_program(name: str, command: str) -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
        return True
    except OSError:
        logger.warning("failed to add startup program %s", name, exc_info=True)
        return False


def remove_startup_program(name: str) -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
        return True
    except OSError:
        logger.warning("failed to remove startup program %s", name, exc_info=True)
        return False
