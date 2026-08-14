"""Custom-painted circular gauge for the dashboard's hero RAM number -
QPainterPath arc + QConicalGradient pen. The blue-green gradient is
intentional decorative chrome (see the plan doc): severity is read from
the number and the status pill next to it, not from the gauge's color, so
there's no separate "gauge turns red at 90%" logic to keep in sync with
core/system_health.py's thresholds.
"""
from __future__ import annotations

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QConicalGradient, QPainter, QPen
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget

_START_ANGLE = 225  # degrees; Qt convention: 0 = 3 o'clock, positive = CCW
_SPAN_ANGLE = -270  # sweeps clockwise 270 degrees, leaving a gap at the bottom
_TRACK_COLOR = "#1f2532"


class ArcGaugeWidget(QWidget):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self._value = 0.0
        self.setMinimumSize(160, 160)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        self._value_label = QLabel("0%", self)
        self._value_label.setAlignment(Qt.AlignCenter)
        self._value_label.setStyleSheet(
            "font-size: 34px; font-weight: 700; color: #e2e8f0; background: transparent;"
        )

        self._title_label = QLabel(title, self)
        self._title_label.setAlignment(Qt.AlignCenter)
        self._title_label.setStyleSheet("font-size: 12px; color: #8c92a4; background: transparent;")

        layout.addStretch(1)
        layout.addWidget(self._value_label)
        layout.addWidget(self._title_label)
        layout.addStretch(1)

    def set_value(self, percent: float) -> None:
        self._value = max(0.0, min(100.0, percent))
        self._value_label.setText(f"{self._value:.0f}%")
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        side = max(min(self.width(), self.height()) - 20, 10)
        rect = QRectF((self.width() - side) / 2.0, (self.height() - side) / 2.0, float(side), float(side))
        pen_width = max(side * 0.09, 6.0)

        track_pen = QPen(QColor(_TRACK_COLOR))
        track_pen.setWidthF(pen_width)
        track_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, _START_ANGLE * 16, _SPAN_ANGLE * 16)

        gradient = QConicalGradient(rect.center(), _START_ANGLE)
        gradient.setColorAt(0.0, QColor("#2f7de0"))
        gradient.setColorAt(1.0, QColor("#059669"))
        value_pen = QPen()
        value_pen.setBrush(gradient)
        value_pen.setWidthF(pen_width)
        value_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(value_pen)

        value_span = _SPAN_ANGLE * (self._value / 100.0)
        painter.drawArc(rect, _START_ANGLE * 16, int(value_span * 16))
