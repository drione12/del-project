"""Resolves paths to files under memory_master/resources/ correctly both
when running from source and when running from a PyInstaller --onefile
bundle. PyInstaller extracts bundled data files to a temporary directory
at startup (exposed as sys._MEIPASS) rather than keeping the same
relative layout the source tree has, so a plain __file__-relative path -
what every resource lookup in this app used before this existed - would
silently break the moment the app is packaged. Centralized here instead
of repeated (and risking a mismatch) across every file that needs an
icon or a stylesheet.
"""
from __future__ import annotations

import os
import sys


def resource_path(*parts: str) -> str:
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir is not None:
        return os.path.join(bundle_dir, "resources", *parts)
    memory_master_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(memory_master_dir, "resources", *parts)
