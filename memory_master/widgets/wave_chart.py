"""Smoothed 2-line trend chart (RAM% + Swap% over the last ~2 minutes) -
QPainterPath.cubicTo between sample points for a soft wave rather than
sharp polyline segments, matching the mockup's rendering. A legend row
(2 color swatches + labels) sits below the plot area.
"""
from __future__ import annotations

from typing import List

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QWidget

_RAM_COLOR = "#2f7de0"
_SWAP_COLOR = "#0d9488"


def _smooth_path(points: List[QPointF]) -> QPainterPath:
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0])
    for i in range(1, len(points)):
        p0 = points[i - 1]
        p1 = points[i]
        mid_x = (p0.x() + p1.x()) / 2.0
        path.cubicTo(QPointF(mid_x, p0.y()), QPointF(mid_x, p1.y()), p1)
    return path


class WaveChartWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._ram_history: List[float] = []
        self._swap_history: List[float] = []
        self.setMinimumHeight(140)

    def set_history(self, ram_history: List[float], swap_history: List[float]) -> None:
        self._ram_history = list(ram_history)
        self._swap_history = list(swap_history)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        legend_height = 24.0
        plot_rect = QRectF(12.0, 8.0, float(self.width()) - 24.0, float(self.height()) - legend_height - 16.0)

        self._draw_series(painter, plot_rect, self._ram_history, _RAM_COLOR, filled=True)
        self._draw_series(painter, plot_rect, self._swap_history, _SWAP_COLOR, filled=False)
        self._draw_legend(painter)

    def _draw_series(self, painter: QPainter, rect: QRectF, history: List[float], color: str, filled: bool) -> None:
        if len(history) < 2:
            return

        n = len(history)
        points = []
        for i, value in enumerate(history):
            x = rect.left() + (rect.width() * i / (n - 1))
            value = max(0.0, min(100.0, value))
            y = rect.bottom() - (rect.height() * value / 100.0)
            points.append(QPointF(x, y))

        line_path = _smooth_path(points)

        if filled:
            fill_path = QPainterPath(line_path)
            fill_path.lineTo(points[-1].x(), rect.bottom())
            fill_path.lineTo(points[0].x(), rect.bottom())
            fill_path.closeSubpath()
            gradient = QLinearGradient(0.0, rect.top(), 0.0, rect.bottom())
            fill_color_top = QColor(color)
            fill_color_top.setAlpha(90)
            fill_color_bottom = QColor(color)
            fill_color_bottom.setAlpha(0)
            gradient.setColorAt(0.0, fill_color_top)
            gradient.setColorAt(1.0, fill_color_bottom)
            painter.fillPath(fill_path, gradient)

        pen = QPen(QColor(color))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawPath(line_path)

    def _draw_legend(self, painter: QPainter) -> None:
        y = float(self.height()) - 18.0
        entries = [("RAM", _RAM_COLOR), ("Swap", _SWAP_COLOR)]
        x = 12.0
        for label, color in entries:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QRectF(x, y, 8.0, 8.0))
            painter.setPen(QColor("#8c92a4"))
            painter.drawText(QRectF(x + 12.0, y - 4.0, 60.0, 16.0), Qt.AlignVCenter, label)
            x += 80.0
