"""'Soft delete' - moves a file to a quarantine folder instead of
force-deleting it outright, a reversible alternative sitting alongside the
irreversible one. Quarantine location is
%LOCALAPPDATA%\\MemoryMaster\\quarantine by default (overridable once the
Settings page exists) - kept next to the app's own log directory rather
than a hidden system temp folder, so a user who wants to go look at what's
in quarantine can actually find it.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from dataclasses import dataclass
from typing import Optional


def default_quarantine_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return os.path.join(base, "MemoryMaster", "quarantine")


@dataclass
class QuarantineResult:
    ok: bool
    quarantined_path: str
    error: str = ""


def move_to_quarantine(path: str, quarantine_dir: Optional[str] = None) -> QuarantineResult:
    quarantine_dir = quarantine_dir or default_quarantine_dir()
    try:
        os.makedirs(quarantine_dir, exist_ok=True)
    except OSError as e:
        return QuarantineResult(False, "", f"격리 폴더를 만들 수 없습니다: {e}")

    name = os.path.basename(path.rstrip("\\/"))
    dest = os.path.join(quarantine_dir, name)
    if os.path.exists(dest):
        # Avoid clobbering an unrelated file that happens to share a name
        # - suffix with a timestamp rather than silently overwriting.
        stem, ext = os.path.splitext(name)
        dest = os.path.join(quarantine_dir, f"{stem}_{int(time.time())}{ext}")

    try:
        shutil.move(path, dest)
        return QuarantineResult(True, dest)
    except OSError as e:
        return QuarantineResult(False, "", str(e))
