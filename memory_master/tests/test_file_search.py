import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.file_search import CATEGORIES, FileEntry, build_index, matches_category, search  # noqa: E402


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


def test_build_index_populates_created_and_accessed_times():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "a.txt"), "wb") as f:
            f.write(b"x")

        entry = build_index([tmp])[0]

        # A just-created file's created/accessed times should be real,
        # recent (nonzero) timestamps, not the 0.0 "unknown"/os.stat()-failed
        # sentinel - the actual moment-in-time value isn't asserted since
        # this only needs to confirm the fields are wired up, not pin down
        # filesystem timestamp precision.
        assert entry.created_at > 0
        assert entry.accessed_at > 0


def test_build_index_attributes_defaults_safely_off_windows():
    # st_file_attributes only exists on Windows (os.stat_result docs) -
    # this suite only ever runs off Windows (see memory_master/README.md),
    # so this confirms the getattr(..., 0) fallback doesn't raise here,
    # not any particular real attribute value.
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "a.txt"), "wb") as f:
            f.write(b"x")

        entry = build_index([tmp])[0]

        assert entry.attributes == 0


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


def test_matches_category_all_matches_everything():
    assert matches_category(_entry("a.txt", "/a.txt"), "all")
    assert matches_category(_entry("dir", "/dir", is_dir=True), "all")


def test_matches_category_music():
    assert matches_category(_entry("song.mp3", "/song.mp3"), "music")
    assert not matches_category(_entry("song.mp3", "/song.mp3"), "video")


def test_matches_category_archive_multi_dot_name_uses_last_extension():
    assert matches_category(_entry("backup.tar.gz", "/backup.tar.gz"), "archive")
    assert not matches_category(_entry("backup.tar.gz", "/backup.tar.gz"), "document")


def test_matches_category_document():
    assert matches_category(_entry("report.docx", "/report.docx"), "document")


def test_matches_category_executable():
    assert matches_category(_entry("setup.exe", "/setup.exe"), "executable")


def test_matches_category_image_is_broader_than_preview_gate():
    # core.image_scanner.is_image_file (preview-gating) intentionally only
    # covers formats QPixmap can decode - the category filter is broader.
    assert matches_category(_entry("photo.heic", "/photo.heic"), "image")


def test_matches_category_video():
    assert matches_category(_entry("clip.mkv", "/clip.mkv"), "video")


def test_matches_category_is_case_insensitive():
    assert matches_category(_entry("PHOTO.JPG", "/PHOTO.JPG"), "image")


def test_matches_category_folder():
    folder = _entry("Documents", "/Documents", is_dir=True)
    assert matches_category(folder, "folder")
    assert matches_category(folder, "all")  # "all" still matches folders too
    assert not matches_category(folder, "music")  # folders never match a type category


def test_matches_category_folder_named_like_another_category_still_only_matches_folder():
    fake_archive_dir = _entry("archive.zip", "/archive.zip", is_dir=True)
    assert matches_category(fake_archive_dir, "folder")
    assert not matches_category(fake_archive_dir, "archive")


def test_matches_category_no_extension_matches_no_type_category():
    entry = _entry("README", "/README")
    for category in CATEGORIES:
        if category == "all":
            assert matches_category(entry, category)
        else:
            assert not matches_category(entry, category)


def test_matches_category_leading_dot_name_matches_windows_native_semantics():
    # Windows (and src/query.cpp's plain find_last_of('.')) has no Unix-style
    # "dotfile" convention - to it, ".gitignore" is a nameless file with a
    # ".gitignore" extension. This intentionally does NOT use
    # os.path.splitext()'s Unix-flavored leading-dot handling, so this stays
    # consistent with the separate C++ EverythingClone's own behavior.
    assert matches_category(_entry(".mp3", "/.mp3"), "music")
    assert not matches_category(_entry(".gitignore", "/.gitignore"), "document")
