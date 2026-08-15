import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.everything_embed import build_launch_args  # noqa: E402


def test_build_launch_args_shape():
    args = build_launch_args(r"C:\Memory Master\resources\EverythingClone.exe", 12345, 800, 600)
    assert args == [
        r"C:\Memory Master\resources\EverythingClone.exe",
        "--embed-parent-hwnd",
        "12345",
        "--embed-width",
        "800",
        "--embed-height",
        "600",
    ]


def test_build_launch_args_exe_path_is_always_first():
    args = build_launch_args("EverythingClone.exe", 1, 100, 100)
    assert args[0] == "EverythingClone.exe"


def test_build_launch_args_values_become_strings():
    args = build_launch_args("EverythingClone.exe", 999, 1, 2)
    assert all(isinstance(a, str) for a in args)
