import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.quarantine import move_to_quarantine  # noqa: E402


def test_moves_file_to_quarantine_dir():
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src", "file.txt")
        os.makedirs(os.path.dirname(src))
        with open(src, "wb") as f:
            f.write(b"data")
        quarantine_dir = os.path.join(tmp, "quarantine")

        result = move_to_quarantine(src, quarantine_dir)

        assert result.ok
        assert not os.path.exists(src)
        assert os.path.exists(result.quarantined_path)
        with open(result.quarantined_path, "rb") as f:
            assert f.read() == b"data"


def test_does_not_clobber_existing_file_with_same_name():
    with tempfile.TemporaryDirectory() as tmp:
        quarantine_dir = os.path.join(tmp, "quarantine")
        os.makedirs(quarantine_dir)
        existing = os.path.join(quarantine_dir, "file.txt")
        with open(existing, "wb") as f:
            f.write(b"original")

        src = os.path.join(tmp, "file.txt")
        with open(src, "wb") as f:
            f.write(b"new")

        result = move_to_quarantine(src, quarantine_dir)

        assert result.ok
        assert result.quarantined_path != existing
        with open(existing, "rb") as f:
            assert f.read() == b"original"  # untouched
        with open(result.quarantined_path, "rb") as f:
            assert f.read() == b"new"


def test_reports_failure_for_missing_source():
    with tempfile.TemporaryDirectory() as tmp:
        result = move_to_quarantine(os.path.join(tmp, "does_not_exist.txt"), os.path.join(tmp, "quarantine"))
        assert not result.ok
        assert result.error
