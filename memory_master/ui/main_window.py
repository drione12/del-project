"""Frameless main window: custom title bar + left icon sidebar driving a
QStackedWidget across the app's pages. Minimize/maximize/close are all
real. A frameless window (Qt.FramelessWindowHint) has no native
edge/corner resize grips at all - TitleBar already works around the
equivalent problem for *moving* the window via QWindow.startSystemMove()
(see title_bar.py); this window resizes itself with that same API's
sibling, QWindow.startSystemResize(), via an application-wide event filter
that detects a press within _RESIZE_MARGIN px of an edge (see
eventFilter()/_resize_edges_at() below) - both hand the actual drag off to
the OS once started, rather than this code tracking mouse movement itself.
Minimizing/closing both go to the system tray instead of exiting, so the
app stays reachable without needing to be relaunched.
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.pages.cleanup_page import CleanupPage
from ui.pages.dashboard_page import DashboardPage
from ui.pages.placeholder_page import PlaceholderPage
from ui.pages.search_page import SearchPage
from ui.pages.settings_page import SettingsPage
from ui.pages.startup_manager_page import StartupManagerPage
from ui.sidebar import Sidebar
from ui.title_bar import TitleBar
from ui.tray import setup_tray

WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800
MIN_WINDOW_WIDTH = 960
MIN_WINDOW_HEIGHT = 600
_RESIZE_MARGIN = 6  # px - how close to the window's edge counts as "grab to resize"

# (page_id, icon_name, tooltip) - the sidebar's pages.
_PAGES = [
    ("dashboard", "cpu", "대시보드"),
    ("cleanup", "cleanup", "정리"),
    ("search", "search", "파일 검색"),
    ("settings", "gear", "설정"),
    ("startup", "sliders", "시작 프로그램"),
]

_PLACEHOLDER_SUBTITLE = "곧 추가될 예정입니다."


class MainWindow(QMainWindow):
    def __init__(self, start_page: Optional[str] = None):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setWindowTitle("Memory Master")
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)

        # App-wide, not just on self: a mouse press near the edge almost
        # always lands on some child widget (the results table, sidebar,
        # etc. all extend flush to the window's edges), and per-widget
        # event filters/overrides only ever see events already routed to
        # that specific widget - installing on the QApplication instead
        # means this sees every press before any widget does, regardless
        # of which one the OS would otherwise have delivered it to.
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

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
        # Falls back to the default rather than silently doing nothing on
        # an invalid/garbage value (e.g. a malformed --start-page).
        self.go_to_page(start_page if start_page in self._page_indices else "dashboard")

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
        if page_id == "search":
            return SearchPage()
        if page_id == "settings":
            return SettingsPage()
        if page_id == "startup":
            return StartupManagerPage()
        return PlaceholderPage(icon_name, tooltip, _PLACEHOLDER_SUBTITLE)

    def go_to_page(self, page_id: str) -> None:
        index = self._page_indices.get(page_id)
        if index is not None:
            self._stack.setCurrentIndex(index)
            self._sidebar.set_current(page_id)

    def eventFilter(self, watched, event) -> bool:
        if (
            event.type() == QEvent.MouseButtonPress
            and event.button() == Qt.LeftButton
            and not self.isMaximized()
            and isinstance(watched, QWidget)
            and watched.window() is self
        ):
            # watched.window() is self: only react to presses on a widget
            # that's actually part of *this* window's own tree - an
            # app-wide filter also sees events for every other top-level
            # window (dialogs, popups, menus), which must never trigger a
            # resize of this one just because their screen position happens
            # to overlap it.
            edges = self._resize_edges_at(event.globalPos())
            if edges is not None:
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemResize(edges)
                    return True
        return super().eventFilter(watched, event)

    def _resize_edges_at(self, global_pos):
        """Qt.Edges the given global screen position is within
        _RESIZE_MARGIN of, or None if it's not near any edge of this
        window. Works from global (screen) coordinates rather than any
        child widget's own local ones, since the child under the cursor at
        the true window edge could be anything (the results table, a
        button, ...) - none of them need to cooperate or leave a margin for
        this to still correctly detect "near this window's actual edge".
        """
        local = self.mapFromGlobal(global_pos)
        if not self.rect().contains(local):
            return None  # belongs to a different top-level window

        left = local.x() <= _RESIZE_MARGIN
        right = local.x() >= self.width() - _RESIZE_MARGIN
        top = local.y() <= _RESIZE_MARGIN
        bottom = local.y() >= self.height() - _RESIZE_MARGIN
        if not (left or right or top or bottom):
            return None

        edges = Qt.Edges()
        if left:
            edges |= Qt.LeftEdge
        if right:
            edges |= Qt.RightEdge
        if top:
            edges |= Qt.TopEdge
        if bottom:
            edges |= Qt.BottomEdge
        return edges

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
