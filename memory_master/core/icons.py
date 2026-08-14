"""Per-executable icon extraction for the Top Processes list, mirroring
src/main.cpp's SHGetFileInfoW + real-path icon cache pattern from the C++
Everything clone in this repo (see GetIconIndex/HasPerFileIcon there) -
the same reasoning applies here: querying by real path (not just by
extension) is what makes an .exe show its own embedded icon instead of a
generic one. Windows-only (ctypes.windll doesn't exist elsewhere) and,
like core/privileges.py, can't be imported or exercised on this Linux dev
environment - only syntax-checked via py_compile. Callers on non-Windows
platforms must not import this module at all (see its sys.platform-guarded
import in ui/pages/dashboard_page.py).
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Dict, Optional

from PyQt5.QtGui import QPixmap

shell32 = ctypes.windll.shell32
user32 = ctypes.windll.user32

SHGFI_ICON = 0x000000100
SHGFI_SMALLICON = 0x000000001
_MAX_PATH = 260


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * _MAX_PATH),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


shell32.SHGetFileInfoW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.POINTER(SHFILEINFOW),
    wintypes.UINT,
    wintypes.UINT,
]
shell32.SHGetFileInfoW.restype = ctypes.c_void_p

user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.DestroyIcon.restype = wintypes.BOOL

_cache: Dict[str, Optional[QPixmap]] = {}


def _hicon_to_pixmap(hicon: int) -> Optional[QPixmap]:
    try:
        from PyQt5.QtWinExtras import QtWin

        pixmap = QtWin.fromHICON(hicon)
        return pixmap if not pixmap.isNull() else None
    except ImportError:
        # Documented gap (see the plan doc's "Honest expectations"): a
        # manual GDI GetIconInfo/GetDIBits fallback is possible but adds a
        # second full icon-extraction path for what should be a rare case
        # - QtWinExtras ships in the standard Windows PyQt5 wheel, so this
        # should not normally trigger.
        return None


def get_icon_for_path(path: str) -> Optional[QPixmap]:
    if path in _cache:
        return _cache[path]

    info = SHFILEINFOW()
    result = shell32.SHGetFileInfoW(
        path, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_ICON | SHGFI_SMALLICON
    )
    pixmap = None
    if result:
        try:
            pixmap = _hicon_to_pixmap(info.hIcon)
        finally:
            user32.DestroyIcon(info.hIcon)

    _cache[path] = pixmap
    return pixmap


def clear_cache() -> None:
    _cache.clear()
