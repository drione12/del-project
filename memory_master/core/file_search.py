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
                except OSError:
                    size_bytes = 0
                    modified_at = 0.0
                by_path[path] = FileEntry(name, path, size_bytes, modified_at, is_dir)

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
