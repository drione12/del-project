"""Windows long-path (\\\\?\\-prefix) support for the raw file I/O call sites
in core/force_delete.py and core/file_search.py. Kernel32-backed calls
(os.remove/os.rmdir/os.stat/os.path.getsize, MoveFileExW - what those two
modules use) pass their argument straight through to the underlying Win32
*W API, which DOES honor the \\\\?\\ prefix to bypass the classic
260-character MAX_PATH limit - unlike Shell-layer APIs such as
SHGetFileInfoW, which do not reliably support it (see core/icons.py's own,
different fallback for that problem).

Deliberately dependency-injects the platform rather than reading
sys.platform internally, so the actual string-prefixing logic is
unit-testable on any host OS (same motivation as core/path_guard.py using
ntpath unconditionally) while still being safe to call unconditionally
from real call sites - passing a \\\\?\\-prefixed string to a real Linux
syscall would be nonsense, so the default (no override) is a no-op
anywhere but win32.
"""
from __future__ import annotations

import ntpath
import sys

_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\?\\UNC\\"


def long_path(path: str, *, platform: str = sys.platform) -> str:
    """\\\\?\\-prefixes an absolute Windows path so raw Win32 file I/O can
    exceed MAX_PATH. `platform` exists purely so tests can exercise the
    Windows-prefixing branch deterministically from any host OS - real
    call sites should never pass it.
    """
    if platform != "win32" or not path or path.startswith(_PREFIX):
        return path
    abs_path = ntpath.abspath(path)
    if abs_path.startswith("\\\\"):
        return _UNC_PREFIX + abs_path.lstrip("\\")
    return _PREFIX + abs_path
