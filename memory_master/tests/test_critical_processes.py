import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import critical_processes as cp  # noqa: E402


def test_self_pid_is_critical():
    assert cp.is_critical(pid=1234, name="somebenignapp.exe", self_pid=1234)


def test_pid_0_is_critical():
    assert cp.is_critical(pid=0, name="System Idle Process", self_pid=999)


def test_pid_4_is_critical():
    assert cp.is_critical(pid=4, name="System", self_pid=999)


def test_lsass_is_critical_regardless_of_pid():
    assert cp.is_critical(pid=555, name="lsass.exe", self_pid=999)


def test_name_match_is_case_insensitive():
    assert cp.is_critical(pid=555, name="LSASS.EXE", self_pid=999)


def test_ordinary_process_is_not_critical():
    assert not cp.is_critical(pid=5555, name="notepad.exe", self_pid=999)


def test_none_name_does_not_crash():
    assert not cp.is_critical(pid=5555, name=None, self_pid=999)
