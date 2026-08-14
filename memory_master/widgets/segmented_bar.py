"""One clipped rounded-rect bar divided into proportional colored segments
(In Use / Standby / Compressed / Free) - a single QPainterPath used as a
clip so segment edges are naturally rounded only at the bar's own two
ends, not per-segment (per-segment rounding would look like separate
pills, not one bar - see the plan doc). A legend row below lists each
segment's label + percentage.
"""
from __future__ import annotations

from typing import List

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPainterPath
from PyQt5.QtWidgets import QWidget

from core.memory_allocation import MemorySegment

_SEGMENT_COLORS = ["#2f7de0", "#0d9488", "#ea580c", "#3a4256"]


class SegmentedBarWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: List[MemorySegment] = []
        self.setMinimumHeight(90)

    def set_segments(self, segments: List[MemorySegment]) -> None:
        self._segments = segments
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        bar_rect = QRectF(0.0, 0.0, float(self.width()), 28.0)
        clip = QPainterPath()
        clip.addRoundedRect(bar_rect, 8.0, 8.0)
        painter.setClipPath(clip)

        total = sum(s.percent for s in self._segments) or 1.0
        x = 0.0
        for i, seg in enumerate(self._segments):
            width = bar_rect.width() * (seg.percent / total)
            color = QColor(_SEGMENT_COLORS[i % len(_SEGMENT_COLORS)])
            painter.fillRect(QRectF(x, 0.0, width, bar_rect.height()), color)
            x += width

        painter.setClipping(False)
        self._draw_legend(painter, bar_rect.bottom() + 12.0)

    def _draw_legend(self, painter: QPainter, top: float) -> None:
        x = 0.0
        y = top
        row_height = 20.0
        for i, seg in enumerate(self._segments):
            color = QColor(_SEGMENT_COLORS[i % len(_SEGMENT_COLORS)])
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(x, y + 4.0, 10.0, 10.0), 2.0, 2.0)
            painter.setPen(QColor("#e2e8f0"))
            text = f"{seg.label} {seg.percent:.0f}%"
            painter.drawText(QRectF(x + 16.0, y, 150.0, row_height), Qt.AlignVCenter, text)
            x += 160.0
            if x > self.width() - 160.0:
                x = 0.0
                y += row_height
