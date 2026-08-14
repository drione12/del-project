"""System tray icon: minimize-to-tray on close, right-click menu with
Open/Quit - keeps the app reachable without needing to be relaunched
instead of exiting when the window is closed.
"""
from __future__ import annotations

from PyQt5.QtWidgets import QAction, QApplication, QMenu, QStyle, QSystemTrayIcon


def setup_tray(window) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(window)
    tray.setIcon(window.style().standardIcon(QStyle.SP_ComputerIcon))
    tray.setToolTip("Memory Master")

    menu = QMenu()
    show_action = QAction("열기", window)
    show_action.triggered.connect(window.show_and_raise)
    quit_action = QAction("종료", window)
    quit_action.triggered.connect(QApplication.instance().quit)
    menu.addAction(show_action)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)

    def _on_activated(reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            window.show_and_raise()

    tray.activated.connect(_on_activated)
    tray.show()
    return tray
