"""ctypes wrapper around EverythingCore.dll (src/core_api.h) - the same
NTFS MFT indexing/USN-watching/search engine EverythingClone.exe uses,
loaded directly into this process instead of spawning and window-embedding
that separate .exe (the old approach - see memory_master/README.md for why
that changed, and src/core_api.h for the full C API this binds).

Windows-only, like core/icons.py's ctypes-calling functions - there is no
off-Windows stub class here (unlike that module's free functions, which do
get off-Windows no-op stubs in their callers) because FastSearchEngine's
constructor itself would fail off Windows (no ctypes.WinDLL, no
EverythingCore.dll) - callers must guard construction with is_available()
plus their own sys.platform check, and use core/file_search.py's slow
os.walk search instead when either is false.
"""
from __future__ import annotations

import os
import sys
from typing import List

from core.file_search import CATEGORIES, FileEntry
from core.resource_path import resource_path

_CATEGORY_TO_INDEX = {category: i for i, category in enumerate(CATEGORIES)}

# FILETIME (100ns ticks since 1601-01-01 UTC, what EC_GetResult*Time
# returns) -> POSIX epoch seconds (what FileEntry.modified_at/
# os.stat().st_mtime use elsewhere in this codebase). 11644473600 is the
# fixed number of seconds between the two epochs.
_FILETIME_EPOCH_DIFF_SECONDS = 11644473600
_FILETIME_TICKS_PER_SECOND = 10_000_000
_FILE_ATTRIBUTE_DIRECTORY = 0x10

# Generous fixed buffer for EC_GetResultPath - see core_api.h's truncation
# contract (returns the true length; a real path this long is not a
# realistic case this app needs to handle gracefully, just not crash on).
_PATH_BUFFER_CHARS = 4096


def _filetime_to_epoch(ticks: int) -> float:
    if ticks == 0:  # this engine's "unknown" sentinel, matching FileEntry elsewhere
        return 0.0
    return ticks / _FILETIME_TICKS_PER_SECOND - _FILETIME_EPOCH_DIFF_SECONDS


def is_available() -> bool:
    """Whether EverythingCore.dll exists alongside this build. False in any
    dev/test environment (this DLL is only ever produced by the Windows CI
    build) and in an old install predating it. Callers should treat this
    the same as "not elevated" - fall back to core/file_search.py.
    """
    if sys.platform != "win32":
        return False
    return os.path.exists(resource_path("EverythingCore.dll"))


class FastSearchEngine:
    """One EC_Handle's worth of state. Every method except close() is
    blocking (build_index especially, on a first-ever run with no saved
    snapshot to catch up from) - callers run this on a background thread,
    same as core/file_search.py's build_index/search already are (see
    ui/pages/search_page.py's _IndexWorker/_FastIndexWorker).
    """

    def __init__(self) -> None:
        import ctypes

        self._dll = ctypes.WinDLL(resource_path("EverythingCore.dll"))
        self._bind_functions()
        self._handle = self._dll.EC_Create()

    def _bind_functions(self) -> None:
        import ctypes

        dll = self._dll
        dll.EC_Create.argtypes = []
        dll.EC_Create.restype = ctypes.c_void_p

        dll.EC_Destroy.argtypes = [ctypes.c_void_p]
        dll.EC_Destroy.restype = None

        dll.EC_BuildIndex.argtypes = [ctypes.c_void_p]
        dll.EC_BuildIndex.restype = ctypes.c_uint64

        dll.EC_SaveIndexes.argtypes = [ctypes.c_void_p]
        dll.EC_SaveIndexes.restype = None

        dll.EC_TotalCount.argtypes = [ctypes.c_void_p]
        dll.EC_TotalCount.restype = ctypes.c_uint64

        dll.EC_Search.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_int]
        dll.EC_Search.restype = ctypes.c_int

        dll.EC_GetResultPath.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int]
        dll.EC_GetResultPath.restype = ctypes.c_int

        dll.EC_GetResultSize.argtypes = [ctypes.c_void_p, ctypes.c_int]
        dll.EC_GetResultSize.restype = ctypes.c_uint64

        dll.EC_GetResultModifiedTime.argtypes = [ctypes.c_void_p, ctypes.c_int]
        dll.EC_GetResultModifiedTime.restype = ctypes.c_uint64

        dll.EC_GetResultCreatedTime.argtypes = [ctypes.c_void_p, ctypes.c_int]
        dll.EC_GetResultCreatedTime.restype = ctypes.c_uint64

        dll.EC_GetResultAccessedTime.argtypes = [ctypes.c_void_p, ctypes.c_int]
        dll.EC_GetResultAccessedTime.restype = ctypes.c_uint64

        dll.EC_GetResultAttributes.argtypes = [ctypes.c_void_p, ctypes.c_int]
        dll.EC_GetResultAttributes.restype = ctypes.c_uint32

    def build_index(self) -> int:
        """Returns the total indexed record count across every fixed NTFS
        volume - 0 usually means this process isn't elevated (raw volume
        access needs administrator rights), not that the drives are empty.
        """
        return int(self._dll.EC_BuildIndex(self._handle))

    def save_indexes(self) -> None:
        """Persists every volume's index to disk so the next build_index()
        (a future run of this app) can use the fast snapshot+catch-up path
        instead of a full MFT rescan. Call before close() on a clean exit.
        """
        self._dll.EC_SaveIndexes(self._handle)

    def total_count(self) -> int:
        return int(self._dll.EC_TotalCount(self._handle))

    def search(self, query: str, category: str, max_results: int) -> List[FileEntry]:
        """category must be one of core.file_search.CATEGORIES. Note this
        engine's own default match behavior (unlike core/file_search.py's
        search(), which always also matches the path) only matches file/
        folder *names* unless the query text itself contains a "path:"
        keyword - that's EverythingClone's own real, already-shipped
        behavior, deliberately not papered over here to force path-matching
        on, since the whole point of using this engine is to get its actual
        behavior rather than an approximation of it.
        """
        import ctypes

        category_index = _CATEGORY_TO_INDEX.get(category, 0)
        count = self._dll.EC_Search(self._handle, query, category_index, max_results)

        buf = ctypes.create_unicode_buffer(_PATH_BUFFER_CHARS)
        entries: List[FileEntry] = []
        for i in range(count):
            self._dll.EC_GetResultPath(self._handle, i, buf, _PATH_BUFFER_CHARS)
            path = buf.value
            attributes = self._dll.EC_GetResultAttributes(self._handle, i)
            is_dir = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
            size_bytes = 0 if is_dir else int(self._dll.EC_GetResultSize(self._handle, i))
            modified_at = _filetime_to_epoch(self._dll.EC_GetResultModifiedTime(self._handle, i))
            created_at = _filetime_to_epoch(self._dll.EC_GetResultCreatedTime(self._handle, i))
            accessed_at = _filetime_to_epoch(self._dll.EC_GetResultAccessedTime(self._handle, i))
            entries.append(
                FileEntry(
                    name=os.path.basename(path),
                    path=path,
                    size_bytes=size_bytes,
                    modified_at=modified_at,
                    is_dir=is_dir,
                    created_at=created_at,
                    accessed_at=accessed_at,
                    attributes=attributes,
                )
            )
        return entries

    def close(self) -> None:
        if self._handle is not None:
            self._dll.EC_Destroy(self._handle)
            self._handle = None
