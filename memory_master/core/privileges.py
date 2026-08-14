"""ctypes port of src/privileges.cpp's EnablePrivilege (the C++ Everything
clone in this same repo) - enables a Windows privilege in the current
process token via OpenProcessToken -> LookupPrivilegeValueW ->
AdjustTokenPrivileges.

Most privileges (including SE_RESTORE_NAME, needed for MoveFileExW's
MOVEFILE_DELAY_UNTIL_REBOOT) are present-but-disabled by default even in an
administrator's token; AdjustTokenPrivileges is how a process turns one on
for itself. This module is Windows-only (ctypes.windll doesn't exist on any
other OS) and can't be exercised on this Linux dev environment - kept small
and a near-literal port of the already-working C++ logic to minimize the
surface that's genuinely new/unverified.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

advapi32 = ctypes.windll.advapi32
kernel32 = ctypes.windll.kernel32

TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x00000002


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]


advapi32.OpenProcessToken.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
]
advapi32.OpenProcessToken.restype = wintypes.BOOL

advapi32.LookupPrivilegeValueW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    ctypes.POINTER(LUID),
]
advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL

advapi32.AdjustTokenPrivileges.argtypes = [
    wintypes.HANDLE,
    wintypes.BOOL,
    ctypes.POINTER(TOKEN_PRIVILEGES),
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL

kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetLastError.restype = wintypes.DWORD

ERROR_NOT_ALL_ASSIGNED = 1300


def enable_privilege(name: str) -> bool:
    """Enables a privilege (e.g. "SeRestorePrivilege") in this process's
    token. Returns False rather than raising on any failure, so callers can
    treat "the privileged fallback isn't available" as an ordinary
    condition to handle instead of a crash - matches EnablePrivilege's
    bool-return shape on the C++ side.
    """
    h_token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
        ctypes.byref(h_token),
    ):
        return False

    try:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False

        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED

        if not advapi32.AdjustTokenPrivileges(h_token, False, ctypes.byref(tp), 0, None, None):
            return False

        # AdjustTokenPrivileges can return success while still not having
        # enabled everything requested (e.g. the token doesn't hold this
        # privilege at all, just not disabled) - GetLastError distinguishes
        # a full success from a partial one (ERROR_NOT_ALL_ASSIGNED).
        return kernel32.GetLastError() != ERROR_NOT_ALL_ASSIGNED
    finally:
        kernel32.CloseHandle(h_token)
