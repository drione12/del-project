import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.fast_search import _filetime_to_epoch, is_available  # noqa: E402


def test_is_available_false_off_windows():
    # This suite only ever runs off Windows (see memory_master/README.md) -
    # is_available() must short-circuit on sys.platform before ever trying
    # to load a DLL that doesn't exist here.
    assert is_available() is False


def test_filetime_zero_is_unknown_sentinel():
    assert _filetime_to_epoch(0) == 0.0


def test_filetime_epoch_matches_1601_1970_difference():
    epoch_1601 = datetime(1601, 1, 1, tzinfo=timezone.utc)
    epoch_1970 = datetime(1970, 1, 1, tzinfo=timezone.utc)
    expected_diff_seconds = (epoch_1970 - epoch_1601).total_seconds()

    # A FILETIME of exactly epoch_1970's tick count should convert to 0.0
    # (the Unix epoch itself).
    ticks_at_unix_epoch = int(expected_diff_seconds * 10_000_000)
    assert _filetime_to_epoch(ticks_at_unix_epoch) == 0.0


def test_filetime_known_date_converts_correctly():
    # 2024-01-01 00:00:00 UTC, computed independently via datetime rather
    # than by re-deriving the same arithmetic _filetime_to_epoch itself
    # uses, so this test can't just be checking the implementation against
    # itself.
    target = datetime(2024, 1, 1, tzinfo=timezone.utc)
    epoch_1601 = datetime(1601, 1, 1, tzinfo=timezone.utc)
    ticks = int((target - epoch_1601).total_seconds() * 10_000_000)

    result = _filetime_to_epoch(ticks)

    assert result == target.timestamp()
