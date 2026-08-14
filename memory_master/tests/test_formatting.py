import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.formatting import format_bytes, format_rate  # noqa: E402


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
