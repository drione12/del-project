import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.processes import find_locking_processes  # noqa: E402


def test_finds_self_holding_file_open():
    with tempfile.NamedTemporaryFile() as f:
        f.write(b"data")
        f.flush()
        # Pass a self_pid that is NOT ours, so we aren't excluded, to
        # verify the open-file scan actually detects the real lock.
        results = find_locking_processes(f.name, self_pid=-1)
        assert any(p.pid == os.getpid() for p in results)


def test_excludes_self_pid():
    with tempfile.NamedTemporaryFile() as f:
        f.write(b"data")
        f.flush()
        results = find_locking_processes(f.name, self_pid=os.getpid())
        assert not any(p.pid == os.getpid() for p in results)


def test_directory_target_matches_open_file_inside():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "locked.txt")
        with open(path, "wb") as f:
            f.write(b"data")
            f.flush()
            results = find_locking_processes(tmp, self_pid=-1)
            assert any(p.pid == os.getpid() for p in results)


def test_no_match_for_unrelated_path():
    with tempfile.NamedTemporaryFile() as f:
        f.write(b"data")
        f.flush()
        with tempfile.TemporaryDirectory() as unrelated:
            results = find_locking_processes(unrelated, self_pid=-1)
            assert not any(p.pid == os.getpid() for p in results)
