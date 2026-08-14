import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import system_health as sh  # noqa: E402


def test_low_percent_is_good():
    assert sh.severity_for_percent(10.0) == sh.Severity.GOOD


def test_at_warning_threshold_is_warning():
    assert sh.severity_for_percent(sh.WARNING_THRESHOLD) == sh.Severity.WARNING


def test_just_below_warning_threshold_is_good():
    assert sh.severity_for_percent(sh.WARNING_THRESHOLD - 0.1) == sh.Severity.GOOD


def test_at_critical_threshold_is_critical():
    assert sh.severity_for_percent(sh.CRITICAL_THRESHOLD) == sh.Severity.CRITICAL


def test_overall_severity_is_worst_of_all():
    assert sh.overall_severity(10.0, 50.0, sh.CRITICAL_THRESHOLD) == sh.Severity.CRITICAL
    assert sh.overall_severity(10.0, sh.WARNING_THRESHOLD, 20.0) == sh.Severity.WARNING
    assert sh.overall_severity(10.0, 20.0, 30.0) == sh.Severity.GOOD


def test_label_for_each_severity_is_non_empty():
    for severity in sh.Severity:
        assert sh.label_for(severity)
