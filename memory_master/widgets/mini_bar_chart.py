"""Small labeled bar tile - reused 4x on the dashboard: three single-hue
trend-history tiles (RAM/CPU/Swap, each bar a recent sample, same color)
and one multi-hue category-breakdown tile (each bar a different metric,
each its own color from the categorical set). One widget, two uses: the
*shape* (title + N bars) is identical either way, only whether the bars
share a color is data, not structure.
"""
from __future__ import annotations

from typing import List, NamedTuple

from PyQt5.QtCore import QRectF
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget


class Bar(NamedTuple):
    label: str
    value: float  # 0-100
    color: str  # hex


class MiniBarChartWidget(QWidget):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self._bars: List[Bar] = []
        self.setMinimumHeight(90)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self._title_label = QLabel(title, self)
        self._title_label.setStyleSheet(
            "font-size: 12px; font-weight: 600; color: #8c92a4; background: transparent;"
        )
        layout.addWidget(self._title_label)
        layout.addStretch(1)

    def set_bars(self, bars: List[Bar]) -> None:
        self._bars = bars
        self.update()

    def paintEvent(self, event) -> None:
        if not self._bars:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        top = 30.0
        bottom = float(self.height()) - 8.0
        chart_height = max(bottom - top, 1.0)
        n = len(self._bars)
        spacing = 4.0
        available_width = float(self.width()) - 16.0
        bar_width = max((available_width - spacing * (n - 1)) / n, 2.0)

        x = 8.0
        for bar in self._bars:
            value = max(0.0, min(100.0, bar.value))
            bar_height = chart_height * (value / 100.0)
            rect = QRectF(x, bottom - bar_height, bar_width, bar_height)
            painter.fillRect(rect, QColor(bar.color))
            x += bar_width + spacing
