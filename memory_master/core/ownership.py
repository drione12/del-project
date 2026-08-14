"""Ownership/permission override for locked-or-permission-denied files -
the icacls/attrib/takeown half of the force-delete pipeline. Uses
subprocess.run with argument lists (never shell=True) so a path containing
spaces or special characters can't be misinterpreted as extra arguments or
trigger shell metacharacter expansion - both reference scripts used
os.system() with an interpolated path string, which has exactly that
injection risk as well as silently swallowing the actual exit code/stderr.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import List

from core.logging_setup import get_logger

logger = get_logger(__name__)

_TIMEOUT_SECONDS = 30


@dataclass
class OwnershipResult:
    ok: bool
    command: str
    output: str


def _run(args: List[str]) -> OwnershipResult:
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False
        )
        ok = completed.returncode == 0
        output = (completed.stdout or "") + (completed.stderr or "")
        if not ok:
            logger.warning("command failed (%s): %s", " ".join(args), output.strip())
        return OwnershipResult(ok=ok, command=" ".join(args), output=output.strip())
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("command raised: %s: %s", " ".join(args), e)
        return OwnershipResult(ok=False, command=" ".join(args), output=str(e))


def take_ownership(path: str, recurse: bool = True) -> OwnershipResult:
    args = ["takeown", "/f", path]
    if recurse:
        args += ["/r", "/d", "y"]
    return _run(args)


def grant_full_control(path: str, recurse: bool = True) -> OwnershipResult:
    username = os.environ.get("USERNAME", "")
    args = ["icacls", path, "/grant", f"{username}:F"]
    if recurse:
        args.append("/t")
    args += ["/c", "/q"]
    return _run(args)


def clear_readonly(path: str) -> OwnershipResult:
    return _run(["attrib", "-r", "-s", "-h", path])
