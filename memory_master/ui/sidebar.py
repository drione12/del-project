"""Left icon sidebar - the mockup's page switcher. Exclusive checkable
buttons (QButtonGroup) drive a QStackedWidget in MainWindow; this widget
only knows about page ids/icons/tooltips, not what's actually on each page.
"""
from __future__ import annotations

import os
from typing import Dict, List, Tuple

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QButtonGroup, QToolButton, QVBoxLayout, QWidget

_ICONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "icons")


class Sidebar(QWidget):
    pageSelected = pyqtSignal(str)

    def __init__(self, pages: List[Tuple[str, str, str]], parent=None):
        """pages: list of (page_id, icon_name, tooltip)."""
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(64)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 8, 16)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignTop)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: Dict[str, QToolButton] = {}

        for page_id, icon_name, tooltip in pages:
            btn = QToolButton(self)
            btn.setObjectName("SidebarButton")
            btn.setIcon(QIcon(os.path.join(_ICONS_DIR, f"{icon_name}.svg")))
            btn.setIconSize(QSize(26, 26))
            btn.setCheckable(True)
            btn.setToolTip(tooltip)
            btn.setFixedSize(48, 48)
            btn.clicked.connect(lambda _checked, pid=page_id: self.pageSelected.emit(pid))
            self._group.addButton(btn)
            self._buttons[page_id] = btn
            layout.addWidget(btn)

        layout.addStretch(1)

    def set_current(self, page_id: str) -> None:
        btn = self._buttons.get(page_id)
        if btn is not None:
            btn.setChecked(True)
