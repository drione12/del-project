import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.formatting import (  # noqa: E402
    format_attributes,
    format_bytes,
    format_datetime,
    format_extension,
    format_rate,
)


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


def test_format_datetime_24_hour_morning():
    ts = datetime(2026, 3, 17, 9, 5).timestamp()
    assert format_datetime(ts) == "2026-03-17 09:05"


def test_format_datetime_24_hour_afternoon():
    ts = datetime(2026, 3, 17, 17, 50).timestamp()
    assert format_datetime(ts) == "2026-03-17 17:50"


def test_format_datetime_midnight():
    ts = datetime(2026, 3, 17, 0, 0).timestamp()
    assert format_datetime(ts) == "2026-03-17 00:00"


def test_format_datetime_noon():
    ts = datetime(2026, 3, 17, 12, 0).timestamp()
    assert format_datetime(ts) == "2026-03-17 12:00"


def test_format_datetime_zero_is_unknown_sentinel():
    assert format_datetime(0) == ""


def test_format_attributes_no_flags():
    assert format_attributes(0) == ""


def test_format_attributes_single_flag():
    assert format_attributes(0x1) == "R"  # READONLY


def test_format_attributes_directory():
    assert format_attributes(0x10) == "D"


def test_format_attributes_multiple_flags_in_fixed_order():
    # READONLY + HIDDEN + ARCHIVE - order must match FormatAttributes'
    # (src/main.cpp) own R,H,S,D,A,C,E,T,O,L order, not insertion order.
    assert format_attributes(0x1 | 0x2 | 0x20) == "RHA"


def test_format_extension_normal_file():
    assert format_extension("report.pdf", is_dir=False) == "pdf"


def test_format_extension_preserves_case():
    assert format_extension("PHOTO.JPG", is_dir=False) == "JPG"


def test_format_extension_multi_dot_uses_last():
    assert format_extension("archive.tar.gz", is_dir=False) == "gz"


def test_format_extension_directory_is_empty_even_with_dot_in_name():
    assert format_extension("archive.zip", is_dir=True) == ""


def test_format_extension_no_dot_is_empty():
    assert format_extension("README", is_dir=False) == ""


def test_format_extension_leading_dot_dotfile_is_empty():
    # Matches Explorer's convention (and EverythingClone's own
    # GetExtensionDisplay), unlike matches_category's deliberately
    # different Windows-native parsing for the same name - see both
    # functions' docstrings.
    assert format_extension(".gitignore", is_dir=False) == ""
