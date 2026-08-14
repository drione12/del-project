"""Review dialog for the duplicate/similar-image scanner - a table of
matched pairs (one checkbox per side, since a pair doesn't imply which
side is the one to remove) on the left, a live preview of the selected
row's two images on the right, a "priority folder" auto-selector (check
whichever side of every pair is NOT in the chosen folder, in one click),
and a right-click menu to open a file/folder or drop a pair that isn't
actually a duplicate. Both destructive actions (force-delete, quarantine)
run on a background thread and act on every currently-checked file.

Batch actions here deliberately skip the kill-list confirmation step
core/force_delete.py's single-path flow uses (ConfirmForceDeleteDialog) -
a locked file just fails to delete and is reported, rather than silently
killing whatever process is using it. That keeps this dialog simple for
what should be the common case (plain files, nothing has them open); the
manual single-path drop-zone flow is where kill-list confirmation happens.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Tuple

from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.force_delete import ExecuteOptions, execute
from core.image_scanner import ImagePair
from core.quarantine import move_to_quarantine

_COL_CHECK_A = 0
_COL_PATH_A = 1
_COL_CHECK_B = 2
_COL_PATH_B = 3
_COL_SIMILARITY = 4

_BASE_PREVIEW_SIZE = 240
_ZOOM_MIN_PERCENT = 50
_ZOOM_MAX_PERCENT = 300
_ZOOM_DEFAULT_PERCENT = 100

_BATCH_DELETE_OPTIONS = ExecuteOptions(
    kill_locking_processes=False,
    take_ownership_on_failure=True,
    secure_shred=False,
    reboot_delete_fallback=True,
)


def _open_path(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - user-triggered, not a scripted action
    else:
        subprocess.run(["xdg-open", path], check=False)


class _BatchDeleteWorker(QThread):
    resultReady = pyqtSignal(list)  # List[Tuple[str, bool]]

    def __init__(self, paths: List[str], parent=None):
        super().__init__(parent)
        self._paths = paths

    def run(self) -> None:
        results = []
        for path in self._paths:
            result = execute(path, kill_pids=[], options=_BATCH_DELETE_OPTIONS)
            results.append((path, result.failed_count == 0))
        self.resultReady.emit(results)


class _BatchQuarantineWorker(QThread):
    resultReady = pyqtSignal(list)  # List[Tuple[str, bool]]

    def __init__(self, paths: List[str], parent=None):
        super().__init__(parent)
        self._paths = paths

    def run(self) -> None:
        results = [(path, move_to_quarantine(path).ok) for path in self._paths]
        self.resultReady.emit(results)


class DuplicateImageManagerDialog(QDialog):
    def __init__(self, pairs: List[ImagePair], parent=None):
        super().__init__(parent)
        self._pairs = list(pairs)
        self._zoom_percent = _ZOOM_DEFAULT_PERCENT
        self.setWindowTitle("중복/유사 이미지 검토")
        self.resize(900, 560)

        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("우선 유지 폴더:"))
        self._priority_combo = QComboBox()
        self._priority_combo.addItem("선택 안 함")
        for folder in sorted(self._folders_involved()):
            self._priority_combo.addItem(folder)
        controls.addWidget(self._priority_combo, 1)
        auto_select_btn = QPushButton("자동 선택")
        auto_select_btn.clicked.connect(self._auto_select_by_priority)
        controls.addWidget(auto_select_btn)
        layout.addLayout(controls)

        body = QHBoxLayout()
        self._table = QTableWidget(len(self._pairs), 5)
        self._table.setHorizontalHeaderLabels(["삭제", "파일 A", "삭제", "파일 B", "유사도"])
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.itemSelectionChanged.connect(self._update_preview)
        self._populate_table()
        body.addWidget(self._table, 2)

        preview_panel = QVBoxLayout()
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("확대/축소:"))
        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setRange(_ZOOM_MIN_PERCENT, _ZOOM_MAX_PERCENT)
        self._zoom_slider.setValue(_ZOOM_DEFAULT_PERCENT)
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)
        zoom_row.addWidget(self._zoom_slider, 1)
        self._zoom_label = QLabel(f"{_ZOOM_DEFAULT_PERCENT}%")
        self._zoom_label.setFixedWidth(40)
        zoom_row.addWidget(self._zoom_label)
        preview_panel.addLayout(zoom_row)

        self._preview_a = self._make_preview_label()
        self._preview_b = self._make_preview_label()
        preview_panel.addWidget(self._preview_a)
        preview_panel.addWidget(self._preview_b)
        preview_widget = QWidget()
        preview_widget.setLayout(preview_panel)
        body.addWidget(preview_widget, 1)
        layout.addLayout(body)

        actions = QHBoxLayout()
        self._quarantine_btn = QPushButton("격리 폴더로 이동")
        self._quarantine_btn.clicked.connect(self._quarantine_checked)
        self._delete_btn = QPushButton("선택 항목 강제 삭제")
        self._delete_btn.setProperty("role", "danger")
        self._delete_btn.clicked.connect(self._delete_checked)
        actions.addWidget(self._quarantine_btn)
        actions.addWidget(self._delete_btn)
        actions.addStretch(1)
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.reject)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

    @staticmethod
    def _make_preview_label() -> QLabel:
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumSize(_BASE_PREVIEW_SIZE, _BASE_PREVIEW_SIZE)
        label.setStyleSheet("background-color: #0f131d; border-radius: 4px;")
        return label

    def _folders_involved(self):
        folders = set()
        for pair in self._pairs:
            folders.add(os.path.dirname(pair.path_a))
            folders.add(os.path.dirname(pair.path_b))
        return folders

    def _populate_table(self) -> None:
        for row, pair in enumerate(self._pairs):
            self._table.setItem(row, _COL_CHECK_A, self._make_check_item())
            self._table.setItem(row, _COL_PATH_A, QTableWidgetItem(pair.path_a))
            self._table.setItem(row, _COL_CHECK_B, self._make_check_item())
            self._table.setItem(row, _COL_PATH_B, QTableWidgetItem(pair.path_b))
            self._table.setItem(row, _COL_SIMILARITY, QTableWidgetItem(f"{pair.similarity:.0f}%"))

    @staticmethod
    def _make_check_item() -> QTableWidgetItem:
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        item.setCheckState(Qt.Unchecked)
        return item

    def _update_preview(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        pair = self._pairs[rows[0].row()]
        self._set_preview(self._preview_a, pair.path_a)
        self._set_preview(self._preview_b, pair.path_b)

    def _set_preview(self, label: QLabel, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            label.setPixmap(QPixmap())
            label.setText(os.path.basename(path))
        else:
            label.setText("")
            size = int(_BASE_PREVIEW_SIZE * self._zoom_percent / 100)
            label.setPixmap(pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _on_zoom_changed(self, value: int) -> None:
        self._zoom_percent = value
        self._zoom_label.setText(f"{value}%")
        self._update_preview()

    def _auto_select_by_priority(self) -> None:
        if self._priority_combo.currentIndex() == 0:
            return
        priority_folder = self._priority_combo.currentText()
        for row, pair in enumerate(self._pairs):
            in_a = os.path.dirname(pair.path_a) == priority_folder
            in_b = os.path.dirname(pair.path_b) == priority_folder
            # Check (mark for deletion) whichever side is NOT in the
            # priority folder. If both or neither side is in it, leave the
            # row alone - an ambiguous case shouldn't get auto-checked.
            if in_a and not in_b:
                self._table.item(row, _COL_CHECK_B).setCheckState(Qt.Checked)
                self._table.item(row, _COL_CHECK_A).setCheckState(Qt.Unchecked)
            elif in_b and not in_a:
                self._table.item(row, _COL_CHECK_A).setCheckState(Qt.Checked)
                self._table.item(row, _COL_CHECK_B).setCheckState(Qt.Unchecked)

    def _checked_paths(self) -> List[str]:
        paths = set()
        for row, pair in enumerate(self._pairs):
            if self._table.item(row, _COL_CHECK_A).checkState() == Qt.Checked:
                paths.add(pair.path_a)
            if self._table.item(row, _COL_CHECK_B).checkState() == Qt.Checked:
                paths.add(pair.path_b)
        return sorted(paths)

    def _show_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        pair = self._pairs[row]
        menu = QMenu(self)
        open_a = menu.addAction(f"파일 열기: {os.path.basename(pair.path_a)}")
        open_a_folder = menu.addAction("폴더 열기 (A)")
        menu.addSeparator()
        open_b = menu.addAction(f"파일 열기: {os.path.basename(pair.path_b)}")
        open_b_folder = menu.addAction("폴더 열기 (B)")
        menu.addSeparator()
        not_duplicate = menu.addAction("실제로는 중복이 아님 (목록에서 제거)")

        action = menu.exec_(self._table.viewport().mapToGlobal(pos))
        if action == open_a:
            _open_path(pair.path_a)
        elif action == open_a_folder:
            _open_path(os.path.dirname(pair.path_a))
        elif action == open_b:
            _open_path(pair.path_b)
        elif action == open_b_folder:
            _open_path(os.path.dirname(pair.path_b))
        elif action == not_duplicate:
            self._pairs.pop(row)
            self._table.removeRow(row)

    def _quarantine_checked(self) -> None:
        paths = self._checked_paths()
        if not paths:
            return
        self._set_actions_enabled(False)
        worker = _BatchQuarantineWorker(paths, self)
        worker.resultReady.connect(self._on_quarantine_done)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_quarantine_done(self, results: List[Tuple[str, bool]]) -> None:
        self._set_actions_enabled(True)
        moved = sum(1 for _, ok in results if ok)
        QMessageBox.information(self, "격리 완료", f"{moved}/{len(results)}개 항목을 격리 폴더로 이동했습니다.")
        self._remove_checked_rows()

    def _delete_checked(self) -> None:
        paths = self._checked_paths()
        if not paths:
            return
        reply = QMessageBox.question(
            self,
            "강제 삭제 확인",
            f"{len(paths)}개 항목을 영구적으로 삭제하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._set_actions_enabled(False)
        worker = _BatchDeleteWorker(paths, self)
        worker.resultReady.connect(self._on_delete_done)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_delete_done(self, results: List[Tuple[str, bool]]) -> None:
        self._set_actions_enabled(True)
        failed = [path for path, ok in results if not ok]
        if failed:
            QMessageBox.warning(self, "일부 삭제 실패", "\n".join(failed))
        self._remove_checked_rows()

    def _set_actions_enabled(self, enabled: bool) -> None:
        self._quarantine_btn.setEnabled(enabled)
        self._delete_btn.setEnabled(enabled)

    def _remove_checked_rows(self) -> None:
        for row in range(self._table.rowCount() - 1, -1, -1):
            checked_a = self._table.item(row, _COL_CHECK_A).checkState() == Qt.Checked
            checked_b = self._table.item(row, _COL_CHECK_B).checkState() == Qt.Checked
            if checked_a or checked_b:
                self._pairs.pop(row)
                self._table.removeRow(row)
