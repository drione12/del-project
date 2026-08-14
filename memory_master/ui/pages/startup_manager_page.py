"""Startup Manager page - lists what launches at sign-in (registry Run
key + Scheduled Tasks) with a rough boot-impact estimate, and lets a user
add/remove registry Run entries (Scheduled Tasks are view-only here -
removing one needs schtasks /delete plus its own confirmation flow, kept
out of scope for this first pass). Scanning runs on a QThread since
querying Scheduled Tasks shells out to schtasks, which can take a real
moment on a machine with many tasks.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.startup_programs import StartupEntry, add_startup_program, list_startup_entries, remove_startup_program

_COL_NAME = 0
_COL_SOURCE = 1
_COL_IMPACT = 2
_COL_STATUS = 3
_COL_COMMAND = 4

_SOURCE_LABELS = {"registry": "레지스트리", "task_scheduler": "작업 스케줄러"}


class _ScanWorker(QThread):
    resultReady = pyqtSignal(list)  # List[StartupEntry]

    def run(self) -> None:
        self.resultReady.emit(list_startup_entries())


class StartupManagerPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: List[StartupEntry] = []
        self._worker: Optional[_ScanWorker] = None

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

        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_table(), 1)

        self._refresh()

    def _build_toolbar(self) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        refresh_btn = QPushButton("새로고침")
        refresh_btn.clicked.connect(self._refresh)
        add_btn = QPushButton("항목 추가...")
        add_btn.clicked.connect(self._add_entry)
        remove_btn = QPushButton("선택 항목 제거")
        remove_btn.clicked.connect(self._remove_selected)
        row.addWidget(refresh_btn)
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addStretch(1)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #8c92a4; font-size: 12px;")
        row.addWidget(self._status_label)
        return widget

    def _build_table(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["이름", "출처", "부팅 영향", "상태", "명령"])
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table)
        return frame

    def _refresh(self) -> None:
        self._status_label.setText("스캔 중...")
        worker = _ScanWorker(self)
        worker.resultReady.connect(self._on_scan_ready)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_scan_ready(self, entries: List[StartupEntry]) -> None:
        self._entries = entries
        self._status_label.setText(f"{len(entries)}개 항목")
        self._populate_table()

    def _populate_table(self) -> None:
        self._table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            self._table.setItem(row, _COL_NAME, QTableWidgetItem(entry.name))
            self._table.setItem(row, _COL_SOURCE, QTableWidgetItem(_SOURCE_LABELS.get(entry.source, entry.source)))
            self._table.setItem(row, _COL_IMPACT, QTableWidgetItem(entry.boot_impact.value))
            self._table.setItem(row, _COL_STATUS, QTableWidgetItem("사용" if entry.enabled else "사용 안 함"))
            self._table.setItem(row, _COL_COMMAND, QTableWidgetItem(entry.command))

    def _add_entry(self) -> None:
        name, ok = QInputDialog.getText(self, "시작 프로그램 추가", "이름:")
        if not ok or not name.strip():
            return
        path, _filter = QFileDialog.getOpenFileName(self, "실행 파일 선택")
        if not path:
            return
        self._apply_add_entry(name.strip(), path)

    def _apply_add_entry(self, name: str, command: str) -> None:
        if add_startup_program(name, command):
            self._refresh()
        else:
            QMessageBox.warning(self, "추가 실패", "시작 프로그램을 추가하지 못했습니다.")

    def _remove_selected(self) -> None:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()}, reverse=True)
        any_failed = False
        for row in rows:
            entry = self._entries[row]
            if entry.source != "registry":
                continue  # scheduled tasks are view-only here, see module docstring
            if not remove_startup_program(entry.name):
                any_failed = True
        if any_failed:
            QMessageBox.warning(self, "제거 실패", "일부 항목을 제거하지 못했습니다.")
        self._refresh()

    def stop(self) -> None:
        """Waits for any in-flight scan to finish before the app can
        safely close. Unlike the rest of this app's workers (which only
        start in response to a user action), this page kicks a QThread
        off immediately on construction - and unlike a quick local test,
        it can genuinely take several real seconds on actual Windows
        (schtasks itself, plus a psutil scan per non-N/A entry). Destroying
        a QThread object while its run() is still executing is undefined
        behavior in Qt, not just a missed cleanup - the timeout here is
        set past _query_scheduled_tasks()'s own 30s subprocess timeout so
        a legitimately slow-but-finishing scan isn't cut short.
        """
        if self._worker is not None:
            self._worker.wait(35000)
