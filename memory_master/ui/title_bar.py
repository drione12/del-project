"""Custom title bar for the frameless main window - drag-to-move (via Qt's
own startSystemMove, which also gets Aero-snap for free), a settings gear
that routes to the same page the sidebar's gear does, and min/max/close
buttons. There's no native frame to draw these into since the window is
frameless (needed to match the mockup's look), so this widget stands in
for one.
"""
from __future__ import annotations

import os

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from core.resource_path import resource_path

_ICONS_DIR = resource_path("icons")


def _icon(name: str) -> QIcon:
    return QIcon(os.path.join(_ICONS_DIR, f"{name}.svg"))


class TitleBar(QWidget):
    settingsClicked = pyqtSignal()
    minimizeClicked = pyqtSignal()
    maximizeRestoreClicked = pyqtSignal()
    closeClicked = pyqtSignal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(44)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(4)

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet("font-size: 14px; font-weight: 600; color: #e2e8f0;")

        settings_btn = self._make_button("gear", self.settingsClicked)
        min_btn = self._make_button("minimize", self.minimizeClicked)
        self._max_restore_btn = self._make_button("maximize", self.maximizeRestoreClicked)
        close_btn = self._make_button("close", self.closeClicked)
        close_btn.setObjectName("CloseButton")

        layout.addWidget(settings_btn)
        layout.addStretch(1)
        layout.addWidget(self._title_label)
        layout.addStretch(1)
        layout.addWidget(min_btn)
        layout.addWidget(self._max_restore_btn)
        layout.addWidget(close_btn)

    def _make_button(self, icon_name: str, signal) -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName("TitleBarButton")
        btn.setIcon(_icon(icon_name))
        btn.setIconSize(QSize(16, 16))
        btn.setFixedSize(32, 28)
        btn.clicked.connect(signal.emit)
        return btn

    def set_maximized(self, is_maximized: bool) -> None:
        self._max_restore_btn.setIcon(_icon("restore" if is_maximized else "maximize"))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)
