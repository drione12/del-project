"""File search page ("파일 검색") - an Everything-style search: every fixed
drive is indexed automatically (core/file_search.py's build_index walks
them via plain os.walk, not a raw NTFS MFT index like the real Everything
app or this repo's separate C++ EverythingClone - the two apps share no
code, see memory_master/README.md), then the in-memory index is searched
instantly as you type. Results support shift/ctrl multi-select, Del to
move selected items to the Recycle Bin, a right-click menu adding a
force-delete option that reuses the same hardened core/force_delete.py
pipeline as the Cleanup page's drop zone (single selection reuses that
exact analyze/confirm/execute flow, multi-selection uses a batch flow with
one confirmation and no kill-list, mirroring
ui/dialogs/duplicate_image_manager.py's existing batch-delete precedent),
and a live preview panel for the selected image.
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.file_search import FileEntry, build_index
from core.file_search import search as search_entries
from core.force_delete import AnalyzeResult, ExecuteOptions, ExecuteResult, execute
from core.formatting import format_bytes, format_datetime_kr
from core.image_scanner import is_image_file
from core.trash import send_to_trash
from core.workers import AnalyzeWorker, ExecuteWorker
from ui.dialogs.confirm_force_delete import ConfirmForceDeleteDialog

if sys.platform == "win32":
    from core.icons import get_icon_for_path
else:
    def get_icon_for_path(path: str, is_dir: bool = False):
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


class SearchPage(QWidget):
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


def _open_path(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - user-triggered, not a scripted action
    else:
        subprocess.run(["xdg-open", path], check=False)


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 13px; font-weight: 600; color: #8c92a4;")
    return label
