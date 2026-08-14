"""Shared stub for sidebar pages that don't have real content yet - shown
with its real label so the sidebar stays honest about what's there versus
what's still coming, rather than a dead page. Mirrors this repo's own
src/main.cpp pattern of showing real menu labels for unbuilt items (there,
grayed out; a sidebar page can't really be "grayed out" the way a menu item
can, so a clearly-labeled stub is the adapted equivalent - see the plan
doc for the full reasoning).
"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget

from core.resource_path import resource_path

_ICONS_DIR = resource_path("icons")


class PlaceholderPage(QWidget):
    def __init__(self, icon_name: str, title: str, subtitle: str = "곧 추가될 예정입니다.", parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(12)

        icon_label = QLabel(self)
        icon_label.setPixmap(QIcon(os.path.join(_ICONS_DIR, f"{icon_name}.svg")).pixmap(48, 48))
        icon_label.setAlignment(Qt.AlignCenter)

        title_label = QLabel(title, self)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 20px; font-weight: 600; color: #e2e8f0;")

        subtitle_label = QLabel(subtitle, self)
        subtitle_label.setAlignment(Qt.AlignCenter)
        subtitle_label.setStyleSheet("color: #8c92a4;")

        layout.addWidget(icon_label)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
