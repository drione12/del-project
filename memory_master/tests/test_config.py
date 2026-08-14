import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.config import AppConfig, load_config, save_config  # noqa: E402


def test_load_missing_file_returns_defaults():
    with tempfile.TemporaryDirectory() as tmp:
        config = load_config(os.path.join(tmp, "does_not_exist.json"))
        assert config == AppConfig()


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.json")
        original = AppConfig(
            theme="light",
            always_on_top=True,
            opacity=0.8,
            quarantine_dir="/some/path",
            search_folders=["/home/user/Documents", "/home/user/Downloads"],
        )

        assert save_config(original, path)
        loaded = load_config(path)

        assert loaded == original


def test_load_config_without_search_folders_key_defaults_to_empty_list():
    """A config.json saved before this field existed has no
    "search_folders" key at all - must not crash, must default to [].
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.json")
        with open(path, "w") as f:
            f.write('{"theme": "light"}')

        assert load_config(path).search_folders == []


def test_load_corrupt_file_returns_defaults():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.json")
        with open(path, "w") as f:
            f.write("{not valid json")
        assert load_config(path) == AppConfig()


def test_clamped_rejects_invalid_theme():
    config = AppConfig(theme="rainbow").clamped()
    assert config.theme == "dark"


def test_clamped_bounds_opacity():
    assert AppConfig(opacity=0.0).clamped().opacity == 0.3
    assert AppConfig(opacity=5.0).clamped().opacity == 1.0
    assert AppConfig(opacity=0.5).clamped().opacity == 0.5


def test_save_applies_clamping():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.json")
        save_config(AppConfig(theme="bogus", opacity=99.0), path)
        loaded = load_config(path)
        assert loaded.theme == "dark"
        assert loaded.opacity == 1.0
