"""Settings page - theme (dark/light), always-on-top, window opacity, and
quarantine folder location. Applies changes live wherever that's
straightforward from here (theme stylesheet, window flags/opacity), and
persists everything via core/config.py.
"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.config import load_config, save_config
from core.quarantine import default_quarantine_dir

_RESOURCES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "resources")


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 13px; font-weight: 600; color: #8c92a4;")
    return label


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._config = load_config()

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

        root.addWidget(self._build_theme_section())
        root.addWidget(self._build_window_section())
        root.addWidget(self._build_quarantine_section())
        root.addStretch(1)

    def _build_theme_section(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.addWidget(_section_title("테마"))
        row = QHBoxLayout()
        self._dark_radio = QRadioButton("다크")
        self._light_radio = QRadioButton("라이트")
        group = QButtonGroup(self)
        group.addButton(self._dark_radio)
        group.addButton(self._light_radio)
        (self._dark_radio if self._config.theme == "dark" else self._light_radio).setChecked(True)
        self._dark_radio.toggled.connect(lambda checked: checked and self._set_theme("dark"))
        self._light_radio.toggled.connect(lambda checked: checked and self._set_theme("light"))
        row.addWidget(self._dark_radio)
        row.addWidget(self._light_radio)
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    def _build_window_section(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.addWidget(_section_title("창"))

        self._always_on_top_check = QCheckBox("항상 위에 표시")
        self._always_on_top_check.setChecked(self._config.always_on_top)
        self._always_on_top_check.toggled.connect(self._set_always_on_top)
        layout.addWidget(self._always_on_top_check)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("투명도"))
        self._opacity_slider = QSlider(Qt.Horizontal)
        self._opacity_slider.setRange(30, 100)
        self._opacity_slider.setValue(int(self._config.opacity * 100))
        self._opacity_slider.valueChanged.connect(self._set_opacity)
        opacity_row.addWidget(self._opacity_slider, 1)
        self._opacity_label = QLabel(f"{int(self._config.opacity * 100)}%")
        opacity_row.addWidget(self._opacity_label)
        layout.addLayout(opacity_row)
        return frame

    def _build_quarantine_section(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.addWidget(_section_title("격리 폴더 위치"))
        row = QHBoxLayout()
        self._quarantine_label = QLabel(self._config.quarantine_dir or default_quarantine_dir())
        self._quarantine_label.setWordWrap(True)
        row.addWidget(self._quarantine_label, 1)
        browse_btn = QPushButton("변경...")
        browse_btn.clicked.connect(self._browse_quarantine_dir)
        row.addWidget(browse_btn)
        layout.addLayout(row)
        return frame

    def _set_theme(self, theme: str) -> None:
        self._config.theme = theme
        save_config(self._config)
        self._apply_theme_live(theme)

    @staticmethod
    def _apply_theme_live(theme: str) -> None:
        app = QApplication.instance()
        if app is None:
            return
        qss_name = "theme.qss" if theme == "dark" else "theme_light.qss"
        try:
            with open(os.path.join(_RESOURCES_DIR, qss_name), "r", encoding="utf-8") as f:
                app.setStyleSheet(f.read())
        except OSError:
            pass

    def _set_always_on_top(self, checked: bool) -> None:
        self._config.always_on_top = checked
        save_config(self._config)
        window = self.window()
        if window is not None:
            was_visible = window.isVisible()
            window.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
            if was_visible:
                window.show()

    def _set_opacity(self, value: int) -> None:
        opacity = value / 100.0
        self._config.opacity = opacity
        self._opacity_label.setText(f"{value}%")
        save_config(self._config)
        window = self.window()
        if window is not None:
            window.setWindowOpacity(opacity)

    def _browse_quarantine_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "격리 폴더 선택")
        if folder:
            self._apply_quarantine_dir(folder)

    def _apply_quarantine_dir(self, folder: str) -> None:
        self._config.quarantine_dir = folder
        self._quarantine_label.setText(folder)
        save_config(self._config)
