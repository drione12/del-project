"""Frameless main window: custom title bar + left icon sidebar driving a
QStackedWidget across the app's pages. Minimize/maximize/close are all
real; there's deliberately no drag-resize in v1 - and no code is needed to
prevent it, since a frameless window (Qt.FramelessWindowHint) has no
native edge/corner resize grips at all unless the app manually implements
hit-testing for them (which this doesn't, to avoid the fiddliest part of
frameless-window work - see the plan doc). Minimizing/closing both go to
the system tray instead of exiting - the Protection page's blacklist
watchdog (once built) only makes sense running continuously in the
background.
"""
from __future__ import annotations

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from ui.pages.cleanup_page import CleanupPage
from ui.pages.dashboard_page import DashboardPage
from ui.pages.placeholder_page import PlaceholderPage
from ui.sidebar import Sidebar
from ui.title_bar import TitleBar
from ui.tray import setup_tray

WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800

# (page_id, icon_name, tooltip) - the mockup's 5 sidebar icons. Dashboard is
# the first real page (added in a later commit); the rest start as
# placeholders and get swapped in one at a time as each is built.
_PAGES = [
    ("dashboard", "cpu", "대시보드"),
    ("cleanup", "cleanup", "정리"),
    ("protection", "shield", "보호"),
    ("settings", "gear", "설정"),
    ("startup", "sliders", "시작 프로그램"),
]

_PLACEHOLDER_SUBTITLE = "곧 추가될 예정입니다."


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setWindowTitle("Memory Master")
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)

        root = QWidget(self)
        root.setObjectName("RootBackground")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._title_bar = TitleBar("Memory Master", self)
        self._title_bar.minimizeClicked.connect(self.showMinimized)
        self._title_bar.maximizeRestoreClicked.connect(self._toggle_maximize)
        self._title_bar.closeClicked.connect(self.close)
        self._title_bar.settingsClicked.connect(lambda: self.go_to_page("settings"))
        outer.addWidget(self._title_bar)

        body = QWidget(root)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self._sidebar = Sidebar(list(_PAGES), self)
        self._sidebar.pageSelected.connect(self.go_to_page)
        body_layout.addWidget(self._sidebar)

        self._stack = QStackedWidget(body)
        body_layout.addWidget(self._stack, 1)
        outer.addWidget(body, 1)

        self._page_indices = {}
        self._build_pages()

        self._tray = setup_tray(self)
        self.go_to_page("dashboard")

    def _build_pages(self) -> None:
        for page_id, icon_name, tooltip in _PAGES:
            widget = self._make_page(page_id, icon_name, tooltip)
            self._page_indices[page_id] = self._stack.addWidget(widget)

    @staticmethod
    def _make_page(page_id: str, icon_name: str, tooltip: str) -> QWidget:
        if page_id == "dashboard":
            return DashboardPage()
        if page_id == "cleanup":
            return CleanupPage()
        return PlaceholderPage(icon_name, tooltip, _PLACEHOLDER_SUBTITLE)

    def go_to_page(self, page_id: str) -> None:
        index = self._page_indices.get(page_id)
        if index is not None:
            self._stack.setCurrentIndex(index)
            self._sidebar.set_current(page_id)

    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.WindowStateChange:
            self._title_bar.set_maximized(self.isMaximized())
        super().changeEvent(event)

    def show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()
        if self._tray.isSystemTrayAvailable():
            self._tray.showMessage("Memory Master", "백그라운드에서 계속 실행 중입니다.")

    def shutdown(self) -> None:
        """Stops any background workers a page may have started (currently
        just the dashboard's metrics poller). Called explicitly by the
        offscreen smoke check, which never enters a real exec_() event
        loop - the normally-running app instead stops workers via
        DashboardPage's own QApplication.aboutToQuit connection, made when
        the tray's exit action calls QApplication.quit() from inside
        exec_().
        """
        for i in range(self._stack.count()):
            page = self._stack.widget(i)
            stop = getattr(page, "stop", None)
            if callable(stop):
                stop()
