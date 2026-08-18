"""File search index for the "파일 검색" (Everything-style search) page - a
plain, in-memory index built by walking a user-chosen list of folders, then
searched entirely in memory. Deliberately not a raw NTFS MFT index like the
real Everything app or this repo's own C++ EverythingClone (src/) - those
two share no code with this Python app (see memory_master/README.md) and
reimplementing that whole engine here in a second language isn't worth it.
Scoping to explicitly-chosen folders (rather than a whole drive) keeps an
os.walk-based index's build time and memory bounded and predictable.

Qt-free like core/duplicates.py and core/image_scanner.py, for the same
reason: pure logic is trivially unit-testable, and all QThread wrapping
happens one layer up in the UI.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from core.winpath import long_path


@dataclass
class FileEntry:
    name: str
    path: str
    size_bytes: int  # 0 for directories - see build_index
    modified_at: float  # st_mtime, epoch seconds
    is_dir: bool
    # Defaulted (not required at every call site, e.g. tests/test_file_search.py's
    # _entry() helper) - 0 is each field's own "unknown" sentinel, matching
    # core/formatting.py's format_datetime/format_attributes/format_extension
    # and the fast backend's own FILETIME-0/no-bits-set conventions
    # (core/fast_search.py). created_at/accessed_at are st_ctime/st_atime -
    # on Windows (this app's only real target) st_ctime is creation time,
    # not the "metadata change time" it means on Unix. attributes is the
    # raw Win32 FILE_ATTRIBUTE_* bitmask (os.stat_result.st_file_attributes,
    # Windows-only - see build_index for why this stays 0 elsewhere).
    created_at: float = 0.0
    accessed_at: float = 0.0
    attributes: int = 0


def _count_entries(root: str) -> int:
    total = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        total += len(dirnames) + len(filenames)
    return total


def build_index(
    roots: List[str],
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    compute_total: bool = True,
) -> List[FileEntry]:
    """Walks every root and returns one FileEntry per file/directory found,
    de-duplicated by path so two overlapping/nested chosen roots can't list
    the same entry twice (same reasoning as image_scanner.py's
    _list_images). Directories get size_bytes=0 rather than a recursive sum
    of their contents - that would turn indexing into a nested walk per
    folder, reintroducing the cost this feature exists to avoid.

    compute_total exists because the progress total is itself a full extra
    walk (_count_entries) before the real one - negligible for a small
    chosen folder, but doubles the cost of a whole-drive walk. Callers
    indexing at that scale pass compute_total=False and just get a live
    "done" count with total staying 0.
    """
    total = sum(_count_entries(root) for root in roots) if (on_progress and compute_total) else 0
    done = 0
    by_path: Dict[str, FileEntry] = {}

    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            entries = [(name, True) for name in dirnames] + [(name, False) for name in filenames]
            for name, is_dir in entries:
                if should_cancel is not None and should_cancel():
                    return list(by_path.values())

                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(long_path(path))
                    size_bytes = 0 if is_dir else st.st_size
                    modified_at = st.st_mtime
                    created_at = st.st_ctime
                    accessed_at = st.st_atime
                    # Windows-only stat field (os.stat_result docs) - 0
                    # (format_attributes' own "no flags" value) elsewhere,
                    # e.g. this app's own Linux dev/test environment.
                    attributes = getattr(st, "st_file_attributes", 0)
                except OSError:
                    size_bytes = 0
                    modified_at = 0.0
                    created_at = 0.0
                    accessed_at = 0.0
                    attributes = 0
                by_path[path] = FileEntry(
                    name, path, size_bytes, modified_at, is_dir, created_at, accessed_at, attributes
                )

                done += 1
                if on_progress is not None:
                    on_progress(done, total)

    return list(by_path.values())


def search(index: List[FileEntry], query: str, match_path: bool = True) -> List[FileEntry]:
    """Case-insensitive, whitespace-tokenized AND match (every term must
    appear somewhere in the name, or the path too when match_path is True)
    - mirrors the real Everything app's own default multi-term behavior
    rather than treating the whole query as one literal substring.
    """
    terms = query.lower().split()
    if not terms:
        return list(index)

    results = []
    for entry in index:
        haystack = entry.name.lower()
        if match_path:
            haystack += " " + entry.path.lower()
        if all(term in haystack for term in terms):
            results.append(entry)
    return results


# Category-filter extension sets, mirroring src/query.cpp's MatchesCategory
# in the separate C++ EverythingClone (same categories, same extensions) so
# the two apps' Search pages behave identically even though they share no
# code. Lowercase, no leading dot - matches os.path.splitext()[1][1:].lower().
# Deliberately a *new*, broader set for "image" here rather than reusing
# core/image_scanner.py's own _IMAGE_EXTENSIONS - that one is intentionally
# narrower, scoped to "can Qt's plain QPixmap decode this for the preview
# panel", a different question from "is this an image file".
_MUSIC_EXTENSIONS = frozenset({
    "mp3", "wav", "wma", "aac", "flac", "ogg", "oga", "m4a", "opus",
    "aiff", "aif", "ape", "alac", "mid", "midi", "amr", "au", "ra", "wv",
})
_ARCHIVE_EXTENSIONS = frozenset({
    "zip", "zipx", "rar", "7z", "tar", "gz", "tgz", "bz2", "tbz2",
    "xz", "txz", "iso", "cab", "arj", "lzh", "lha", "ace", "z", "wim",
})
_DOCUMENT_EXTENSIONS = frozenset({
    "doc", "docx", "pdf", "txt", "rtf", "odt", "hwp", "hwpx",
    "xls", "xlsx", "ods", "csv", "ppt", "pptx", "odp",
    "md", "xps", "epub", "mobi", "log", "wpd", "tex",
})
_EXECUTABLE_EXTENSIONS = frozenset({
    "exe", "msi", "bat", "cmd", "com", "scr", "ps1", "vbs",
    "jar", "msp", "gadget", "appx", "appxbundle", "msix", "msixbundle",
})
_IMAGE_CATEGORY_EXTENSIONS = frozenset({
    "jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "tif",
    "svg", "ico", "heic", "heif", "raw", "cr2", "nef", "arw",
    "dng", "orf", "rw2", "psd", "avif", "jfif", "jp2",
})
_VIDEO_EXTENSIONS = frozenset({
    "mp4", "avi", "mkv", "mov", "wmv", "flv", "webm", "m4v",
    "mpg", "mpeg", "mpe", "3gp", "3g2", "ts", "m2ts", "vob",
    "ogv", "rm", "rmvb", "asf", "divx",
})

CATEGORIES = ("all", "music", "archive", "document", "executable", "folder", "image", "video")


def matches_category(entry: FileEntry, category: str) -> bool:
    """category must be one of CATEGORIES. "all" and "folder" need no
    extension lookup (folder is attribute-based, like every other category
    here it never matches a directory except this one).
    """
    if category == "all":
        return True
    if category == "folder":
        return entry.is_dir
    if entry.is_dir:
        return False

    # Last-dot-in-the-name, not os.path.splitext() - splitext treats a
    # *leading* dot as part of the base name (its own deliberate handling
    # for Unix-style dotfiles like ".gitignore"), which would disagree with
    # the C++ side's plain name.find_last_of(L'.') for a name like that
    # (name[1:] = "gitignore" there). Windows itself has no such dotfile
    # convention - to it, ".gitignore" is simply a nameless file with a
    # ".gitignore" extension - so matching the C++/Windows-native behavior
    # here, not Python's Unix-flavored default, is the one that's actually
    # consistent with the platform both apps target.
    dot = entry.name.rfind(".")
    ext = entry.name[dot + 1 :].lower() if dot != -1 else ""
    if category == "music":
        return ext in _MUSIC_EXTENSIONS
    if category == "archive":
        return ext in _ARCHIVE_EXTENSIONS
    if category == "document":
        return ext in _DOCUMENT_EXTENSIONS
    if category == "executable":
        return ext in _EXECUTABLE_EXTENSIONS
    if category == "image":
        return ext in _IMAGE_CATEGORY_EXTENSIONS
    if category == "video":
        return ext in _VIDEO_EXTENSIONS
    return True
