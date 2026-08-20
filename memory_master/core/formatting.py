"""Tiny byte/rate/datetime formatting helpers used by the search page's
results table (size/date/extension/attribute columns) - pure functions, no
OS or Qt dependency, so they're trivially unit-testable.
"""
from __future__ import annotations

from datetime import datetime


def format_bytes(n: float) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0:
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}PB"


def format_rate(bytes_per_sec: float) -> str:
    return f"{format_bytes(bytes_per_sec)}/s"


def format_datetime(timestamp: float) -> str:
    """"YYYY-MM-DD HH:MM" (24-hour) - matches the separate C++
    EverythingClone's own FormatFileTime (src/main.cpp) exactly, so a
    result shown on this page's table looks identical to the same result
    in EverythingClone's native window. 0 (the "unknown" sentinel
    FileEntry.modified_at/created_at/accessed_at use - a failed os.stat()
    on the slow backend, or FILETIME 0 from the fast one) renders as ""
    rather than the 1970-01-01 epoch a naive datetime.fromtimestamp(0)
    would give, again matching FormatFileTime's own early return.
    """
    if timestamp == 0:
        return ""
    dt = datetime.fromtimestamp(timestamp)
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d} {dt.hour:02d}:{dt.minute:02d}"


# Win32 FILE_ATTRIBUTE_* bit values (winnt.h) - letter mapping and order
# both match EverythingClone's own FormatAttributes (src/main.cpp) exactly.
_ATTRIBUTE_FLAGS = (
    (0x1, "R"),  # READONLY
    (0x2, "H"),  # HIDDEN
    (0x4, "S"),  # SYSTEM
    (0x10, "D"),  # DIRECTORY
    (0x20, "A"),  # ARCHIVE
    (0x800, "C"),  # COMPRESSED
    (0x4000, "E"),  # ENCRYPTED
    (0x100, "T"),  # TEMPORARY
    (0x1000, "O"),  # OFFLINE
    (0x400, "L"),  # REPARSE_POINT ("L"ink, matching FormatAttributes's own letter choice)
)


def format_attributes(attributes: int) -> str:
    """Compact attrib-style letter string (e.g. "RHA") - see
    _ATTRIBUTE_FLAGS above. attributes is a raw Win32 FILE_ATTRIBUTE_*
    bitmask (FileEntry.attributes); 0 (no bits set, or this field
    unavailable - e.g. the slow backend running somewhere other than
    Windows) renders as "".
    """
    return "".join(letter for bit, letter in _ATTRIBUTE_FLAGS if attributes & bit)


def format_extension(name: str, is_dir: bool) -> str:
    """Extension without the leading dot, for display - matches
    EverythingClone's own GetExtensionDisplay (src/main.cpp) exactly:
    empty for directories, empty for a name with no dot at all, and empty
    for a name whose only dot is the very first character (a leading-dot
    "dotfile" like ".gitignore" - Explorer shows no extension for these,
    even though matches_category's own extension parsing deliberately
    disagrees for filter-matching purposes; see that function's own
    docstring for why display and matching intentionally differ here).
    Original case is preserved, not lowercased - this is for display, not
    for a case-insensitive comparison.
    """
    if is_dir:
        return ""
    dot = name.rfind(".")
    if dot == -1 or dot == 0:
        return ""
    return name[dot + 1 :]
