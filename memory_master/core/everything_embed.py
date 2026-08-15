"""Native window-embedding helpers for hosting EverythingClone.exe's own
window inside a Memory Master page as a real WS_CHILD window, instead of a
second separate top-level one (see the new --embed-parent-hwnd/
--embed-width/--embed-height flags src/main.cpp's wWinMain now accepts).

Windows-only, like core/privileges.py and core/icons.py - callers on other
platforms must not import/call the ctypes-calling functions here (see
ui/pages/search_page.py's sys.platform-guarded import, the same convention
core.icons already established). Unlike core/icons.py, every ctypes.windll
access here is deferred to inside each function body (guarded by its own
sys.platform check) rather than bound once at module import time - the
same pattern core/elevation.py and core/force_delete.py already use for
IsUserAnAdmin/ShellExecuteW - specifically so this module, and
build_launch_args (the one pure/no-ctypes function here), stay importable
and testable on any platform.
"""
from __future__ import annotations

import sys
from typing import List, Optional

# Must match kWindowClassName in src/main.cpp - there is no other link
# between the two languages/processes here, just this shared literal.
WINDOW_CLASS_NAME = "EverythingCloneWindow"

_WM_CLOSE = 0x0010


def build_launch_args(exe_path: str, parent_hwnd: int, width: int, height: int) -> List[str]:
    """The argv EverythingClone.exe needs to create its window as a child
    of parent_hwnd instead of a standalone top-level window. Pure list
    construction, no ctypes involved - the one function in this module
    that's testable off Windows.
    """
    return [
        exe_path,
        "--embed-parent-hwnd",
        str(parent_hwnd),
        "--embed-width",
        str(width),
        "--embed-height",
        str(height),
    ]


def find_child_hwnd(parent_hwnd: int) -> Optional[int]:
    """Looks up the embedded EverythingClone window as a direct child of
    parent_hwnd. None until its process has had time to create it (or if
    it never appears) - the caller is expected to poll this on a timer
    rather than treating one None as final.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowExW.restype = wintypes.HWND

    result = user32.FindWindowExW(parent_hwnd, None, WINDOW_CLASS_NAME, None)
    return result or None


def resize_child(child_hwnd: int, width: int, height: int) -> None:
    """Matches the embedded window to its container's current size - a
    WS_CHILD window doesn't resize itself just because its parent did;
    that's the host's job. EverythingClone's own WM_SIZE handler
    (unmodified) relayouts its internal controls in response to this.
    """
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.MoveWindow.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.BOOL,
    ]
    user32.MoveWindow.restype = wintypes.BOOL

    user32.MoveWindow(child_hwnd, 0, 0, width, height, True)


def request_graceful_close(child_hwnd: int) -> None:
    """Asks the embedded EverythingClone window to close itself the same
    way its (nonexistent, in embed mode) close button would - its WndProc
    has no explicit WM_CLOSE case, so this falls through to
    DefWindowProc's default DestroyWindow, which still runs WM_DESTROY's
    index-save-to-disk on the way out. Deliberately not
    TerminateProcess/Popen.terminate(): that would skip WM_DESTROY
    entirely and turn every restart into a full MFT rescan instead of a
    fast snapshot load.
    """
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL

    user32.PostMessageW(child_hwnd, _WM_CLOSE, 0, 0)
