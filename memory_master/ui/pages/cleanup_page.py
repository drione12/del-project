"""Cleanup page - duplicate-file finder, duplicate/similar-image finder,
and a drag-drop force-delete zone, all funneling into the same hardened
core/force_delete.py pipeline (see the plan doc's Cleanup page section).
The drop zone is the page's one path through the *full* Analyze -> confirm
-> Execute flow with kill-list confirmation; the two scanners' batch
actions live inside DuplicateImageManagerDialog (images) or this page's
own simple list (files) and intentionally skip that confirmation step for
the common no-conflict case - see duplicate_image_manager.py's docstring.
"""
from __future__ import annotations

from typing import List

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.duplicates import DuplicateGroup, find_duplicate_files
from core.force_delete import AnalyzeResult, ExecuteOptions, ExecuteResult, analyze
from core.formatting import format_bytes
from core.image_scanner import ImagePair, find_near_duplicate_images, find_similar_images_by_features
from core.workers import ExecuteWorker
from ui.dialogs.confirm_force_delete import ConfirmForceDeleteDialog
from ui.dialogs.duplicate_image_manager import DuplicateImageManagerDialog
from widgets.drop_zone import DropZoneWidget


class _DuplicateFilesWorker(QThread):
    progress = pyqtSignal(int, int)
    resultReady = pyqtSignal(list)  # List[DuplicateGroup]

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self._root = root
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        groups = find_duplicate_files(
            self._root,
            on_progress=lambda i, n: self.progress.emit(i, n),
            should_cancel=lambda: self._cancel_requested,
        )
        self.resultReady.emit(groups)


class _ImageScanWorker(QThread):
    progress = pyqtSignal(int, int)
    resultReady = pyqtSignal(list)  # List[ImagePair]

    def __init__(self, roots: List[str], parent=None):
        super().__init__(parent)
        self._roots = roots
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        def should_cancel() -> bool:
            return self._cancel_requested

        pairs = find_near_duplicate_images(
            self._roots, on_progress=lambda i, n: self.progress.emit(i, n), should_cancel=should_cancel
        )
        seen = {(p.path_a, p.path_b) for p in pairs}
        if not self._cancel_requested:
            feature_pairs = find_similar_images_by_features(
                self._roots, on_progress=lambda i, n: self.progress.emit(i, n), should_cancel=should_cancel
            )
            for pair in feature_pairs:
                if (pair.path_a, pair.path_b) not in seen:
                    pairs.append(pair)
        self.resultReady.emit(pairs)


class _AnalyzeWorker(QThread):
    resultReady = pyqtSignal(object)  # AnalyzeResult

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self) -> None:
        self.resultReady.emit(analyze(self._path))


class CleanupPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._image_folders: List[str] = []

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

        root.addWidget(self._build_drop_zone())
        root.addWidget(self._build_duplicate_files_section())
        root.addWidget(self._build_duplicate_images_section())
        root.addWidget(self._build_progress_row())
        root.addStretch(1)

        self._worker = None  # keeps the active QThread alive; one at a time

    def _build_drop_zone(self) -> DropZoneWidget:
        zone = DropZoneWidget()
        zone.pathDropped.connect(self._analyze_dropped_path)
        return zone

    def _build_duplicate_files_section(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.addWidget(_section_title("중복 파일 찾기"))
        row = QHBoxLayout()
        browse_btn = QPushButton("폴더 선택...")
        browse_btn.clicked.connect(self._browse_for_duplicate_files)
        row.addWidget(browse_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self._duplicate_files_list = QListWidget()
        self._duplicate_files_list.setMaximumHeight(160)
        layout.addWidget(self._duplicate_files_list)
        return frame

    def _build_duplicate_images_section(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.addWidget(_section_title("중복/유사 이미지 찾기"))

        self._image_folders_widget = QListWidget()
        self._image_folders_widget.setMaximumHeight(100)
        layout.addWidget(self._image_folders_widget)

        row = QHBoxLayout()
        add_btn = QPushButton("폴더 추가...")
        add_btn.clicked.connect(self._add_image_folder)
        remove_btn = QPushButton("선택 항목 제거")
        remove_btn.clicked.connect(self._remove_image_folder)
        scan_btn = QPushButton("검색 시작")
        scan_btn.setProperty("role", "primary")
        scan_btn.clicked.connect(self._start_image_scan)
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addWidget(scan_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    def _build_progress_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        self._progress_label = QLabel("")
        self._progress_label.setStyleSheet("color: #8c92a4; font-size: 12px;")
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._stop_btn = QPushButton("중지")
        self._stop_btn.setVisible(False)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        layout.addWidget(self._progress_label)
        layout.addWidget(self._progress_bar, 1)
        layout.addWidget(self._stop_btn)
        return widget

    # -- duplicate files ------------------------------------------------

    def _browse_for_duplicate_files(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "폴더 선택")
        if not folder:
            return
        self._duplicate_files_list.clear()
        self._start_progress("중복 파일 검색 중...")

        worker = _DuplicateFilesWorker(folder, self)
        worker.progress.connect(self._on_scan_progress)
        worker.resultReady.connect(self._on_duplicate_files_ready)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_duplicate_files_ready(self, groups: List[DuplicateGroup]) -> None:
        self._end_progress()
        self._duplicate_files_list.clear()
        if not groups:
            self._duplicate_files_list.addItem("중복 파일을 찾지 못했습니다.")
            return
        for group in groups:
            self._duplicate_files_list.addItem(
                f"{format_bytes(group.size_bytes)} x {len(group.paths)}개: {group.paths[0]}"
            )

    # -- duplicate images -------------------------------------------------

    def _add_image_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "폴더 선택")
        if folder:
            self._apply_add_image_folder(folder)

    def _apply_add_image_folder(self, folder: str) -> None:
        if folder and folder not in self._image_folders:
            self._image_folders.append(folder)
            self._image_folders_widget.addItem(folder)

    def _remove_image_folder(self) -> None:
        for item in self._image_folders_widget.selectedItems():
            if item.text() in self._image_folders:
                self._image_folders.remove(item.text())
            self._image_folders_widget.takeItem(self._image_folders_widget.row(item))

    def _start_image_scan(self) -> None:
        if not self._image_folders:
            QMessageBox.information(self, "폴더 없음", "검색할 폴더를 먼저 추가하세요.")
            return
        self._start_progress("이미지 스캔 중...")

        worker = _ImageScanWorker(list(self._image_folders), self)
        worker.progress.connect(self._on_scan_progress)
        worker.resultReady.connect(self._on_image_scan_ready)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_image_scan_ready(self, pairs: List[ImagePair]) -> None:
        self._end_progress()
        if not pairs:
            QMessageBox.information(self, "이미지 스캔 완료", "중복되거나 유사한 이미지를 찾지 못했습니다.")
            return
        DuplicateImageManagerDialog(pairs, self).exec_()

    # -- drop zone: the full analyze -> confirm -> execute flow -----------

    def _analyze_dropped_path(self, path: str) -> None:
        self._start_progress("분석 중...", cancellable=False)
        worker = _AnalyzeWorker(path, self)
        worker.resultReady.connect(self._on_analyze_ready)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_analyze_ready(self, result: AnalyzeResult) -> None:
        self._end_progress()
        if result.blocked:
            QMessageBox.warning(self, "삭제할 수 없음", result.block_reason)
            return
        dialog = ConfirmForceDeleteDialog(result, self)
        if dialog.exec_() == QDialog.Accepted:
            self._execute_delete(result.path, dialog.kill_pids, dialog.build_options())

    def _execute_delete(self, path: str, kill_pids: List[int], options: ExecuteOptions) -> None:
        self._start_progress("삭제 중...")
        worker = ExecuteWorker(path, kill_pids, options, self)
        worker.resultReady.connect(self._on_execute_done)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_execute_done(self, result: ExecuteResult) -> None:
        self._end_progress()
        QMessageBox.information(self, "삭제 완료", f"{result.deleted_count}개 삭제, {result.failed_count}개 실패")

    # -- shared progress/cancel helpers -------------------------------------

    def _start_progress(self, label: str, cancellable: bool = True) -> None:
        self._progress_label.setText(label)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._stop_btn.setVisible(cancellable)
        self._stop_btn.setEnabled(True)

    def _end_progress(self) -> None:
        self._progress_label.setText("")
        self._progress_bar.setVisible(False)
        self._stop_btn.setVisible(False)

    def _on_scan_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress_bar.setValue(int(done / total * 100))
        self._progress_label.setText(f"{done}/{total}")

    def _on_stop_clicked(self) -> None:
        cancel = getattr(self._worker, "cancel", None)
        if callable(cancel):
            cancel()
            self._progress_label.setText("중지 중...")
            self._stop_btn.setEnabled(False)

    def stop(self) -> None:
        """Cancels and waits for any in-flight scan/execute worker before
        the app can safely close - destroying a QThread object while its
        run() is still executing is undefined behavior in Qt, the same
        class of crash already fixed for StartupManagerPage (see its
        stop() docstring). Requests cancellation before waiting, since
        none of these operations (a big folder scan, a large delete) have
        a fixed upper bound the way StartupManagerPage's schtasks call
        does - but every cancellable worker here checks should_cancel()
        on every file/comparison, so a cancel request should be honored
        within a moment regardless of how large the overall job is.
        """
        if self._worker is None:
            return
        cancel = getattr(self._worker, "cancel", None)
        if callable(cancel):
            cancel()
        self._worker.wait(10000)


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 13px; font-weight: 600; color: #8c92a4;")
    return label
