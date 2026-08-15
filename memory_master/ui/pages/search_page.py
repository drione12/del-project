"""File search page ("파일 검색"). Two views behind one coordinator,
switched once at construction based on whether this process is elevated:

  _EmbeddedSearchView - shown when running as administrator. Launches
  EverythingClone.exe (src/, this repo's separate C++/Win32 Everything
  clone - raw NTFS MFT index + live USN journal watching) with the
  --embed-parent-hwnd flag added for exactly this purpose, and hosts its
  window as a real WS_CHILD window inside this page via core/
  everything_embed.py's ctypes helpers - not a second separate window.
  EverythingClone itself requires admin rights (raw volume handles), and
  Windows blocks/degrades window-parenting across different integrity
  levels (UIPI), which is why this whole page is elevation-gated rather
  than just launching the child unconditionally.

  _FallbackSearchView - shown otherwise: today's original from-scratch
  Python search (core/file_search.py's build_index walks every fixed
  drive via plain os.walk, then the in-memory index is searched instantly
  as you type - no raw MFT index, no persistence, no live updates; see
  memory_master/README.md), plus a banner offering to restart the whole
  app elevated (core/elevation.py's request_admin_restart, generalized
  from its original force-delete-only use so the relaunched instance can
  pass --start-page search and land right back here). Never removed: the
  Search page should never be a dead "you need admin" screen with nothing
  usable, and this is already-working, already-tested code.

Delete/force-delete on the fallback view reuses the same hardened
core/force_delete.py pipeline as the Cleanup page's drop zone (single
selection reuses that exact analyze/confirm/execute flow, multi-selection
uses a batch flow with one confirmation and no kill-list, mirroring
ui/dialogs/duplicate_image_manager.py's existing batch-delete precedent);
the embedded view's own force-delete (EverythingClone's native context
menu) is a separate, independent implementation on the C++ side.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

import psutil
from PyQt5.QtCore import QThread, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.elevation import is_running_as_admin, request_admin_restart
from core.file_search import FileEntry, build_index
from core.file_search import search as search_entries
from core.force_delete import AnalyzeResult, ExecuteOptions, ExecuteResult, execute
from core.formatting import format_bytes, format_datetime_kr
from core.image_scanner import is_image_file
from core.resource_path import resource_path
from core.trash import send_to_trash
from core.workers import AnalyzeWorker, ExecuteWorker
from ui.dialogs.confirm_force_delete import ConfirmForceDeleteDialog

if sys.platform == "win32":
    from core.icons import get_icon_for_path
else:
    def get_icon_for_path(path: str, is_dir: bool = False):
        return None

if sys.platform == "win32":
    from core.everything_embed import build_launch_args, find_child_hwnd, request_graceful_close, resize_child
else:
    def build_launch_args(exe_path: str, parent_hwnd: int, width: int, height: int) -> List[str]:
        return []

    def find_child_hwnd(parent_hwnd: int) -> Optional[int]:
        return None

    def resize_child(child_hwnd: int, width: int, height: int) -> None:
        return None

    def request_graceful_close(child_hwnd: int) -> None:
        return None

_COL_NAME = 0
_COL_PATH = 1
_COL_SIZE = 2
_COL_MODIFIED = 3

_MAX_DISPLAYED_RESULTS = 2000
_SEARCH_DEBOUNCE_MS = 200
_PREVIEW_PLACEHOLDER_TEXT = "이미지를 선택하면\n미리보기가 표시됩니다"

_BATCH_FORCE_DELETE_OPTIONS = ExecuteOptions(
    kill_locking_processes=False,
    take_ownership_on_failure=True,
    secure_shred=False,
    reboot_delete_fallback=True,
)

_EMBED_POLL_INTERVAL_MS = 100
_EMBED_POLL_BUDGET_MS = 10000
_EMBED_MIN_WIDTH = 200
_EMBED_MIN_HEIGHT = 150


def _fixed_drive_roots() -> List[str]:
    """Every currently-mounted drive except optical media (an empty
    CD/DVD drive can hang or error on enumeration, and isn't useful to
    index anyway) - this is what "그냥 모든 파일 폴더 보여주는 식" (just show
    everything) resolves to: no folder picker, whole-machine coverage.
    """
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception:
        return []
    roots = []
    for part in partitions:
        opts = part.opts.split(",") if part.opts else []
        if "cdrom" in opts:
            continue
        roots.append(part.mountpoint)
    return roots


class _ResultsTable(QTableWidget):
    """Adds Del-key support on top of QTableWidget - there is no existing
    precedent for this anywhere else in the app, so it's kept local rather
    than added as a generic shared widget for a single consumer.
    """

    deleteRequested = pyqtSignal()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Delete:
            self.deleteRequested.emit()
        else:
            super().keyPressEvent(event)


class _IndexWorker(QThread):
    progress = pyqtSignal(int, int)
    resultReady = pyqtSignal(list)  # List[FileEntry]

    def __init__(self, roots: List[str], parent=None):
        super().__init__(parent)
        self._roots = roots
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        entries = build_index(
            self._roots,
            on_progress=lambda i, n: self.progress.emit(i, n),
            should_cancel=lambda: self._cancel_requested,
            compute_total=False,  # a whole-drive pre-count would double an already-long walk
        )
        self.resultReady.emit(entries)


class _TrashDeleteWorker(QThread):
    resultReady = pyqtSignal(list)  # List[Tuple[str, bool]]

    def __init__(self, paths: List[str], parent=None):
        super().__init__(parent)
        self._paths = paths
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        results = []
        for path in self._paths:
            if self._cancel_requested:
                break
            results.append((path, send_to_trash(path).ok))
        self.resultReady.emit(results)


class _BatchForceDeleteWorker(QThread):
    resultReady = pyqtSignal(list)  # List[Tuple[str, bool]]

    def __init__(self, paths: List[str], parent=None):
        super().__init__(parent)
        self._paths = paths
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        results = []
        for path in self._paths:
            if self._cancel_requested:
                break
            result = execute(path, kill_pids=[], options=_BATCH_FORCE_DELETE_OPTIONS)
            results.append((path, result.failed_count == 0))
        self.resultReady.emit(results)


class _FallbackSearchView(QWidget):
    """Today's original from-scratch Python search - unchanged except for
    the admin-restart banner added at the top. See the module docstring
    for why this stays instead of being replaced by the embedded view.
    """

    restartAsAdminRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._index: List[FileEntry] = []
        self._by_path: Dict[str, FileEntry] = {}
        self._worker: Optional[QThread] = None  # one at a time, mirrors CleanupPage
        self._auto_indexed = False  # first showEvent kicks off indexing, not __init__

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        root.addWidget(self._build_admin_banner())
        root.addWidget(self._build_index_section())
        root.addWidget(self._build_search_section(), 1)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(_SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._apply_search)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._auto_indexed:
            self._auto_indexed = True
            self._start_indexing()

    # -- construction -------------------------------------------------------

    def _build_admin_banner(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QHBoxLayout(frame)
        label = QLabel(
            "빠른 검색(EverythingClone)을 쓰려면 관리자 권한이 필요합니다. 지금은 느린 기본 검색을 사용 중입니다."
        )
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        restart_btn = QPushButton("관리자 권한으로 다시 시작")
        restart_btn.setProperty("role", "primary")
        restart_btn.clicked.connect(self.restartAsAdminRequested.emit)
        layout.addWidget(restart_btn)
        return frame

    def _build_index_section(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.addWidget(_section_title("인덱스"))

        row = QHBoxLayout()
        self._reindex_btn = QPushButton("다시 인덱싱")
        self._reindex_btn.setProperty("role", "primary")
        self._reindex_btn.clicked.connect(self._start_indexing)
        row.addWidget(self._reindex_btn)
        row.addStretch(1)
        self._index_status_label = QLabel("")
        self._index_status_label.setStyleSheet("color: #8c92a4; font-size: 12px;")
        row.addWidget(self._index_status_label)
        layout.addLayout(row)
        return frame

    def _build_search_section(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.addWidget(_section_title("검색"))

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("인덱싱 중...")
        self._search_box.setEnabled(False)
        self._search_box.textChanged.connect(self._on_search_text_changed)
        layout.addWidget(self._search_box)

        self._results_status_label = QLabel("")
        self._results_status_label.setStyleSheet("color: #8c92a4; font-size: 12px;")
        layout.addWidget(self._results_status_label)

        body = QHBoxLayout()
        self._results_table = self._build_results_table()
        body.addWidget(self._results_table, 2)
        self._preview_label = self._build_preview_label()
        body.addWidget(self._preview_label, 1)
        layout.addLayout(body, 1)
        return frame

    def _build_results_table(self) -> _ResultsTable:
        table = _ResultsTable(0, 4)
        table.setHorizontalHeaderLabels(["이름", "경로", "크기", "수정한 날짜"])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        header = table.horizontalHeader()
        header.setSectionResizeMode(_COL_NAME, QHeaderView.Interactive)
        header.setSectionResizeMode(_COL_PATH, QHeaderView.Stretch)
        header.setSectionResizeMode(_COL_SIZE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_MODIFIED, QHeaderView.ResizeToContents)
        table.setColumnWidth(_COL_NAME, 220)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(self._show_context_menu)
        table.deleteRequested.connect(self._on_delete_requested)
        table.itemSelectionChanged.connect(self._update_preview)
        return table

    @staticmethod
    def _build_preview_label() -> QLabel:
        label = QLabel(_PREVIEW_PLACEHOLDER_TEXT)
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumSize(280, 280)
        label.setWordWrap(True)
        label.setStyleSheet("background-color: #0f131d; border-radius: 4px; color: #8c92a4;")
        return label

    # -- indexing ---------------------------------------------------------

    def _start_indexing(self) -> None:
        roots = _fixed_drive_roots()
        if not roots:
            QMessageBox.warning(self, "드라이브 없음", "검색 가능한 드라이브를 찾지 못했습니다.")
            return
        self._reindex_btn.setEnabled(False)
        self._search_box.setEnabled(False)
        self._results_table.setRowCount(0)
        self._index_status_label.setText("인덱싱 중... (전체 드라이브, 다소 시간이 걸릴 수 있습니다)")

        worker = _IndexWorker(roots, self)
        worker.progress.connect(self._on_index_progress)
        worker.resultReady.connect(self._on_index_ready)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_index_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._index_status_label.setText(f"인덱싱 중... {done}/{total}")
        else:
            self._index_status_label.setText(f"인덱싱 중... {done}개 처리됨")

    def _on_index_ready(self, entries: List[FileEntry]) -> None:
        self._reindex_btn.setEnabled(True)
        self._index = entries
        self._by_path = {e.path: e for e in entries}
        self._index_status_label.setText(f"{len(entries)}개 항목 인덱싱됨")
        self._search_box.setEnabled(True)
        self._search_box.setPlaceholderText("검색어 입력...")
        self._apply_search()

    # -- search -------------------------------------------------------------

    def _on_search_text_changed(self, _text: str) -> None:
        self._search_timer.start()

    def _apply_search(self) -> None:
        results = search_entries(self._index, self._search_box.text()) if self._index else []
        self._populate_results(results)

    def _populate_results(self, results: List[FileEntry]) -> None:
        total = len(results)
        capped = results[:_MAX_DISPLAYED_RESULTS]
        table = self._results_table
        table.setRowCount(len(capped))
        for row, entry in enumerate(capped):
            name_item = QTableWidgetItem(entry.name)
            name_item.setData(Qt.UserRole, entry.path)
            pixmap = get_icon_for_path(entry.path, entry.is_dir)
            if pixmap is not None:
                name_item.setIcon(QIcon(pixmap))
            table.setItem(row, _COL_NAME, name_item)
            table.setItem(row, _COL_PATH, QTableWidgetItem(entry.path))
            size_text = "-" if entry.is_dir else format_bytes(entry.size_bytes)
            table.setItem(row, _COL_SIZE, QTableWidgetItem(size_text))
            table.setItem(row, _COL_MODIFIED, QTableWidgetItem(format_datetime_kr(entry.modified_at)))

        if total > _MAX_DISPLAYED_RESULTS:
            self._results_status_label.setText(f"{_MAX_DISPLAYED_RESULTS}개 표시 중 (전체 {total}개 일치)")
        else:
            self._results_status_label.setText(f"{total}개 일치")

    def _selected_entries(self) -> List[FileEntry]:
        rows = {idx.row() for idx in self._results_table.selectedIndexes()}
        entries = []
        for row in sorted(rows):
            item = self._results_table.item(row, _COL_NAME)
            if item is None:
                continue
            entry = self._by_path.get(item.data(Qt.UserRole))
            if entry is not None:
                entries.append(entry)
        return entries

    # -- image preview --------------------------------------------------

    def _update_preview(self) -> None:
        entries = self._selected_entries()
        if len(entries) == 1 and not entries[0].is_dir and is_image_file(entries[0].path):
            pixmap = QPixmap(entries[0].path)
            if not pixmap.isNull():
                box = self._preview_label.size()
                if box.width() < 10 or box.height() < 10:
                    # Layout may not have settled yet (e.g. right after the
                    # page's very first show) - the label's own minimum
                    # size is a reliable floor since it was set explicitly,
                    # not derived from a layout pass that may not have run.
                    box = self._preview_label.minimumSize()
                self._preview_label.setPixmap(pixmap.scaled(box, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        self._preview_label.setPixmap(QPixmap())
        self._preview_label.setText(_PREVIEW_PLACEHOLDER_TEXT)

    # -- Del key / context menu --------------------------------------------

    def _on_delete_requested(self) -> None:
        entries = self._selected_entries()
        if entries:
            self._trash_delete(entries)

    def _build_context_menu(self, entries: List[FileEntry]) -> QMenu:
        menu = QMenu(self)
        self._ctx_open_action = menu.addAction("열기")
        self._ctx_open_folder_action = menu.addAction("폴더 열기")
        menu.addSeparator()
        self._ctx_trash_action = menu.addAction("삭제 (휴지통으로 이동)")
        self._ctx_force_delete_action = menu.addAction("강제 삭제")
        return menu

    def _show_context_menu(self, pos) -> None:
        row = self._results_table.rowAt(pos.y())
        if row < 0:
            return
        if not self._results_table.item(row, _COL_NAME).isSelected():
            self._results_table.selectRow(row)

        entries = self._selected_entries()
        if not entries:
            return

        menu = self._build_context_menu(entries)
        action = menu.exec_(self._results_table.viewport().mapToGlobal(pos))
        if action == self._ctx_open_action:
            for entry in entries:
                _open_path(entry.path)
        elif action == self._ctx_open_folder_action:
            folders = {entry.path if entry.is_dir else os.path.dirname(entry.path) for entry in entries}
            for folder in folders:
                _open_path(folder)
        elif action == self._ctx_trash_action:
            self._trash_delete(entries)
        elif action == self._ctx_force_delete_action:
            self._force_delete(entries)

    # -- normal delete (Recycle Bin) ----------------------------------------

    def _trash_delete(self, entries: List[FileEntry]) -> None:
        reply = QMessageBox.question(
            self,
            "휴지통으로 이동",
            f"{len(entries)}개 항목을 휴지통으로 이동하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._set_busy(True)
        worker = _TrashDeleteWorker([e.path for e in entries], self)
        worker.resultReady.connect(self._on_trash_delete_done)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_trash_delete_done(self, results: List[Tuple[str, bool]]) -> None:
        self._set_busy(False)
        failed = [path for path, ok in results if not ok]
        if failed:
            QMessageBox.warning(self, "일부 이동 실패", "\n".join(failed))
        self._remove_deleted_paths({path for path, ok in results if ok})

    # -- force delete: single (full confirm flow) / batch --------------------

    def _force_delete(self, entries: List[FileEntry]) -> None:
        if len(entries) == 1:
            self._force_delete_single(entries[0].path)
        else:
            self._force_delete_batch(entries)

    def _force_delete_single(self, path: str) -> None:
        self._set_busy(True)
        worker = AnalyzeWorker(path, self)
        worker.resultReady.connect(self._on_analyze_ready)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_analyze_ready(self, result: AnalyzeResult) -> None:
        self._set_busy(False)
        if result.blocked:
            QMessageBox.warning(self, "삭제할 수 없음", result.block_reason)
            return
        dialog = ConfirmForceDeleteDialog(result, self)
        if dialog.exec_() == QDialog.Accepted:
            self._execute_single_delete(result.path, dialog.kill_pids, dialog.build_options())

    def _execute_single_delete(self, path: str, kill_pids: List[int], options: ExecuteOptions) -> None:
        self._set_busy(True)
        worker = ExecuteWorker(path, kill_pids, options, self)
        worker.resultReady.connect(lambda result: self._on_single_execute_done(path, result))
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_single_execute_done(self, path: str, result: ExecuteResult) -> None:
        self._set_busy(False)
        QMessageBox.information(self, "삭제 완료", f"{result.deleted_count}개 삭제, {result.failed_count}개 실패")
        if result.deleted_count > 0:
            self._remove_deleted_paths({path})

    def _force_delete_batch(self, entries: List[FileEntry]) -> None:
        reply = QMessageBox.question(
            self,
            "강제 삭제 확인",
            f"{len(entries)}개 항목을 영구적으로 삭제하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._set_busy(True)
        worker = _BatchForceDeleteWorker([e.path for e in entries], self)
        worker.resultReady.connect(self._on_batch_force_delete_done)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_batch_force_delete_done(self, results: List[Tuple[str, bool]]) -> None:
        self._set_busy(False)
        failed = [path for path, ok in results if not ok]
        if failed:
            QMessageBox.warning(self, "일부 삭제 실패", "\n".join(failed))
        self._remove_deleted_paths({path for path, ok in results if ok})

    # -- shared helpers -------------------------------------------------

    def _remove_deleted_paths(self, paths) -> None:
        """Purges successfully-deleted paths from the in-memory index (not
        just the visible results table) so a later search doesn't resurface
        something that no longer exists. A deleted directory's whole
        subtree needs purging too, hence the prefix check - not just an
        exact-path match. The trailing separator on each prefix is what
        stops "Downloads" from matching "Downloads2" (same boundary bug
        path_guard.py's is_within_or_equal fixes, applied here just for
        index-list hygiene rather than a safety check).
        """
        if not paths:
            return
        prefixes = tuple(os.path.join(p, "") for p in paths)
        self._index = [e for e in self._index if e.path not in paths and not e.path.startswith(prefixes)]
        self._by_path = {e.path: e for e in self._index}
        self._apply_search()

    def _set_busy(self, busy: bool) -> None:
        self._reindex_btn.setEnabled(not busy)
        self._results_table.setEnabled(not busy)

    def stop(self) -> None:
        """Cancels and waits for any in-flight worker before the app can
        safely close - same reasoning as CleanupPage.stop() (destroying a
        live QThread is undefined behavior in Qt). Matters even more here
        than most pages: a whole-drive index is the single largest, longest
        background job in this app, so a user closing the app mid-index is
        an expected, not edge-case, path through this method.

        The RuntimeError guard covers a real, confirmed-reachable case: a
        worker that already finished (every worker here connects
        finished -> deleteLater) has its underlying Qt object destroyed the
        next time the event loop runs - which, in a long-running real app,
        has near-certainly already happened by the time the user gets
        around to closing it. self._worker is then a dangling wrapper
        around a deleted object; touching it (getattr included - sip
        raises RuntimeError, not AttributeError, so getattr's own default
        doesn't catch it) raises instead of behaving like None. There's
        nothing to cancel or wait for in that case anyway, so treating it
        as a no-op is correct, not just a workaround.
        """
        if self._worker is None:
            return
        try:
            cancel = getattr(self._worker, "cancel", None)
            if callable(cancel):
                cancel()
            self._worker.wait(10000)
        except RuntimeError:
            pass


class _EmbeddedSearchView(QWidget):
    """Hosts EverythingClone.exe's own window as a real child window. Only
    ever shown (and so only ever launches anything) when this process is
    already elevated - see the module docstring and SearchPage below.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: Optional[subprocess.Popen] = None
        self._child_hwnd: Optional[int] = None
        self._launch_attempted = False  # first showEvent launches, not __init__
        self._poll_elapsed_ms = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._status_label = QLabel("빠른 검색을 준비하는 중...")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #8c92a4; padding: 24px;")
        layout.addWidget(self._status_label)

        # The container that EverythingClone.exe's window gets parented
        # into. WA_NativeWindow forces Qt to back it with a real native
        # window (HWND on Windows) up front, rather than potentially
        # deferring that - winId() below needs a real, stable handle.
        self._container = QWidget(self)
        self._container.setAttribute(Qt.WA_NativeWindow, True)
        layout.addWidget(self._container, 1)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_EMBED_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_for_child)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._launch_attempted:
            self._launch_attempted = True
            # Deferred a tick so the container has had a layout pass and
            # width()/height() reflect its real, settled size - matches
            # _FallbackSearchView._update_preview's identical reasoning for
            # a widget's size right after its very first show.
            QTimer.singleShot(0, self._launch)

    # -- launch + discovery -------------------------------------------------

    def _launch(self) -> None:
        exe_path = resource_path("EverythingClone.exe")
        if not os.path.exists(exe_path):
            self._show_error(f"EverythingClone.exe를 찾을 수 없습니다:\n{exe_path}")
            return

        width = max(self._container.width(), _EMBED_MIN_WIDTH)
        height = max(self._container.height(), _EMBED_MIN_HEIGHT)
        parent_hwnd = int(self._container.winId())
        args = build_launch_args(exe_path, parent_hwnd, width, height)
        try:
            self._process = subprocess.Popen(args)
        except OSError as e:
            self._show_error(f"EverythingClone.exe를 실행하지 못했습니다:\n{e}")
            return

        self._poll_elapsed_ms = 0
        self._poll_timer.start()

    def _poll_for_child(self) -> None:
        if self._process is not None and self._process.poll() is not None:
            self._poll_timer.stop()
            self._show_error("EverythingClone.exe가 예기치 않게 종료되었습니다.")
            return

        hwnd = find_child_hwnd(int(self._container.winId()))
        if hwnd is not None:
            self._poll_timer.stop()
            self._child_hwnd = hwnd
            self._status_label.setVisible(False)
            resize_child(hwnd, self._container.width(), self._container.height())
            return

        self._poll_elapsed_ms += _EMBED_POLL_INTERVAL_MS
        if self._poll_elapsed_ms >= _EMBED_POLL_BUDGET_MS:
            self._poll_timer.stop()
            self._show_error("빠른 검색 창을 여는 데 시간이 너무 오래 걸립니다.")

    def _show_error(self, message: str) -> None:
        self._status_label.setText(message)
        self._status_label.setVisible(True)

    # -- resize forwarding ----------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._child_hwnd is not None:
            resize_child(self._child_hwnd, self._container.width(), self._container.height())

    # -- teardown -------------------------------------------------------

    def _terminate_process(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._process.kill()

    def stop(self) -> None:
        """Closes the embedded EverythingClone process, if one was ever
        launched. Prefers a graceful WM_CLOSE (request_graceful_close) over
        a hard terminate()/kill() whenever a child window was actually
        discovered - see core/everything_embed.py's request_graceful_close
        docstring for why a hard kill would regress "fast restart from a
        saved index snapshot" into "full MFT rescan every launch".
        """
        if self._process is None:
            return
        self._poll_timer.stop()
        if self._child_hwnd is not None:
            request_graceful_close(self._child_hwnd)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._terminate_process()
        else:
            self._terminate_process()
        self._process = None
        self._child_hwnd = None


class SearchPage(QWidget):
    """Thin coordinator: picks _EmbeddedSearchView or _FallbackSearchView
    once, based on this process's elevation, and stays out of the way -
    same class name/constructor signature as before this split, so nothing
    outside this file needed to change to keep using it.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget(self)
        layout.addWidget(self._stack)

        self._fallback_view = _FallbackSearchView(self)
        self._fallback_view.restartAsAdminRequested.connect(self._on_restart_as_admin)
        fallback_index = self._stack.addWidget(self._fallback_view)

        self._embedded_view = _EmbeddedSearchView(self)
        embedded_index = self._stack.addWidget(self._embedded_view)

        self._stack.setCurrentIndex(embedded_index if is_running_as_admin() else fallback_index)

        # Required, not optional: the embedded view can own a running,
        # *admin-elevated* child process. MainWindow.shutdown() is only
        # ever called by the offscreen smoke check, not a real run (see its
        # own docstring) - a real app exit goes through the tray's quit
        # action calling QApplication.quit() directly, which fires
        # aboutToQuit. Without connecting to it here, every ordinary quit
        # would leak that process in the background.
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    def _on_restart_as_admin(self) -> None:
        if request_admin_restart(extra_args=["--start-page", "search"]):
            app = QApplication.instance()
            if app is not None:
                app.quit()
        else:
            QMessageBox.warning(
                self,
                "다시 시작 실패",
                "관리자 권한으로 다시 시작하지 못했습니다. 직접 관리자 권한으로 실행해 주세요.",
            )

    def stop(self) -> None:
        self._fallback_view.stop()
        self._embedded_view.stop()


def _open_path(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - user-triggered, not a scripted action
    else:
        subprocess.run(["xdg-open", path], check=False)


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 13px; font-weight: 600; color: #8c92a4;")
    return label
