"""Drag-and-drop target - drop a file or folder to run it through the same
Analyze -> confirm -> Execute force-delete flow as a manually entered path
(see core/force_delete.py). Not a separate delete code path, just another
way to supply the initial target path.
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget


class DropZoneWidget(QWidget):
    pathDropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)
        self.setProperty("role", "card")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        self._label = QLabel("여기로 파일이나 폴더를 끌어다 놓으면 강제 삭제를 진행합니다")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("color: #8c92a4; font-size: 13px; background: transparent;")
        layout.addWidget(self._label)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self.pathDropped.emit(path)
        event.acceptProposedAction()
