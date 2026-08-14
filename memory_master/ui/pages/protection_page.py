"""Protection page - process whitelist/blacklist with a background
auto-kill watchdog, plus a read-only view of the hard-coded protected-path
denylist (core/path_guard.py's PROTECTED_ROOTS - this must stay read-only,
it's the actual safety backstop, not something a UI bug or a user typo
could accidentally weaken) alongside a user-addable "also protect these"
list for anything extra someone wants covered.
"""
from __future__ import annotations

import queue
from typing import List, Set

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.path_guard import PROTECTED_ROOTS
from core.process_lists import BlacklistWatchdog, load_process_lists, save_process_lists


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 13px; font-weight: 600; color: #8c92a4;")
    return label


class ProtectionPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._lists = load_process_lists()
        self._extra_protected_paths: List[str] = []
        self._kill_events: "queue.Queue" = queue.Queue()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        root.addWidget(self._build_whitelist_section())
        root.addWidget(self._build_blacklist_section())
        root.addWidget(self._build_protected_paths_section())
        root.addWidget(self._build_activity_log())
        root.addStretch(1)

        self._watchdog = BlacklistWatchdog(get_lists=lambda: self._lists, on_kill=self._on_kill_from_watchdog)
        self._watchdog.start()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._drain_kill_events)
        self._poll_timer.start(1000)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    # -- whitelist / blacklist --------------------------------------------

    def _build_whitelist_section(self) -> QFrame:
        frame, layout, self._whitelist_widget = self._build_name_list_section(
            "화이트리스트 (강제 삭제 대상에서 항상 제외)", self._lists.whitelist
        )
        add_btn, remove_btn = self._list_buttons(layout)
        add_btn.clicked.connect(lambda: self._add_name(self._lists.whitelist, self._whitelist_widget))
        remove_btn.clicked.connect(lambda: self._remove_selected(self._lists.whitelist, self._whitelist_widget))
        return frame

    def _build_blacklist_section(self) -> QFrame:
        frame, layout, self._blacklist_widget = self._build_name_list_section(
            "블랙리스트 (실행되는 즉시 자동 종료)", self._lists.blacklist
        )
        add_btn, remove_btn = self._list_buttons(layout)
        add_btn.clicked.connect(lambda: self._add_name(self._lists.blacklist, self._blacklist_widget))
        remove_btn.clicked.connect(lambda: self._remove_selected(self._lists.blacklist, self._blacklist_widget))
        return frame

    @staticmethod
    def _build_name_list_section(title: str, names: Set[str]):
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.addWidget(_section_title(title))
        list_widget = QListWidget()
        list_widget.setMaximumHeight(140)
        list_widget.addItems(sorted(names))
        layout.addWidget(list_widget)
        return frame, layout, list_widget

    @staticmethod
    def _list_buttons(layout: QVBoxLayout):
        row = QHBoxLayout()
        add_btn = QPushButton("추가...")
        remove_btn = QPushButton("선택 항목 제거")
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return add_btn, remove_btn

    def _add_name(self, target_set: Set[str], list_widget: QListWidget) -> None:
        name, ok = QInputDialog.getText(self, "프로세스 이름 추가", "프로세스 이름 (예: notepad.exe):")
        if ok:
            self._apply_add_name(target_set, list_widget, name)

    def _apply_add_name(self, target_set: Set[str], list_widget: QListWidget, name: str) -> None:
        normalized = name.strip().lower()
        if not normalized:
            return
        target_set.add(normalized)
        list_widget.clear()
        list_widget.addItems(sorted(target_set))
        save_process_lists(self._lists)

    def _remove_selected(self, target_set: Set[str], list_widget: QListWidget) -> None:
        for item in list_widget.selectedItems():
            target_set.discard(item.text())
        list_widget.clear()
        list_widget.addItems(sorted(target_set))
        save_process_lists(self._lists)

    # -- protected paths ----------------------------------------------------

    def _build_protected_paths_section(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.addWidget(_section_title("핵심 보호 경로 (읽기 전용)"))
        core_list = QListWidget()
        core_list.setMaximumHeight(120)
        core_list.addItems([root for root in PROTECTED_ROOTS if root])
        layout.addWidget(core_list)

        layout.addWidget(_section_title("추가 보호 경로"))
        self._extra_paths_widget = QListWidget()
        self._extra_paths_widget.setMaximumHeight(100)
        layout.addWidget(self._extra_paths_widget)
        row = QHBoxLayout()
        add_btn = QPushButton("경로 추가...")
        add_btn.clicked.connect(self._add_extra_protected_path)
        remove_btn = QPushButton("선택 항목 제거")
        remove_btn.clicked.connect(self._remove_extra_protected_path)
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    def _add_extra_protected_path(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "보호할 폴더 선택")
        if path:
            self._apply_add_extra_protected_path(path)

    def _apply_add_extra_protected_path(self, path: str) -> None:
        if path and path not in self._extra_protected_paths:
            self._extra_protected_paths.append(path)
            self._extra_paths_widget.addItem(path)

    def _remove_extra_protected_path(self) -> None:
        for item in self._extra_paths_widget.selectedItems():
            if item.text() in self._extra_protected_paths:
                self._extra_protected_paths.remove(item.text())
            self._extra_paths_widget.takeItem(self._extra_paths_widget.row(item))

    # -- activity log -------------------------------------------------------

    def _build_activity_log(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.addWidget(_section_title("차단 활동 기록"))
        self._activity_list = QListWidget()
        self._activity_list.setMaximumHeight(140)
        layout.addWidget(self._activity_list)
        return frame

    def _on_kill_from_watchdog(self, name: str, pid: int) -> None:
        # Called from the watchdog's background thread - must not touch
        # Qt widgets directly here. Queue.Queue is thread-safe; draining
        # it is left to _drain_kill_events, which a UI-thread QTimer
        # drives instead.
        self._kill_events.put((name, pid))

    def _drain_kill_events(self) -> None:
        while True:
            try:
                name, pid = self._kill_events.get_nowait()
            except queue.Empty:
                break
            self._activity_list.insertItem(0, f"{name} (PID {pid}) 자동 종료됨")

    def stop(self) -> None:
        self._watchdog.stop()
        self._poll_timer.stop()
