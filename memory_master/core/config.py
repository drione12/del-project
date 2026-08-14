"""General app configuration - theme, always-on-top, window opacity,
quarantine folder location. Persisted to
%LOCALAPPDATA%\\MemoryMaster\\config.json (or ~/.cache/MemoryMaster/config.json
off Windows, so this stays testable here) - not relative to the current
working directory, which is where both reference scripts stored their own
config.json and which breaks depending on what directory the app happens
to be launched from (especially once packaged, or launched via a shortcut
with a different working directory).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from core.logging_setup import get_logger

logger = get_logger(__name__)

_VALID_THEMES = ("dark", "light")
_MIN_OPACITY = 0.3
_MAX_OPACITY = 1.0


def _config_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return os.path.join(base, "MemoryMaster")


def config_path() -> str:
    return os.path.join(_config_dir(), "config.json")


@dataclass
class AppConfig:
    theme: str = "dark"
    always_on_top: bool = False
    opacity: float = 1.0
    quarantine_dir: Optional[str] = None  # None = core/quarantine.py's default
    search_folders: List[str] = field(default_factory=list)  # 파일 검색 page's folder list

    def clamped(self) -> "AppConfig":
        theme = self.theme if self.theme in _VALID_THEMES else "dark"
        opacity = max(_MIN_OPACITY, min(_MAX_OPACITY, self.opacity))
        return AppConfig(
            theme=theme,
            always_on_top=self.always_on_top,
            opacity=opacity,
            quarantine_dir=self.quarantine_dir,
            search_folders=list(self.search_folders),
        )


def load_config(path: Optional[str] = None) -> AppConfig:
    path = path or config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AppConfig(
            theme=data.get("theme", "dark"),
            always_on_top=bool(data.get("always_on_top", False)),
            opacity=float(data.get("opacity", 1.0)),
            quarantine_dir=data.get("quarantine_dir"),
            search_folders=list(data.get("search_folders", [])),
        ).clamped()
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return AppConfig()


def save_config(config: AppConfig, path: Optional[str] = None) -> bool:
    path = path or config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(config.clamped()), f, indent=2)
        return True
    except OSError:
        logger.warning("failed to save config to %s", path, exc_info=True)
        return False
