import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.elevation import _build_relaunch_args, is_running_as_admin  # noqa: E402


def test_is_running_as_admin_is_false_off_windows():
    if sys.platform != "win32":
        assert is_running_as_admin() is False


def test_build_relaunch_args_with_no_extra_args():
    argv = ["MemoryMaster.exe"]
    assert _build_relaunch_args(argv, None) == ["MemoryMaster.exe"]


def test_build_relaunch_args_appends_extra_args():
    argv = ["MemoryMaster.exe"]
    assert _build_relaunch_args(argv, ["--start-page", "search"]) == [
        "MemoryMaster.exe",
        "--start-page",
        "search",
    ]


def test_build_relaunch_args_does_not_mutate_input_argv():
    argv = ["MemoryMaster.exe"]
    _build_relaunch_args(argv, ["--start-page", "search"])
    assert argv == ["MemoryMaster.exe"]
