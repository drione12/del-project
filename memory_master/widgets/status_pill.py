"""Small rounded status badge (e.g. "정상"/"주의"/"위험") - color comes from
the reserved status palette (core/system_health.Severity), which is never
reused as a chart series color so a pill's color always means severity and
nothing else on screen does. The label defaults to the severity's own name
but can be overridden (e.g. Startup Manager's boot-impact column shows
"낮음"/"보통"/"높음" in the same severity colors, since "정상"/"주의"/"위험"
wouldn't make sense in that context).
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtGui import QColor, QPainter, QPainterPath
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget

from core.system_health import Severity, label_for

_COLORS = {
    Severity.GOOD: QColor("#059669"),
    Severity.WARNING: QColor("#ea580c"),
    Severity.CRITICAL: QColor("#dc2626"),
}


class StatusPill(QWidget):
    def __init__(self, severity: Severity = Severity.GOOD, parent=None, text: Optional[str] = None):
        super().__init__(parent)
        self._severity = severity

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        self._label = QLabel(self)
        self._label.setStyleSheet("color: white; font-weight: 600; font-size: 12px; background: transparent;")
        layout.addWidget(self._label)

        self.set_severity(severity, text=text)

    def set_severity(self, severity: Severity, text: Optional[str] = None) -> None:
        self._severity = severity
        self._label.setText(text if text is not None else label_for(severity))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        radius = float(self.height()) / 2.0
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(self.width()), float(self.height()), radius, radius)
        painter.fillPath(path, _COLORS[self._severity])
