import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.trash import send_to_trash  # noqa: E402


def test_sends_file_to_trash():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "file.txt")
        with open(path, "wb") as f:
            f.write(b"data")

        result = send_to_trash(path)

        assert result.ok
        assert not os.path.exists(path)


def test_sends_directory_to_trash():
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "sub")
        os.makedirs(target)
        with open(os.path.join(target, "f.txt"), "wb") as f:
            f.write(b"x")

        result = send_to_trash(target)

        assert result.ok
        assert not os.path.exists(target)


def test_missing_path_returns_error():
    with tempfile.TemporaryDirectory() as tmp:
        result = send_to_trash(os.path.join(tmp, "does_not_exist.txt"))

        assert not result.ok
        assert result.error


def test_blocks_protected_path_without_calling_send2trash():
    result = send_to_trash(r"C:\Windows\System32")

    assert not result.ok
    assert "핵심 시스템 경로" in result.error
