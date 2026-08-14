"""Reversible delete via the OS Recycle Bin - the Del key / right-click
"삭제" action on the search page, deliberately NOT the hardened force-delete
pipeline (core/force_delete.py), which is reserved for files that resist
this simpler path (right-click "강제 삭제" specifically). Still runs the
same protected-path check force_delete.py gates on first: a Recycle Bin
move of e.g. C:\\Windows\\System32 is still highly disruptive even though
technically reversible.
"""
from __future__ import annotations

from dataclasses import dataclass

import send2trash

from core.path_guard import is_protected


@dataclass
class TrashResult:
    ok: bool
    error: str = ""


def send_to_trash(path: str) -> TrashResult:
    blocked, reason = is_protected(path)
    if blocked:
        return TrashResult(False, reason)

    try:
        send2trash.send2trash(path)
        return TrashResult(True)
    except Exception as e:
        # send2trash's exception types vary by platform/backend (unlike
        # e.g. shutil.move's plain OSError) - a broad catch is the only
        # reliable way to turn "trash failed" into a TrashResult instead of
        # an unhandled exception reaching the UI thread.
        return TrashResult(False, str(e))
