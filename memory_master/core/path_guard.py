"""Protected-path checks for the force-delete pipeline.

Refuses to touch core Windows install locations, or any *ancestor* of one
(deleting a parent of C:\\Windows is equally catastrophic), and requires
extra depth under a user-profile root so a whole C:\\Users\\<name> can't be
wiped out as a side effect of a generic "shallow path" rule.

Deliberately uses `ntpath` (Windows path semantics) rather than the
platform-dependent `os.path` throughout, since these are always Windows-
style paths regardless of what OS this code happens to run on - `os.path`
*is* `ntpath` on Windows, but is `posixpath` (which doesn't understand
backslash separators at all) everywhere else, including this Linux dev
environment. Using `ntpath` explicitly is what makes this module correct on
Windows *and* unit-testable here, rather than one or the other.
"""
from __future__ import annotations

import ntpath
import os
import sys
from pathlib import PureWindowsPath
from typing import List, Tuple

# Minimum path depth (segments below the drive root) required for a target
# to be deletable at all - guards against obviously-too-shallow targets even
# when they don't literally match one of the named roots below.
MIN_PATH_DEPTH = 2

# C:\Users\<name> is exactly MIN_PATH_DEPTH deep, which the generic rule
# above would allow - wiping someone's whole profile. Require one more
# level specifically under a user-profile root.
USER_PROFILE_MIN_DEPTH = 3


def _normalize(path: str) -> PureWindowsPath:
    return PureWindowsPath(ntpath.normcase(path))


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _protected_roots() -> List[str]:
    """Concrete protected paths. The System32/pagefile.sys/bootmgr entries
    were confirmed useful from a reference implementation; the surrounding
    ancestor/depth logic is this module's own, applied on top of them.
    """
    system_root = _env("SystemRoot", r"C:\Windows")
    program_files = _env("ProgramFiles", r"C:\Program Files")
    program_files_x86 = _env("ProgramFiles(x86)", r"C:\Program Files (x86)")
    program_data = _env("ProgramData", r"C:\ProgramData")
    system_drive = _env("SystemDrive", "C:") + "\\"

    roots = [
        system_root,
        ntpath.join(system_root, "System32"),
        program_files,
        program_files_x86,
        program_data,
        # Deliberately NOT the bare drive root itself (e.g. "C:\") here:
        # is_within_or_equal() treats "under this root" as protected, and
        # every path on the drive is trivially under its own root - that
        # would block everything, not just the root. The drive root itself
        # is still refused, just via the depth check below (it's depth 0,
        # under MIN_PATH_DEPTH) rather than a blanket path-prefix match.
        ntpath.join(system_drive, "pagefile.sys"),
        ntpath.join(system_drive, "bootmgr"),
    ]

    # Never let the app delete its own install/run directory out from under
    # itself mid-operation.
    try:
        roots.append(ntpath.dirname(ntpath.abspath(sys.argv[0])))
    except Exception:
        pass

    return roots


PROTECTED_ROOTS = _protected_roots()


def is_within_or_equal(path: str, other: str) -> bool:
    """True if `path` is `other` itself, or a real descendant of it.

    This is the fix for the sibling-folder boundary bug found in both
    reference scripts: a naive `str.startswith()` check treats
    "C:\\Users\\bob\\Downloads2" as "inside" "C:\\Users\\bob\\Downloads"
    because one string happens to prefix the other. Comparing path *parts*
    instead of raw characters avoids that.
    """
    p = _normalize(ntpath.abspath(path))
    o = _normalize(ntpath.abspath(other))
    return p == o or o in p.parents


def _depth_below_root(path: PureWindowsPath) -> int:
    return len(path.parts) - 1


def _is_user_profile_subpath(path: PureWindowsPath) -> bool:
    parts = path.parts
    return len(parts) >= 2 and parts[1].lower() == "users"


def _check_single(abs_target: str) -> Tuple[bool, str]:
    norm_target = _normalize(abs_target)

    for root in PROTECTED_ROOTS:
        if not root:
            continue
        if is_within_or_equal(abs_target, root):
            return True, f"핵심 시스템 경로({root})이거나 그 안에 포함된 항목입니다."
        norm_root = _normalize(ntpath.abspath(root))
        if norm_target in norm_root.parents:
            return True, f"핵심 시스템 경로({root})의 상위 폴더입니다."

    depth = _depth_below_root(norm_target)
    if _is_user_profile_subpath(norm_target):
        if depth < USER_PROFILE_MIN_DEPTH:
            return True, "사용자 프로필 폴더 전체는 삭제할 수 없습니다."
    elif depth < MIN_PATH_DEPTH:
        return True, "드라이브 루트에 너무 가까운 경로는 삭제할 수 없습니다."

    return False, ""


def is_protected(target: str) -> Tuple[bool, str]:
    """Returns (True, reason) if target must be refused, else (False, "")."""
    try:
        abs_target = ntpath.abspath(target)
    except Exception as e:
        return True, f"경로를 확인할 수 없습니다: {e}"

    blocked, reason = _check_single(abs_target)
    if blocked:
        return True, reason

    # Defend against a symlink/junction that resolves somewhere protected
    # even though the raw path looks shallow/safe. Real symlink resolution
    # only happens when this actually runs on Windows (ntpath.realpath on
    # another OS can't see the real filesystem) - a documented limitation
    # of testing this specific check off-Windows, not of the check itself.
    try:
        real_target = ntpath.realpath(abs_target)
    except Exception:
        real_target = abs_target
    if ntpath.normcase(real_target) != ntpath.normcase(abs_target):
        blocked, reason = _check_single(real_target)
        if blocked:
            return True, reason

    return False, ""
