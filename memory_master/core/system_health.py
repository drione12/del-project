"""Maps a raw metric percentage to a severity tier, used to color the
dashboard's status pills. Thresholds are deliberately conservative (90%+
is "critical", not 95%+) since a system already thrashing at 90% used is a
more useful early warning than waiting for it to be nearly pinned.
"""
from __future__ import annotations

from enum import Enum


class Severity(Enum):
    GOOD = "good"
    WARNING = "warning"
    CRITICAL = "critical"


_LABELS = {
    Severity.GOOD: "정상",
    Severity.WARNING: "주의",
    Severity.CRITICAL: "위험",
}

WARNING_THRESHOLD = 75.0
CRITICAL_THRESHOLD = 90.0


def severity_for_percent(percent: float) -> Severity:
    if percent >= CRITICAL_THRESHOLD:
        return Severity.CRITICAL
    if percent >= WARNING_THRESHOLD:
        return Severity.WARNING
    return Severity.GOOD


def label_for(severity: Severity) -> str:
    return _LABELS[severity]


def overall_severity(*percents: float) -> Severity:
    """Worst-of-N - the overall status pill shows the worst individual
    metric rather than an average, so a maxed-out swap doesn't get diluted
    by otherwise-idle CPU/disk.
    """
    worst = Severity.GOOD
    for p in percents:
        s = severity_for_percent(p)
        if s == Severity.CRITICAL:
            return Severity.CRITICAL
        if s == Severity.WARNING:
            worst = Severity.WARNING
    return worst
