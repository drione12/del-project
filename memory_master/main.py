"""Memory Master entry point."""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from core.config import load_config  # noqa: E402
from core.logging_setup import get_logger  # noqa: E402
from core.resource_path import resource_path  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

logger = get_logger(__name__)


def _install_crash_logging() -> None:
    """The packaged build runs with --windowed (see
    build-memory-master.yml) - there's no console, so an uncaught
    exception's default traceback (sys.__excepthook__, which prints to
    stderr) goes nowhere and the app just silently disappears, with zero
    diagnostic information left behind. Routing it through the same
    rotating file logger every other unexpected-but-handled failure in this
    app already uses (core/logging_setup.py) means a future crash at least
    leaves a trace in %LOCALAPPDATA%\\MemoryMaster\\logs\\memory_master.log.
    """
    default_hook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        logger.critical("unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
        default_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def _load_stylesheet(theme: str) -> str:
    qss_name = "theme.qss" if theme == "dark" else "theme_light.qss"
    try:
        with open(resource_path(qss_name), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--start-page", default=None)
    # parse_known_args (not parse_args): any other/unrecognized argument -
    # e.g. one Qt itself understands - is left alone rather than treated as
    # an error, since this app has never validated its own argv before.
    args, _unknown = parser.parse_known_args(argv)
    return args


def main() -> int:
    _install_crash_logging()
    config = load_config()
    args = _parse_args(sys.argv[1:])

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # tray keeps the app alive when the window is hidden
    app.setStyleSheet(_load_stylesheet(config.theme))

    window = MainWindow(start_page=args.start_page)
    if config.always_on_top:
        window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.setWindowOpacity(config.opacity)
    window.show()

    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
