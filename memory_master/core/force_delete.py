"""The hardened force-delete core - one pipeline, shared by every
destructive-action entry point in the app (manual path entry, the
Cleanup page's drag-drop zone and duplicate-file/image cleanup, once
those are built). Two phases, always in this order:

  analyze(path)  - dry run: protected-path check, admin check, computes
                   the kill list, but kills nothing and deletes nothing.
                   Safe to call as often as needed (e.g. every time the
                   user changes the target path in the UI).
  execute(path, kill_pids, options, ...) - the actual operation. Takes the
                   *exact* kill_pids list analyze() returned (the caller
                   is expected to have shown it to the user and gotten
                   explicit confirmation) rather than recomputing it, so
                   there's no way for what's shown to the user and what
                   actually gets killed to silently drift apart between
                   the two calls.

Both reference scripts that fed this app's design ran their equivalent of
execute() directly from a button click, computed and killed processes in
the same step with nothing shown first, used os.system() for icacls/attrib,
and deleted via shutil.rmtree with no progress/cancel. This module fixes
all of that - see the plan doc's "Force-delete core" section for the full
enumerated list of fixes.

Admin rights are checked and reported, but only actually *gate* the
ownership-override escalation path (which genuinely cannot succeed
without them) - not the whole pipeline. A file the current user already
owns deletes just fine without admin rights, and demanding a full
restart-as-administrator for that common case would be friction the
underlying problem doesn't need.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

import psutil

from core.critical_processes import is_critical
from core.logging_setup import get_logger
from core.ownership import clear_readonly, grant_full_control, take_ownership
from core.path_guard import is_protected
from core.winpath import long_path

logger = get_logger(__name__)

if sys.platform == "win32":
    from core.processes import LockingProcess, find_locking_processes
else:
    @dataclass
    class LockingProcess:
        pid: int
        name: str

    def find_locking_processes(target_path: str, self_pid: int) -> List[LockingProcess]:
        return []


@dataclass
class AnalyzeResult:
    path: str
    blocked: bool
    block_reason: str
    is_admin: bool
    file_count: int
    total_bytes: int
    locking_processes: List[LockingProcess] = field(default_factory=list)


def is_running_as_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        logger.warning("IsUserAnAdmin check failed", exc_info=True)
        return False


def request_admin_restart() -> bool:
    """Re-launches this app elevated via the UAC prompt (the "runas" shell
    verb). Does not exit the current, non-elevated instance itself -
    that's left to the caller (typically: launch elevated, then quit).
    """
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        args = " ".join(f'"{a}"' for a in sys.argv)
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, None, 1)
        return result > 32  # ShellExecuteW: any return > 32 means success
    except Exception:
        logger.warning("restart-as-admin failed", exc_info=True)
        return False


def _scan(path: str):
    if os.path.isfile(long_path(path)):
        try:
            return 1, os.path.getsize(long_path(path))
        except OSError:
            return 1, 0

    file_count = 0
    total_bytes = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            file_count += 1
            try:
                total_bytes += os.path.getsize(long_path(os.path.join(root, name)))
            except OSError:
                pass
    return file_count, total_bytes


def analyze(path: str) -> AnalyzeResult:
    blocked, reason = is_protected(path)
    is_admin = is_running_as_admin()

    if blocked:
        return AnalyzeResult(path, True, reason, is_admin, 0, 0, [])

    if not os.path.exists(path):
        return AnalyzeResult(path, True, "경로가 존재하지 않습니다.", is_admin, 0, 0, [])

    file_count, total_bytes = _scan(path)

    locking: List[LockingProcess] = []
    self_pid = os.getpid()
    try:
        locking = [p for p in find_locking_processes(path, self_pid) if not is_critical(p.pid, p.name, self_pid)]
    except Exception:
        logger.warning("locking-process scan failed for %s", path, exc_info=True)

    return AnalyzeResult(path, False, "", is_admin, file_count, total_bytes, locking)


@dataclass
class ExecuteOptions:
    kill_locking_processes: bool = True
    take_ownership_on_failure: bool = True
    secure_shred: bool = False
    reboot_delete_fallback: bool = True


@dataclass
class ExecuteProgress:
    processed_count: int
    total_count: int
    current_path: str


@dataclass
class ExecuteResult:
    deleted_count: int
    failed_count: int
    cancelled: bool
    killed_pids: List[int] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    reboot_scheduled: List[str] = field(default_factory=list)


class DeleteOutcome(Enum):
    DELETED = "deleted"
    SCHEDULED_ON_REBOOT = "scheduled_on_reboot"
    FAILED = "failed"


_SHRED_PASSES = (b"\x00", b"\xff", None)  # None = random bytes


def _secure_shred_file(path: str) -> None:
    """Opt-in 3-pass overwrite before deletion (zeros, then 0xFF, then
    random) - genuinely effective on spinning HDDs, meaningfully weaker on
    SSDs (wear-leveling means the overwrite doesn't reliably hit the same
    physical flash cells the original data occupied), a caveat the caller
    is expected to show in the UI before this option can be enabled.
    """
    safe_path = long_path(path)
    try:
        size = os.path.getsize(safe_path)
    except OSError:
        return
    if size == 0:
        return
    with open(safe_path, "r+b") as f:
        for pattern in _SHRED_PASSES:
            f.seek(0)
            f.write(os.urandom(size) if pattern is None else pattern * size)
            f.flush()
            os.fsync(f.fileno())


def _kill_processes(pids: List[int]) -> List[int]:
    killed = []
    self_pid = os.getpid()
    for pid in pids:
        if is_critical(pid, None, self_pid):
            continue
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                proc.kill()
            killed.append(pid)
        except psutil.NoSuchProcess:
            continue
        except Exception:
            logger.warning("failed to kill pid=%s", pid, exc_info=True)
    return killed


def _schedule_delete_on_reboot(path: str) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes

    from core.privileges import enable_privilege

    if not enable_privilege("SeRestorePrivilege"):
        return False
    movefile_delay_until_reboot = 0x4
    ok = ctypes.windll.kernel32.MoveFileExW(long_path(path), None, movefile_delay_until_reboot)
    return bool(ok)


def _delete_one(path: str, options: ExecuteOptions, is_dir: bool, is_admin: bool):
    safe_path = long_path(path)
    try:
        if options.secure_shred and not is_dir:
            _secure_shred_file(path)
        if is_dir:
            os.rmdir(safe_path)
        else:
            os.remove(safe_path)
        return DeleteOutcome.DELETED, ""
    except OSError as first_error:
        if not options.take_ownership_on_failure:
            return DeleteOutcome.FAILED, str(first_error)
        if sys.platform == "win32" and not is_admin:
            # takeown/icacls cannot succeed without admin rights - fail
            # clearly now instead of shelling out to commands that would
            # just fail anyway with a much less useful error.
            return DeleteOutcome.FAILED, "관리자 권한이 필요합니다 (소유권 변경 불가)"

        clear_readonly(path)
        take_ownership(path, recurse=False)
        grant_full_control(path, recurse=False)
        try:
            if is_dir:
                os.rmdir(safe_path)
            else:
                os.remove(safe_path)
            return DeleteOutcome.DELETED, ""
        except OSError as second_error:
            # The reboot-delete fallback (MOVEFILE_DELAY_UNTIL_REBOOT) is
            # documented for files; a non-empty directory can't be moved
            # this way, so it isn't attempted for one.
            if options.reboot_delete_fallback and sys.platform == "win32" and not is_dir:
                if _schedule_delete_on_reboot(path):
                    return DeleteOutcome.SCHEDULED_ON_REBOOT, ""
            return DeleteOutcome.FAILED, str(second_error)


def execute(
    path: str,
    kill_pids: List[int],
    options: ExecuteOptions,
    on_progress: Optional[Callable[[ExecuteProgress], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> ExecuteResult:
    """Deletes path (file or directory tree) bottom-up so a real progress
    count and a working mid-operation Cancel are both possible -
    shutil.rmtree (what both reference scripts used) offers neither.
    Re-checks is_protected() itself rather than trusting the caller not to
    have changed the target between analyze() and here.
    """
    blocked, reason = is_protected(path)
    if blocked:
        return ExecuteResult(0, 1, False, [], [f"차단됨: {reason}"])

    is_admin = is_running_as_admin()

    killed_pids: List[int] = []
    if options.kill_locking_processes and kill_pids:
        killed_pids = _kill_processes(kill_pids)

    if os.path.isfile(long_path(path)):
        entries = [(path, False)]
    else:
        entries = []
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                entries.append((os.path.join(root, name), False))
            for name in dirs:
                entries.append((os.path.join(root, name), True))
        entries.append((path, True))

    total = len(entries)
    deleted = 0
    failed = 0
    failures: List[str] = []
    reboot_scheduled: List[str] = []
    cancelled = False

    for i, (entry_path, is_dir) in enumerate(entries):
        if should_cancel is not None and should_cancel():
            cancelled = True
            break

        outcome, error = _delete_one(entry_path, options, is_dir, is_admin)
        if outcome == DeleteOutcome.DELETED:
            deleted += 1
        elif outcome == DeleteOutcome.SCHEDULED_ON_REBOOT:
            reboot_scheduled.append(entry_path)
        else:
            failed += 1
            failures.append(f"{entry_path}: {error}")
            logger.warning("delete failed: %s: %s", entry_path, error)

        if on_progress is not None:
            on_progress(ExecuteProgress(i + 1, total, entry_path))

    return ExecuteResult(deleted, failed, cancelled, killed_pids, failures, reboot_scheduled)
