"""Windows Task Manager's memory composition bar (In Use / Standby /
Compressed / Free) can't be fully reconstructed from psutil's
cross-platform fields alone - psutil.virtual_memory() only guarantees
total/available/used/free/percent everywhere, with the Windows-specific
breakdown (standby cache, compressed working set) needing a best-effort
extra on top. That extra degrades to an honest omitted segment rather than
a guessed number when it can't be determined (non-Windows, or the Memory
Compression process isn't present/accessible).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List, Optional

import psutil


@dataclass
class MemorySegment:
    label: str
    bytes_: int
    percent: float


@dataclass
class MemoryAllocation:
    total: int
    segments: List[MemorySegment]


def _compressed_bytes() -> Optional[int]:
    """Windows exposes no simple public counter for "bytes of compressed
    memory" - Task Manager derives its own figure from the "Memory
    Compression" system process's working set, which is a real,
    reasonably accurate proxy (that process's entire job is holding the
    compressed pages) and far simpler than reverse-engineering the
    undocumented NT APIs Task Manager actually uses internally. Returns
    None (not 0) when the process can't be found or read, so callers can
    tell "genuinely zero" apart from "couldn't determine" and show "N/A"
    instead of a fabricated number.
    """
    if sys.platform != "win32":
        return None
    total = 0
    found = False
    for proc in psutil.process_iter(["name", "memory_info"]):
        try:
            name = proc.info.get("name") or ""
            if name.lower() == "memory compression":
                mem = proc.info.get("memory_info")
                total += mem.rss if mem else 0
                found = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total if found else None


def compute_segments(
    total: int, used: int, available: int, free: int, compressed: Optional[int]
) -> MemoryAllocation:
    """Pure math over already-sampled numbers - kept separate from
    get_memory_allocation() so the part with actual logic worth getting
    right is testable without psutil or a real OS at all.
    """
    total = max(total, 1)  # guards divide-by-zero on a degenerate reading
    compressed = min(compressed or 0, used)
    standby = max(available - free, 0)
    # "In use" excludes whatever's attributed to standby/compressed so the
    # segments sum to the total instead of double-counting memory that's
    # simultaneously "used" and "reclaimable standby cache".
    in_use = max(used - standby - compressed, 0)
    free_seg = max(total - in_use - standby - compressed, 0)

    def pct(n: int) -> float:
        return round(n / total * 100.0, 1)

    segments = [
        MemorySegment("사용 중", in_use, pct(in_use)),
        MemorySegment("대기(재사용 가능)", standby, pct(standby)),
    ]
    if compressed > 0:
        segments.append(MemorySegment("압축됨", compressed, pct(compressed)))
    segments.append(MemorySegment("여유", free_seg, pct(free_seg)))
    return MemoryAllocation(total=total, segments=segments)


def get_memory_allocation() -> MemoryAllocation:
    vm = psutil.virtual_memory()
    compressed = _compressed_bytes()
    return compute_segments(vm.total, vm.used, vm.available, vm.free, compressed)
