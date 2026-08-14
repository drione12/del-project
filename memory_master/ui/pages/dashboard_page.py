"""Dashboard page - the mockup's default landing view: a hero RAM gauge,
compact CPU/Disk/Network readouts, four trend/breakdown tiles, a 2-minute
RAM+Swap wave chart, the memory composition bar, Quick Actions, and Top
Processes.

All psutil sampling happens on MetricsWorker (a QThread), including the
memory-allocation breakdown (which, on Windows, does its own short
process scan for the Memory Compression process) - anything that touches
psutil.process_iter() belongs off the UI thread, per the pattern used
everywhere else in this app. Per-process icon lookups (core/icons.py) are
the one exception done on the UI thread: QPixmap construction isn't safe
to do outside the GUI thread in Qt, and the lookups are cached by path
after the first tick anyway, so the steady-state cost is a dict lookup.
"""
from __future__ import annotations

import sys
from collections import deque
from typing import Callable, Deque, List

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import system_health
from core.formatting import format_bytes, format_rate
from core.memory_allocation import MemoryAllocation, get_memory_allocation
from core.metrics import MetricsSampler, ProcessInfo, Snapshot, top_processes
from core.optimizer import CleanResult, OptimizeResult, clean_cache, optimize_ram
from ui.dialogs.memory_details_dialog import MemoryDetailsDialog
from widgets.arc_gauge import ArcGaugeWidget
from widgets.mini_bar_chart import Bar, MiniBarChartWidget
from widgets.process_row import ProcessRowWidget
from widgets.segmented_bar import SegmentedBarWidget
from widgets.status_pill import StatusPill
from widgets.wave_chart import WaveChartWidget

if sys.platform == "win32":
    from core.icons import get_icon_for_path
else:
    def get_icon_for_path(path: str):
        return None


_HISTORY_LENGTH = 60  # ~2 minutes at the default 2s poll interval
_TREND_BAR_COUNT = 8
_CATEGORY_COLORS = ["#2f7de0", "#059669", "#ea580c", "#0d9488"]


def _card(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("role", "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    if title:
        label = QLabel(title)
        label.setStyleSheet("font-size: 13px; font-weight: 600; color: #8c92a4; background: transparent;")
        layout.addWidget(label)
    return frame, layout


class MetricsWorker(QThread):
    snapshotReady = pyqtSignal(object, object, object)  # Snapshot, List[ProcessInfo], MemoryAllocation

    def __init__(self, interval_seconds: float = 2.0, parent=None):
        super().__init__(parent)
        self._interval = interval_seconds
        self._running = True

    def run(self) -> None:
        sampler = MetricsSampler()
        while self._running:
            snapshot = sampler.sample()
            processes = top_processes(limit=8)
            allocation = get_memory_allocation()
            self.snapshotReady.emit(snapshot, processes, allocation)
            self.msleep(int(self._interval * 1000))

    def stop(self) -> None:
        self._running = False
        self.wait(3000)


class _ActionWorker(QThread):
    """One-shot worker for a Quick Action button - Qt parent/child
    ownership (parent=page) keeps this alive for its short run without
    needing a manually-managed reference list.
    """

    resultReady = pyqtSignal(object)

    def __init__(self, fn: Callable[[], object], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        self.resultReady.emit(self._fn())


class DashboardPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._ram_history: Deque[float] = deque(maxlen=_HISTORY_LENGTH)
        self._cpu_history: Deque[float] = deque(maxlen=_HISTORY_LENGTH)
        self._swap_history: Deque[float] = deque(maxlen=_HISTORY_LENGTH)

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

        root.addLayout(self._build_hero_row())
        root.addLayout(self._build_trend_row())
        root.addWidget(self._build_wave_card())
        root.addWidget(self._build_allocation_card())
        root.addLayout(self._build_quick_actions())
        root.addWidget(self._build_process_list(), 1)

        self._worker = MetricsWorker(parent=self)
        self._worker.snapshotReady.connect(self._on_snapshot)
        self._worker.start()

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    # -- construction -------------------------------------------------------

    def _build_hero_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)

        gauge_frame, gauge_layout = _card()
        self._gauge = ArcGaugeWidget("RAM 사용량")
        gauge_layout.addWidget(self._gauge)
        self._status_pill = StatusPill()
        pill_row = QHBoxLayout()
        pill_row.addStretch(1)
        pill_row.addWidget(self._status_pill)
        pill_row.addStretch(1)
        gauge_layout.addLayout(pill_row)
        row.addWidget(gauge_frame, 1)

        readouts_frame, readouts_layout = _card("시스템 상태")
        self._cpu_bar = self._make_readout(readouts_layout, "CPU")
        self._disk_bar = self._make_readout(readouts_layout, "디스크")
        self._net_label = QLabel("네트워크: -")
        self._net_label.setStyleSheet("color: #8c92a4; font-size: 12px; background: transparent;")
        readouts_layout.addWidget(self._net_label)
        readouts_layout.addStretch(1)
        row.addWidget(readouts_frame, 1)

        return row

    @staticmethod
    def _make_readout(layout: QVBoxLayout, label: str) -> QProgressBar:
        row = QHBoxLayout()
        name = QLabel(label)
        name.setFixedWidth(48)
        name.setStyleSheet("color: #e2e8f0; font-size: 12px; background: transparent;")
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(True)
        row.addWidget(name)
        row.addWidget(bar, 1)
        layout.addLayout(row)
        return bar

    def _build_trend_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        self._ram_trend = MiniBarChartWidget("RAM 추이")
        self._cpu_trend = MiniBarChartWidget("CPU 추이")
        self._swap_trend = MiniBarChartWidget("Swap 추이")
        self._breakdown = MiniBarChartWidget("항목별 비교")
        for tile in (self._ram_trend, self._cpu_trend, self._swap_trend, self._breakdown):
            frame = QFrame()
            frame.setProperty("role", "card")
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(0, 0, 0, 0)
            frame_layout.addWidget(tile)
            row.addWidget(frame, 1)
        return row

    def _build_wave_card(self) -> QFrame:
        frame, layout = _card("RAM / Swap 추이 (최근 2분)")
        self._wave = WaveChartWidget()
        layout.addWidget(self._wave)
        return frame

    def _build_allocation_card(self) -> QFrame:
        frame, layout = _card("메모리 할당")
        self._segmented_bar = SegmentedBarWidget()
        layout.addWidget(self._segmented_bar)
        return frame

    def _build_quick_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        self._optimize_btn = QPushButton("RAM 최적화")
        self._optimize_btn.setProperty("role", "primary")
        self._optimize_btn.clicked.connect(self._on_optimize_ram)

        self._clean_btn = QPushButton("캐시 정리")
        self._clean_btn.clicked.connect(self._on_clean_cache)

        details_btn = QPushButton("자세히 보기")
        details_btn.clicked.connect(self._on_view_details)

        for btn in (self._optimize_btn, self._clean_btn, details_btn):
            row.addWidget(btn)
        row.addStretch(1)
        return row

    def _build_process_list(self) -> QFrame:
        frame, layout = _card("상위 프로세스")
        self._process_list = QListWidget()
        self._process_list.setFrameShape(QFrame.NoFrame)
        self._process_list.setSelectionMode(QAbstractItemView.NoSelection)
        self._process_list.setMinimumHeight(260)
        layout.addWidget(self._process_list)
        return frame

    # -- updates --------------------------------------------------------------

    def _on_snapshot(self, snapshot: Snapshot, processes: List[ProcessInfo], allocation: MemoryAllocation) -> None:
        self._ram_history.append(snapshot.ram_percent)
        self._cpu_history.append(snapshot.cpu_percent)
        self._swap_history.append(snapshot.swap_percent)

        self._gauge.set_value(snapshot.ram_percent)
        self._status_pill.set_severity(
            system_health.overall_severity(snapshot.ram_percent, snapshot.cpu_percent, snapshot.swap_percent)
        )
        self._cpu_bar.setValue(int(snapshot.cpu_percent))
        self._disk_bar.setValue(int(snapshot.disk_percent))
        self._net_label.setText(
            f"네트워크: ↑{format_rate(snapshot.net_sent_rate)} ↓{format_rate(snapshot.net_recv_rate)}"
        )

        self._ram_trend.set_bars(self._trend_bars(self._ram_history, "#2f7de0"))
        self._cpu_trend.set_bars(self._trend_bars(self._cpu_history, "#059669"))
        self._swap_trend.set_bars(self._trend_bars(self._swap_history, "#ea580c"))
        self._breakdown.set_bars(
            [
                Bar("RAM", snapshot.ram_percent, _CATEGORY_COLORS[0]),
                Bar("CPU", snapshot.cpu_percent, _CATEGORY_COLORS[1]),
                Bar("Swap", snapshot.swap_percent, _CATEGORY_COLORS[2]),
                Bar("Disk", snapshot.disk_percent, _CATEGORY_COLORS[3]),
            ]
        )

        self._wave.set_history(list(self._ram_history), list(self._swap_history))
        self._segmented_bar.set_segments(allocation.segments)

        self._process_list.clear()
        for proc in processes:
            item = QListWidgetItem(self._process_list)
            widget = ProcessRowWidget(
                proc.name or f"PID {proc.pid}",
                f"PID {proc.pid} · CPU {proc.cpu_percent:.0f}%",
                format_bytes(proc.memory_bytes),
                get_icon_for_path(proc.exe) if proc.exe else None,
            )
            item.setSizeHint(widget.sizeHint())
            self._process_list.addItem(item)
            self._process_list.setItemWidget(item, widget)

    @staticmethod
    def _trend_bars(history: Deque[float], color: str) -> List[Bar]:
        recent = list(history)[-_TREND_BAR_COUNT:]
        return [Bar("", value, color) for value in recent]

    # -- quick actions ----------------------------------------------------

    def _on_optimize_ram(self) -> None:
        self._run_action(self._optimize_btn, optimize_ram, self._on_optimize_done)

    def _on_optimize_done(self, result: OptimizeResult) -> None:
        self._show_result(
            "RAM 최적화 완료",
            f"{result.trimmed_count}개 프로세스 최적화, {result.skipped_count}개 건너뜀\n"
            f"RAM 사용률: {result.ram_before_percent:.1f}% → {result.ram_after_percent:.1f}%",
        )

    def _on_clean_cache(self) -> None:
        self._run_action(self._clean_btn, clean_cache, self._on_clean_done)

    def _on_clean_done(self, result: CleanResult) -> None:
        self._show_result(
            "캐시 정리 완료",
            f"{result.deleted_count}개 파일 삭제, {result.skipped_count}개 건너뜀 "
            f"({format_bytes(result.freed_bytes)} 확보)",
        )

    def _on_view_details(self) -> None:
        MemoryDetailsDialog(self).exec_()

    def _run_action(self, button: QPushButton, fn: Callable[[], object], on_done: Callable[[object], None]) -> None:
        button.setEnabled(False)
        worker = _ActionWorker(fn, self)

        def _handle(result: object) -> None:
            button.setEnabled(True)
            on_done(result)

        worker.resultReady.connect(_handle)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _show_result(self, title: str, message: str) -> None:
        QMessageBox.information(self, title, message)

    def stop(self) -> None:
        self._worker.stop()
