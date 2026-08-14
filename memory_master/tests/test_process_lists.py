import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import process_lists  # noqa: E402
from core.process_lists import (  # noqa: E402
    BlacklistWatchdog,
    ProcessLists,
    is_whitelisted,
    load_process_lists,
    running_blacklisted_processes,
    save_process_lists,
)


def test_load_missing_file_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        lists = load_process_lists(os.path.join(tmp, "does_not_exist.json"))
        assert lists.whitelist == set()
        assert lists.blacklist == set()


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "lists.json")
        original = ProcessLists(whitelist={"backup.exe"}, blacklist={"malware.exe", "bad.exe"})

        assert save_process_lists(original, path)
        loaded = load_process_lists(path)

        assert loaded.whitelist == {"backup.exe"}
        assert loaded.blacklist == {"malware.exe", "bad.exe"}


def test_load_corrupt_file_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "lists.json")
        with open(path, "w") as f:
            f.write("not valid json{{{")
        lists = load_process_lists(path)
        assert lists.whitelist == set()
        assert lists.blacklist == set()


def test_is_whitelisted_case_insensitive():
    lists = ProcessLists(whitelist={"backup.exe"})
    assert is_whitelisted("Backup.EXE", lists)
    assert not is_whitelisted("other.exe", lists)
    assert not is_whitelisted(None, lists)


def test_running_blacklisted_processes_empty_blacklist_short_circuits():
    lists = ProcessLists(blacklist=set())
    assert running_blacklisted_processes(lists, self_pid=os.getpid()) == []


def test_running_blacklisted_processes_excludes_critical(monkeypatch):
    class FakeProc:
        def __init__(self, pid, name):
            self.info = {"pid": pid, "name": name}

    def fake_process_iter(_attrs):
        return [FakeProc(4, "System"), FakeProc(5555, "notepad.exe"), FakeProc(os.getpid(), "notepad.exe")]

    monkeypatch.setattr(process_lists.psutil, "process_iter", fake_process_iter)

    lists = ProcessLists(blacklist={"system", "notepad.exe"})
    matches = running_blacklisted_processes(lists, self_pid=os.getpid())

    names_pids = [(m.info["name"], m.info["pid"]) for m in matches]
    assert ("notepad.exe", 5555) in names_pids
    assert not any(pid == 4 for _name, pid in names_pids)  # pid 4 always critical
    assert not any(pid == os.getpid() for _name, pid in names_pids)  # self excluded


def test_watchdog_calls_on_kill_for_matched_process(monkeypatch):
    class FakeProc:
        def __init__(self, pid, name):
            self.pid = pid
            self._name = name
            self.killed = False

        def name(self):
            return self._name

        def kill(self):
            self.killed = True

    fake_proc = FakeProc(pid=99999, name="evil.exe")
    calls = {"n": 0}

    def fake_running_blacklisted(_lists, _self_pid):
        calls["n"] += 1
        return [fake_proc] if calls["n"] == 1 else []

    monkeypatch.setattr(process_lists, "running_blacklisted_processes", fake_running_blacklisted)

    events = []
    watchdog = BlacklistWatchdog(
        get_lists=lambda: ProcessLists(blacklist={"evil.exe"}),
        on_kill=lambda n, p: events.append((n, p)),
        poll_seconds=0.05,
    )
    watchdog.start()
    try:
        deadline = time.time() + 2
        while time.time() < deadline and not events:
            time.sleep(0.05)
    finally:
        watchdog.stop()

    assert events == [("evil.exe", 99999)]
    assert fake_proc.killed


def test_watchdog_stop_joins_cleanly():
    watchdog = BlacklistWatchdog(get_lists=lambda: ProcessLists(), poll_seconds=0.05)
    watchdog.start()
    watchdog.stop()
    assert watchdog._thread is None
