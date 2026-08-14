import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.memory_allocation import compute_segments  # noqa: E402


def _segment_bytes_sum(allocation) -> int:
    return sum(seg.bytes_ for seg in allocation.segments)


def test_segments_sum_to_total_without_compressed():
    allocation = compute_segments(total=16_000_000_000, used=8_000_000_000, available=10_000_000_000,
                                   free=6_000_000_000, compressed=None)
    assert _segment_bytes_sum(allocation) == 16_000_000_000


def test_segments_sum_to_total_with_compressed():
    allocation = compute_segments(total=16_000_000_000, used=8_000_000_000, available=10_000_000_000,
                                   free=6_000_000_000, compressed=500_000_000)
    assert _segment_bytes_sum(allocation) == 16_000_000_000


def test_no_compressed_segment_when_none():
    allocation = compute_segments(total=1000, used=500, available=700, free=400, compressed=None)
    labels = [seg.label for seg in allocation.segments]
    assert "압축됨" not in labels


def test_compressed_segment_present_when_positive():
    allocation = compute_segments(total=1000, used=500, available=700, free=200, compressed=100)
    labels = [seg.label for seg in allocation.segments]
    assert "압축됨" in labels


def test_percentages_sum_to_about_100():
    allocation = compute_segments(total=1000, used=500, available=700, free=400, compressed=None)
    total_percent = sum(seg.percent for seg in allocation.segments)
    assert abs(total_percent - 100.0) < 1.0


def test_zero_total_does_not_crash():
    allocation = compute_segments(total=0, used=0, available=0, free=0, compressed=None)
    assert allocation.total == 1


def test_compressed_never_exceeds_used():
    allocation = compute_segments(total=1000, used=100, available=900, free=50, compressed=99999)
    compressed_seg = next(seg for seg in allocation.segments if seg.label == "압축됨")
    assert compressed_seg.bytes_ <= 100
