import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.winpath import long_path  # noqa: E402


def test_prefixes_local_path_on_windows():
    assert long_path(r"C:\Users\a\file.txt", platform="win32") == r"\\?\C:\Users\a\file.txt"


def test_prefixes_unc_path_on_windows():
    assert long_path(r"\\server\share\file.txt", platform="win32") == r"\\?\UNC\server\share\file.txt"


def test_idempotent_on_already_prefixed_path():
    p = r"\\?\C:\Users\a\file.txt"
    assert long_path(p, platform="win32") == p


def test_noop_off_windows():
    assert long_path(r"/home/user/file.txt", platform="linux") == r"/home/user/file.txt"


def test_defaults_to_real_sys_platform():
    # True on any host OS by construction, since long_path's `platform` kwarg
    # defaults to sys.platform - this checks the wiring, not a specific
    # platform's behavior (test_prefixes_local_path_on_windows /
    # test_noop_off_windows already cover platform="win32"/"linux" explicitly).
    # A hardcoded "must be a no-op" assertion here is what broke CI: this
    # exact test also runs on the real windows-latest runner, where
    # sys.platform genuinely is "win32" and long_path correctly does prefix.
    assert long_path(r"C:\Users\a\file.txt") == long_path(r"C:\Users\a\file.txt", platform=sys.platform)


def test_empty_path_returned_unchanged():
    assert long_path("", platform="win32") == ""
