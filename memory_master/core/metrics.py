"""Cross-platform system metrics via psutil - polled from a QThread (see
ui/pages/dashboard_page.py's MetricsWorker) so the dashboard never blocks
its own UI thread waiting on a sample. Every field here is one of psutil's
OS-independent guarantees (total/available/percent/used/free on
virtual_memory, for example) rather than a Windows- or Linux-only extra
(buffers/cached/etc), because this module runs for real on this Linux dev
container as well as on the windows-latest CI runner and eventually real
Windows - keeping to the common fields means "verified here" actually
means something on Windows too.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

import psutil


def _default_disk_path() -> str:
    if sys.platform == "win32":
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


@dataclass
class Snapshot:
    timestamp: float
    cpu_percent: float
    ram_percent: float
    ram_used: int
    ram_total: int
    swap_percent: float
    disk_percent: float
    net_sent_rate: float  # bytes/sec since the previous sample
    net_recv_rate: float  # bytes/sec since the previous sample


class MetricsSampler:
    """Stateful sampler - network throughput needs a previous reading to
    diff against, and psutil's own cpu_percent() needs a primed baseline
    from a prior call to report anything meaningful, so this isn't a bare
    free function.
    """

    def __init__(self, disk_path: Optional[str] = None):
        self._disk_path = disk_path or _default_disk_path()
        self._last_net = psutil.net_io_counters()
        self._last_time = time.monotonic()
        psutil.cpu_percent(interval=None)

    def sample(self) -> Snapshot:
        now = time.monotonic()
        elapsed = max(now - self._last_time, 1e-6)

        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        try:
            disk_percent = psutil.disk_usage(self._disk_path).percent
        except OSError:
            disk_percent = 0.0

        net = psutil.net_io_counters()
        sent_rate = max(net.bytes_sent - self._last_net.bytes_sent, 0) / elapsed
        recv_rate = max(net.bytes_recv - self._last_net.bytes_recv, 0) / elapsed
        self._last_net = net
        self._last_time = now

        return Snapshot(
            timestamp=now,
            cpu_percent=psutil.cpu_percent(interval=None),
            ram_percent=vm.percent,
            ram_used=vm.used,
            ram_total=vm.total,
            swap_percent=swap.percent,
            disk_percent=disk_percent,
            net_sent_rate=sent_rate,
            net_recv_rate=recv_rate,
        )


@dataclass
class ProcessInfo:
    pid: int
    name: str
    exe: str
    memory_bytes: int
    cpu_percent: float


def top_processes(limit: int = 8) -> List[ProcessInfo]:
    """Sorted by working-set size, descending. Tolerates the two very
    ordinary races of scanning a live process list - something exits
    mid-scan (NoSuchProcess/ZombieProcess), or this process isn't allowed
    to read another's info (AccessDenied, e.g. another user's session or a
    protected system process) - by skipping just that one entry instead of
    failing the whole snapshot.
    """
    results: List[ProcessInfo] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "memory_info", "cpu_percent"]):
        try:
            info = proc.info
            mem = info.get("memory_info")
            results.append(
                ProcessInfo(
                    pid=info["pid"],
                    name=info.get("name") or "",
                    exe=info.get("exe") or "",
                    memory_bytes=mem.rss if mem else 0,
                    cpu_percent=info.get("cpu_percent") or 0.0,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    results.sort(key=lambda p: p.memory_bytes, reverse=True)
    return results[:limit]
