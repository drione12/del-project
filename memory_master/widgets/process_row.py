"""One row of the dashboard's Top Processes list - icon, name, and a
right-aligned memory reading. Used as the item widget for each
QListWidgetItem rather than a bare QTableWidget row, so it can match the
mockup's rounded-card list style instead of a plain grid.
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget


class ProcessRowWidget(QWidget):
    def __init__(self, name: str, detail: str, memory_text: str, icon: Optional[QPixmap] = None, parent=None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)

        icon_label = QLabel(self)
        icon_label.setFixedSize(28, 28)
        if icon is not None and not icon.isNull():
            icon_label.setPixmap(icon.scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(0)
        name_label = QLabel(name, self)
        name_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #e2e8f0; background: transparent;")
        detail_label = QLabel(detail, self)
        detail_label.setStyleSheet("font-size: 11px; color: #8c92a4; background: transparent;")
        text_layout.addWidget(name_label)
        text_layout.addWidget(detail_label)
        layout.addLayout(text_layout, 1)

        memory_label = QLabel(memory_text, self)
        memory_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        memory_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #e2e8f0; background: transparent;")
        layout.addWidget(memory_label)
