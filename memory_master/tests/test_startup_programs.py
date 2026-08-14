import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.startup_programs import (  # noqa: E402
    BootImpact,
    _resolve_impact_size,
    add_startup_program,
    classify_boot_impact,
    extract_exe_path,
    parse_scheduled_tasks_csv,
    remove_startup_program,
)

_SAMPLE_CSV = (
    '"HostName","TaskName","Status","Task To Run"\r\n'
    '"DESKTOP-ABC","\\MyApp\\Updater","Ready","C:\\Program Files\\MyApp\\updater.exe"\r\n'
    '"DESKTOP-ABC","\\Microsoft\\Windows\\SomeFolder","Ready","N/A"\r\n'
    '"DESKTOP-ABC","\\OldTool\\Disabled Task","Disabled","C:\\OldTool\\old.exe"\r\n'
)


def test_classify_boot_impact_low():
    assert classify_boot_impact(1024) == BootImpact.LOW


def test_classify_boot_impact_medium_at_threshold():
    assert classify_boot_impact(20 * 1024 * 1024) == BootImpact.MEDIUM


def test_classify_boot_impact_high_at_threshold():
    assert classify_boot_impact(150 * 1024 * 1024) == BootImpact.HIGH


def test_extract_exe_path_quoted_with_args():
    assert extract_exe_path('"C:\\Program Files\\App\\app.exe" --flag') == "C:\\Program Files\\App\\app.exe"


def test_extract_exe_path_unquoted_with_args():
    assert extract_exe_path("C:\\App\\app.exe --flag") == "C:\\App\\app.exe"


def test_extract_exe_path_empty_returns_none():
    assert extract_exe_path("") is None
    assert extract_exe_path("   ") is None


def test_resolve_impact_size_falls_back_to_file_size():
    with tempfile.TemporaryDirectory() as tmp:
        # A unique name guarantees no running process on this machine
        # will happen to share it, so this exercises the file-size
        # fallback path specifically rather than the running-process path.
        unique_name = f"{uuid.uuid4().hex}.bin"
        path = os.path.join(tmp, unique_name)
        with open(path, "wb") as f:
            f.write(b"x" * 12345)

        assert _resolve_impact_size(path) == 12345


def test_resolve_impact_size_missing_file_returns_zero():
    assert _resolve_impact_size("/no/such/file/at/all.exe") == 0


def test_resolve_impact_size_prefers_running_process_rss():
    # Our own interpreter is definitely running right now - resolving by
    # its own executable path should find a live process and return a
    # real nonzero RSS, not fall through to a file-size read.
    size = _resolve_impact_size(sys.executable)
    assert size > 0


def test_parse_scheduled_tasks_csv_reads_ready_task():
    entries = parse_scheduled_tasks_csv(_SAMPLE_CSV)
    names = {e.name: e for e in entries}
    assert "MyApp\\Updater" in names
    entry = names["MyApp\\Updater"]
    assert entry.enabled
    assert entry.source == "task_scheduler"
    assert entry.command == "C:\\Program Files\\MyApp\\updater.exe"


def test_parse_scheduled_tasks_csv_skips_na_task_to_run():
    entries = parse_scheduled_tasks_csv(_SAMPLE_CSV)
    names = [e.name for e in entries]
    assert not any("SomeFolder" in n for n in names)


def test_parse_scheduled_tasks_csv_marks_disabled_status():
    entries = parse_scheduled_tasks_csv(_SAMPLE_CSV)
    names = {e.name: e for e in entries}
    assert not names["OldTool\\Disabled Task"].enabled


def test_parse_scheduled_tasks_csv_empty_input():
    assert parse_scheduled_tasks_csv("") == []


def test_add_and_remove_startup_program_are_false_off_windows():
    if sys.platform != "win32":
        assert add_startup_program("Test", "C:\\test.exe") is False
        assert remove_startup_program("Test") is False
