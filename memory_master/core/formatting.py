"""Tiny byte/rate formatting helpers shared by the dashboard page and the
process details dialog - pure functions, no OS or Qt dependency, so
they're trivially unit-testable.
"""
from __future__ import annotations


def format_bytes(n: float) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0:
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}PB"


def format_rate(bytes_per_sec: float) -> str:
    return f"{format_bytes(bytes_per_sec)}/s"
