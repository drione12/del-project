"""Memory Master entry point."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402


def _load_stylesheet() -> str:
    qss_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "theme.qss")
    try:
        with open(qss_path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # tray keeps the app alive when the window is hidden
    app.setStyleSheet(_load_stylesheet())

    window = MainWindow()
    window.show()

    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
