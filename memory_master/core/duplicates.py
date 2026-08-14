"""Duplicate-file finder - walks a folder, MD5-hashes files above a size
floor (hashing every tiny file is wasted work - the false negatives that
matter are the large ones anyway), groups by hash. Meant to run on a
QThread (see cleanup_page.py's worker) since a big folder walk + hash pass
is exactly the kind of thing that would otherwise freeze the UI - the
reference script this was ported from ran it synchronously from a button
click, the same class of bug already fixed in the force-delete pipeline.
"""
from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

_DEFAULT_MIN_SIZE_BYTES = 1024 * 1024  # 1MB
_HASH_CHUNK_SIZE = 1024 * 1024


@dataclass
class DuplicateGroup:
    size_bytes: int
    paths: List[str]


def _hash_file(path: str) -> Optional[str]:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(_HASH_CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def find_duplicate_files(
    root: str,
    min_size_bytes: int = _DEFAULT_MIN_SIZE_BYTES,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> List[DuplicateGroup]:
    candidates: List[str] = []
    for dirpath, _dirs, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) >= min_size_bytes:
                    candidates.append(path)
            except OSError:
                continue

    by_hash: Dict[str, List[str]] = defaultdict(list)
    total = len(candidates)
    for i, path in enumerate(candidates):
        if should_cancel is not None and should_cancel():
            break
        digest = _hash_file(path)
        if digest is not None:
            by_hash[digest].append(path)
        if on_progress is not None:
            on_progress(i + 1, total)

    groups = []
    for paths in by_hash.values():
        if len(paths) < 2:
            continue
        try:
            size = os.path.getsize(paths[0])
        except OSError:
            size = 0
        groups.append(DuplicateGroup(size_bytes=size, paths=paths))

    groups.sort(key=lambda g: g.size_bytes * len(g.paths), reverse=True)
    return groups
