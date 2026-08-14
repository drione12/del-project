import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.file_search import FileEntry, build_index, search  # noqa: E402


def _entry(name, path, is_dir=False):
    return FileEntry(name=name, path=path, size_bytes=0, modified_at=0.0, is_dir=is_dir)


def test_build_index_finds_nested_files():
    with tempfile.TemporaryDirectory() as tmp:
        nested = os.path.join(tmp, "sub")
        os.makedirs(nested)
        with open(os.path.join(tmp, "a.txt"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(nested, "b.txt"), "wb") as f:
            f.write(b"yy")

        names = {e.name for e in build_index([tmp])}

        assert "a.txt" in names
        assert "b.txt" in names


def test_build_index_includes_directories_with_zero_size():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "sub"))

        entries = build_index([tmp])
        dirs = [e for e in entries if e.is_dir]

        assert len(dirs) == 1
        assert dirs[0].name == "sub"
        assert dirs[0].size_bytes == 0


def test_build_index_file_has_correct_size():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "a.txt"), "wb") as f:
            f.write(b"x" * 123)

        entries = build_index([tmp])

        assert entries[0].size_bytes == 123
        assert entries[0].is_dir is False


def test_build_index_dedupes_overlapping_roots():
    with tempfile.TemporaryDirectory() as tmp:
        nested = os.path.join(tmp, "nested")
        os.makedirs(nested)
        with open(os.path.join(nested, "a.txt"), "wb") as f:
            f.write(b"x")

        entries = build_index([tmp, nested])
        matching = [e for e in entries if e.name == "a.txt"]

        assert len(matching) == 1


def test_build_index_progress_callback_invoked():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "a.txt"), "wb") as f:
            f.write(b"x")

        calls = []
        build_index([tmp], on_progress=lambda i, n: calls.append((i, n)))

        assert calls == [(1, 1)]


def test_build_index_compute_total_false_reports_zero_total():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "a.txt"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(tmp, "b.txt"), "wb") as f:
            f.write(b"y")

        calls = []
        entries = build_index([tmp], on_progress=lambda i, n: calls.append((i, n)), compute_total=False)

        assert len(entries) == 2
        assert calls == [(1, 0), (2, 0)]


def test_build_index_respects_cancel():
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(10):
            with open(os.path.join(tmp, f"f{i}.txt"), "wb") as f:
                f.write(b"x")

        calls = {"n": 0}

        def should_cancel():
            calls["n"] += 1
            return calls["n"] > 2

        entries = build_index([tmp], should_cancel=should_cancel)

        assert len(entries) < 10


def test_search_case_insensitive_name_match():
    index = [_entry("Report.pdf", "/docs/Report.pdf"), _entry("notes.txt", "/docs/notes.txt")]

    results = search(index, "report")

    assert [e.name for e in results] == ["Report.pdf"]


def test_search_matches_path_when_match_path_true():
    index = [_entry("a.txt", "/projects/secret/a.txt"), _entry("b.txt", "/projects/public/b.txt")]

    results = search(index, "secret")

    assert [e.name for e in results] == ["a.txt"]


def test_search_does_not_match_path_when_match_path_false():
    index = [_entry("a.txt", "/projects/secret/a.txt")]

    results = search(index, "secret", match_path=False)

    assert results == []


def test_search_requires_all_terms():
    index = [_entry("annual report.pdf", "/docs/annual report.pdf"), _entry("report.pdf", "/docs/report.pdf")]

    results = search(index, "annual report")

    assert [e.name for e in results] == ["annual report.pdf"]


def test_search_empty_query_returns_everything():
    index = [_entry("a.txt", "/a.txt"), _entry("b.txt", "/b.txt")]

    assert search(index, "") == index


def test_search_no_match_returns_empty_list():
    index = [_entry("a.txt", "/a.txt")]

    assert search(index, "zzz") == []
