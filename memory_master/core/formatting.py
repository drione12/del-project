"""Tiny byte/rate/datetime formatting helpers shared by the dashboard page,
the process details dialog, and the search page - pure functions, no OS or
Qt dependency, so they're trivially unit-testable.
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


def format_datetime_kr(timestamp: float) -> str:
    """"YYYY-MM-DD 오전/오후 H:MM", matching Windows Explorer/Everything's
    own Korean-locale date display. Computed manually rather than via
    strftime("%p")/%-I: %p is locale-dependent (would print English AM/PM
    on a non-Korean-locale runner, including this app's own CI), and %-I
    (no-leading-zero hour) is a glibc strftime extension that raises on
    Windows - this app's actual target platform.
    """
    dt = datetime.fromtimestamp(timestamp)
    hour12 = dt.hour % 12 or 12
    period = "오전" if dt.hour < 12 else "오후"
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d} {period} {hour12}:{dt.minute:02d}"
