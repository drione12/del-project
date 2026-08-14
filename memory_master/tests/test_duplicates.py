import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.duplicates import find_duplicate_files  # noqa: E402

_MIN_SIZE = 10  # small floor so short test file contents count


def test_finds_duplicate_pair():
    with tempfile.TemporaryDirectory() as tmp:
        content = b"x" * 100
        with open(os.path.join(tmp, "a.txt"), "wb") as f:
            f.write(content)
        with open(os.path.join(tmp, "b.txt"), "wb") as f:
            f.write(content)

        groups = find_duplicate_files(tmp, min_size_bytes=_MIN_SIZE)

        assert len(groups) == 1
        assert groups[0].size_bytes == 100
        assert set(groups[0].paths) == {os.path.join(tmp, "a.txt"), os.path.join(tmp, "b.txt")}


def test_ignores_files_below_size_floor():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "a.txt"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(tmp, "b.txt"), "wb") as f:
            f.write(b"x")

        groups = find_duplicate_files(tmp, min_size_bytes=_MIN_SIZE)

        assert groups == []


def test_different_content_is_not_a_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "a.txt"), "wb") as f:
            f.write(b"x" * 100)
        with open(os.path.join(tmp, "b.txt"), "wb") as f:
            f.write(b"y" * 100)

        groups = find_duplicate_files(tmp, min_size_bytes=_MIN_SIZE)

        assert groups == []


def test_three_way_duplicate_grouped_together():
    with tempfile.TemporaryDirectory() as tmp:
        content = b"z" * 50
        for name in ("a.txt", "b.txt", "c.txt"):
            with open(os.path.join(tmp, name), "wb") as f:
                f.write(content)

        groups = find_duplicate_files(tmp, min_size_bytes=_MIN_SIZE)

        assert len(groups) == 1
        assert len(groups[0].paths) == 3


def test_progress_callback_invoked():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "a.txt"), "wb") as f:
            f.write(b"x" * 100)

        calls = []
        find_duplicate_files(tmp, min_size_bytes=_MIN_SIZE, on_progress=lambda i, n: calls.append((i, n)))

        assert calls == [(1, 1)]
