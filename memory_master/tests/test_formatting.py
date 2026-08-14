import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.formatting import format_bytes, format_datetime_kr, format_rate  # noqa: E402


def test_format_bytes_under_1kb():
    assert format_bytes(500) == "500.0B"


def test_format_bytes_kb():
    assert format_bytes(2048) == "2.0KB"


def test_format_bytes_mb():
    assert format_bytes(5 * 1024 * 1024) == "5.0MB"


def test_format_bytes_gb():
    assert format_bytes(3 * 1024 ** 3) == "3.0GB"


def test_format_bytes_zero():
    assert format_bytes(0) == "0.0B"


def test_format_rate_appends_per_second():
    assert format_rate(1024) == "1.0KB/s"


def test_format_datetime_kr_am():
    ts = datetime(2026, 3, 17, 9, 5).timestamp()
    assert format_datetime_kr(ts) == "2026-03-17 오전 9:05"


def test_format_datetime_kr_pm():
    ts = datetime(2026, 3, 17, 17, 50).timestamp()
    assert format_datetime_kr(ts) == "2026-03-17 오후 5:50"


def test_format_datetime_kr_midnight_is_12am():
    ts = datetime(2026, 3, 17, 0, 0).timestamp()
    assert format_datetime_kr(ts) == "2026-03-17 오전 12:00"


def test_format_datetime_kr_noon_is_12pm():
    ts = datetime(2026, 3, 17, 12, 0).timestamp()
    assert format_datetime_kr(ts) == "2026-03-17 오후 12:00"
