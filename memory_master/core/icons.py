"""Per-file icon extraction, mirroring src/main.cpp's SHGetFileInfoW +
real-path icon cache pattern from the C++ Everything clone in this repo
(see GetIconIndex/HasPerFileIcon there) - the same reasoning applies here:
querying by real path (not just by extension) is what makes an .exe show
its own embedded icon instead of a generic one. Windows-only
(ctypes.windll doesn't exist elsewhere) and, like core/privileges.py,
can't be imported or exercised on this Linux dev environment - only
syntax-checked via py_compile. Callers on non-Windows platforms must not
import this module at all (see its sys.platform-guarded import in
ui/pages/dashboard_page.py and friends).
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Dict, Optional

from PyQt5.QtGui import QPixmap

shell32 = ctypes.windll.shell32
user32 = ctypes.windll.user32

SHGFI_ICON = 0x000000100
SHGFI_SMALLICON = 0x000000001
SHGFI_USEFILEATTRIBUTES = 0x000000010
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_DIRECTORY = 0x10
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


def _lookup_icon(path: str, attrs: int = 0) -> Optional[QPixmap]:
    flags = SHGFI_ICON | SHGFI_SMALLICON
    if attrs:
        flags |= SHGFI_USEFILEATTRIBUTES
    info = SHFILEINFOW()
    result = shell32.SHGetFileInfoW(path, attrs, ctypes.byref(info), ctypes.sizeof(info), flags)
    if not result:
        return None
    try:
        return _hicon_to_pixmap(info.hIcon)
    finally:
        user32.DestroyIcon(info.hIcon)


def get_icon_for_path(path: str, is_dir: bool = False) -> Optional[QPixmap]:
    if path in _cache:
        return _cache[path]

    pixmap = _lookup_icon(path)
    if pixmap is None:
        # The real-path lookup can fail for reasons that have nothing to do
        # with the file's actual type - a path over the Shell API's own
        # ~260-char limit (unlike raw kernel32 file I/O, \\?\ prefixing
        # does not reliably lift this for SHGetFileInfoW - see
        # core/winpath.py's own, different fix for that), a locked file, a
        # transient share glitch. SHGFI_USEFILEATTRIBUTES never touches the
        # real file at all - it just needs a plausible name/extension and
        # an attributes flag - so falling back to it means the user
        # reliably gets at least a correct-for-the-type icon instead of a
        # blank cell.
        if is_dir:
            pixmap = _lookup_icon(path, attrs=FILE_ATTRIBUTE_DIRECTORY)
        else:
            ext = os.path.splitext(path)[1] or path
            pixmap = _lookup_icon(ext, attrs=FILE_ATTRIBUTE_NORMAL)

    _cache[path] = pixmap
    return pixmap


def clear_cache() -> None:
    _cache.clear()
