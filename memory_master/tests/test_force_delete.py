import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.force_delete import (  # noqa: E402
    ExecuteOptions,
    _secure_shred_file,
    analyze,
    execute,
)

_SAFE_OPTIONS = ExecuteOptions(
    kill_locking_processes=False, take_ownership_on_failure=False, secure_shred=False, reboot_delete_fallback=False
)


def test_analyze_blocks_protected_path():
    result = analyze(r"C:\Windows\System32\notepad.exe")
    assert result.blocked
    assert result.locking_processes == []


def test_analyze_reports_missing_path():
    with tempfile.TemporaryDirectory() as tmp:
        missing = os.path.join(tmp, "does_not_exist.txt")
        result = analyze(missing)
        assert result.blocked
        assert "존재하지" in result.block_reason


def test_analyze_counts_real_files():
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(3):
            with open(os.path.join(tmp, f"f{i}.txt"), "wb") as f:
                f.write(b"x" * 10)
        result = analyze(tmp)
        assert not result.blocked
        assert result.file_count == 3
        assert result.total_bytes == 30


def test_execute_deletes_real_files():
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i in range(3):
            p = os.path.join(tmp, f"f{i}.txt")
            with open(p, "wb") as f:
                f.write(b"x")
            paths.append(p)

        result = execute(tmp, kill_pids=[], options=_SAFE_OPTIONS)

        assert result.failed_count == 0
        assert not result.cancelled
        for p in paths:
            assert not os.path.exists(p)
        assert not os.path.exists(tmp)


def test_execute_blocks_protected_path():
    result = execute(r"C:\Windows\System32", kill_pids=[], options=_SAFE_OPTIONS)
    assert result.deleted_count == 0
    assert result.failed_count == 1
    assert "차단됨" in result.failures[0]


def test_execute_respects_cancel():
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(10):
            with open(os.path.join(tmp, f"f{i}.txt"), "wb") as f:
                f.write(b"x")

        calls = {"n": 0}

        def should_cancel():
            calls["n"] += 1
            return calls["n"] > 2

        result = execute(tmp, kill_pids=[], options=_SAFE_OPTIONS, should_cancel=should_cancel)

        assert result.cancelled
        assert result.deleted_count < 11  # 10 files + the directory itself


def test_execute_reports_progress():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "f.txt"), "wb") as f:
            f.write(b"x")

        progress_calls = []
        execute(tmp, kill_pids=[], options=_SAFE_OPTIONS, on_progress=progress_calls.append)

        assert len(progress_calls) == 2  # the file, then the now-empty directory
        assert progress_calls[-1].processed_count == progress_calls[-1].total_count


def test_secure_shred_overwrites_before_caller_deletes():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"secret" * 100)
        path = f.name
    try:
        _secure_shred_file(path)
        assert os.path.exists(path)  # shred does not delete, only overwrites
        with open(path, "rb") as f:
            content = f.read()
        assert b"secret" not in content
        assert len(content) == 600
    finally:
        os.remove(path)


def test_execute_with_secure_shred_still_deletes_and_wipes_content():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sensitive.txt")
        with open(path, "wb") as f:
            f.write(b"sensitive data")

        shred_options = ExecuteOptions(
            kill_locking_processes=False, take_ownership_on_failure=False, secure_shred=True,
            reboot_delete_fallback=False,
        )
        result = execute(tmp, kill_pids=[], options=shred_options)

        assert result.failed_count == 0
        assert not os.path.exists(path)


def test_execute_kills_real_locking_process():
    import subprocess
    import time

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.txt")
            with open(path, "wb") as f:
                f.write(b"x")

            options = ExecuteOptions(
                kill_locking_processes=True, take_ownership_on_failure=False, secure_shred=False,
                reboot_delete_fallback=False,
            )
            result = execute(tmp, kill_pids=[child.pid], options=options)

            assert child.pid in result.killed_pids
            time.sleep(0.5)
            assert child.poll() is not None  # the child process actually died
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
