"""Pure-logic tests for path_guard - runs on any OS (PureWindowsPath, not
the real filesystem), so these run both in CI on windows-latest and locally
during development.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import path_guard  # noqa: E402


def test_windows_root_is_protected():
    blocked, _ = path_guard.is_protected(r"C:\Windows")
    assert blocked


def test_windows_system32_is_protected():
    blocked, _ = path_guard.is_protected(r"C:\Windows\System32")
    assert blocked


def test_windows_subfolder_is_protected():
    blocked, _ = path_guard.is_protected(r"C:\Windows\Temp\something")
    assert blocked


def test_program_files_is_protected():
    blocked, _ = path_guard.is_protected(r"C:\Program Files\SomeApp")
    assert blocked


def test_program_files_x86_is_protected():
    blocked, _ = path_guard.is_protected(r"C:\Program Files (x86)\SomeApp")
    assert blocked


def test_pagefile_is_protected():
    blocked, _ = path_guard.is_protected(r"C:\pagefile.sys")
    assert blocked


def test_drive_root_is_protected():
    blocked, _ = path_guard.is_protected(r"C:\ ".strip())
    assert blocked


def test_whole_user_profile_is_protected():
    blocked, _ = path_guard.is_protected(r"C:\Users\bob")
    assert blocked


def test_users_folder_itself_is_protected():
    blocked, _ = path_guard.is_protected(r"C:\Users")
    assert blocked


def test_shallow_top_level_folder_is_protected():
    blocked, _ = path_guard.is_protected(r"C:\SomeRandomFolder")
    assert blocked


def test_normal_deep_user_path_is_allowed():
    blocked, _ = path_guard.is_protected(r"C:\Users\bob\Downloads\old_installer.exe")
    assert not blocked


def test_normal_deep_non_user_path_is_allowed():
    blocked, _ = path_guard.is_protected(r"D:\Projects\stale_build\output.bin")
    assert not blocked


def test_is_within_or_equal_true_for_descendant():
    assert path_guard.is_within_or_equal(r"C:\Windows\System32\foo.dll", r"C:\Windows")


def test_is_within_or_equal_true_for_equal_path():
    assert path_guard.is_within_or_equal(r"C:\Windows", r"C:\Windows")


def test_is_within_or_equal_rejects_sibling_with_shared_prefix():
    # The exact boundary bug found in both reference scripts: a naive
    # startswith() check would treat this sibling folder as "inside".
    assert not path_guard.is_within_or_equal(
        r"C:\Users\bob\Downloads2\file.txt", r"C:\Users\bob\Downloads"
    )


def test_is_within_or_equal_rejects_unrelated_path():
    assert not path_guard.is_within_or_equal(r"C:\Windows2\foo.dll", r"C:\Windows")
