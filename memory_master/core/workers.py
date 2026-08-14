"""QThread workers for the force-delete pipeline's two phases - Analyze
(dry run: compute the kill list without touching anything) and Execute
(the actual deletion) - kept off the UI thread since both can take a
real, user-noticeable amount of time (scanning for locking processes,
walking a large directory tree).
"""
from __future__ import annotations

from typing import List

from PyQt5.QtCore import QThread, pyqtSignal

from core.force_delete import ExecuteOptions, analyze, execute


class AnalyzeWorker(QThread):
    resultReady = pyqtSignal(object)  # AnalyzeResult

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self) -> None:
        self.resultReady.emit(analyze(self._path))


class ExecuteWorker(QThread):
    progress = pyqtSignal(object)  # ExecuteProgress
    resultReady = pyqtSignal(object)  # ExecuteResult

    def __init__(self, path: str, kill_pids: List[int], options: ExecuteOptions, parent=None):
        super().__init__(parent)
        self._path = path
        self._kill_pids = kill_pids
        self._options = options
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        result = execute(
            self._path,
            self._kill_pids,
            self._options,
            on_progress=lambda p: self.progress.emit(p),
            should_cancel=lambda: self._cancel_requested,
        )
        self.resultReady.emit(result)
