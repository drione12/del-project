"""Memory Master entry point."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from core.config import load_config  # noqa: E402
from core.resource_path import resource_path  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def _load_stylesheet(theme: str) -> str:
    qss_name = "theme.qss" if theme == "dark" else "theme_light.qss"
    try:
        with open(resource_path(qss_name), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def main() -> int:
    config = load_config()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # tray keeps the app alive when the window is hidden
    app.setStyleSheet(_load_stylesheet(config.theme))

    window = MainWindow()
    if config.always_on_top:
        window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.setWindowOpacity(config.opacity)
    window.show()

    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
