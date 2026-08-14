import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.optimizer import _delete_paths  # noqa: E402


def test_delete_paths_removes_existing_files_and_counts_bytes():
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        total_size = 0
        for i in range(3):
            path = os.path.join(tmp, f"file{i}.txt")
            content = f"hello {i}".encode()
            with open(path, "wb") as f:
                f.write(content)
            total_size += len(content)
            paths.append(path)

        result = _delete_paths(paths)

        assert result.deleted_count == 3
        assert result.skipped_count == 0
        assert result.freed_bytes == total_size
        for path in paths:
            assert not os.path.exists(path)


def test_delete_paths_skips_missing_files():
    result = _delete_paths(["/nonexistent/path/that/does/not/exist.txt"])
    assert result.deleted_count == 0
    assert result.skipped_count == 1
    assert result.freed_bytes == 0


def test_delete_paths_mixed_existing_and_missing():
    with tempfile.TemporaryDirectory() as tmp:
        real_path = os.path.join(tmp, "real.txt")
        with open(real_path, "wb") as f:
            f.write(b"data")

        result = _delete_paths([real_path, "/no/such/file.txt"])

        assert result.deleted_count == 1
        assert result.skipped_count == 1
        assert result.freed_bytes == 4
