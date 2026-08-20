"""CI smoke check: constructs the main window without entering the event
loop or needing a real display (run with QT_QPA_PLATFORM=offscreen).
Confirms the app and every page import and build without crashing - this
can't run on the Linux dev environment this app was written in (no PyQt5
there), only in CI on windows-latest.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    app.processEvents()
    print("OK: MainWindow constructed and all pages built without error.")

    # QStackedWidget only delivers a real showEvent to whichever page is
    # current, so the default-page-only check above never exercises the
    # Search page's own showEvent-gated logic. A CI runner process is
    # never elevated, so this deterministically exercises the "not
    # elevated -> fallback view" branch for real - the embedded-view
    # branch stays fundamentally untestable here (no real window manager
    # under QT_QPA_PLATFORM=offscreen to embed a child window into).
    window.go_to_page("search")
    app.processEvents()
    print("OK: Search page shown (fallback view, since this process isn't elevated).")

    window.shutdown()
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
