import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.resource_path import resource_path  # noqa: E402


def test_resource_path_without_meipass_resolves_to_a_real_file(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    path = resource_path("theme.qss")
    assert path.endswith(os.path.join("resources", "theme.qss"))
    assert os.path.exists(path)


def test_resource_path_without_meipass_supports_nested_parts(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    path = resource_path("icons", "search.svg")
    assert path.endswith(os.path.join("resources", "icons", "search.svg"))
    assert os.path.exists(path)


def test_resource_path_with_meipass_uses_bundle_layout(monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", os.path.join("fake", "bundle", "dir"), raising=False)
    path = resource_path("icons", "search.svg")
    assert path == os.path.join("fake", "bundle", "dir", "resources", "icons", "search.svg")
