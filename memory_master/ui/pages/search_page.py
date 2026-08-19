"""File search page ("파일 검색") - one QWidget (SearchPage) whose UI never
changes, but whose indexing/search backend does: FastSearchEngine
(core/fast_search.py, backed by EverythingCore.dll - see src/core_api.h,
the same NTFS MFT index + USN journal watcher EverythingClone.exe uses) in
this same process when elevated and the DLL is available, else
core/file_search.py's slower from-scratch Python os.walk index otherwise.

Used to be two entirely separate view classes - an embedded native
EverythingClone.exe child window (spawned as a subprocess, its window
parented into this page via core/everything_embed.py's ctypes helpers) vs.
this page's own from-scratch Python search - picked by a thin coordinator.
Collapsed into this one class now that the fast path also renders through
this same QTableView-based UI instead of hosting a second process's own
window: EverythingClone's engine runs in-process via ctypes now, so there's
no second window to host, no subprocess to manage, and no UIPI concern
(Windows blocking/degrading window-parenting across different integrity
levels, since EverythingClone needs admin and this app doesn't always run
elevated) - that risk simply doesn't exist anymore because there's no
cross-process window parenting happening at all. See memory_master/
README.md for more on why this changed.

Delete/force-delete reuses the same hardened core/force_delete.py pipeline
as the Cleanup page's drop zone (single selection reuses that exact
analyze/confirm/execute flow, multi-selection uses a batch flow with one
confirmation and no kill-list, mirroring
ui/dialogs/duplicate_image_manager.py's existing batch-delete precedent) -
identical for both backends, since it only ever operates on a FileEntry's
path, not on how that FileEntry was found.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, List, Optional, Tuple

import psutil
from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QThread, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QImage, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.elevation import is_running_as_admin, request_admin_restart
from core.fast_search import FastSearchEngine
from core.fast_search import is_available as fast_search_available
from core.file_search import CATEGORIES, FileEntry, build_index, is_video_file, matches_category
from core.file_search import search as search_entries
from core.force_delete import AnalyzeResult, ExecuteOptions, ExecuteResult, execute
from core.formatting import format_attributes, format_bytes, format_datetime, format_extension
from core.image_scanner import is_image_file
from core.logging_setup import get_logger
from core.trash import send_to_trash
from core.workers import AnalyzeWorker, ExecuteWorker
from ui.dialogs.confirm_force_delete import ConfirmForceDeleteDialog

logger = get_logger(__name__)

if sys.platform == "win32":
    from core.icons import get_icon_for_path
else:
    def get_icon_for_path(path: str, is_dir: bool = False):
        return None

_COL_NAME = 0
_COL_PATH = 1
_COL_SIZE = 2
_COL_MODIFIED = 3
_COL_CREATED = 4
_COL_ACCESSED = 5
_COL_EXTENSION = 6
_COL_ATTRIBUTES = 7

_MAX_DISPLAYED_RESULTS = 2000
_SEARCH_DEBOUNCE_MS = 250
_PREVIEW_PLACEHOLDER_TEXT = "이미지나 동영상을 선택하면\n미리보기가 표시됩니다"
_PREVIEW_LOADING_TEXT = "미리보기 불러오는 중..."
_PREVIEW_VIDEO_FAILED_TEXT = "미리보기를 불러올 수 없습니다"

# (category, label) pairs for the category filter dropdown, in display
# order - same categories/order as CATEGORIES (core/file_search.py) and the
# separate C++ EverythingClone's own filter combo box (src/main.cpp). This
# page and EverythingClone are meant to stay in sync as EverythingClone's
# own feature set evolves (see memory_master/README.md), not just for this
# one filter.
_CATEGORY_OPTIONS = (
    ("all", "전체"),
    ("music", "음악"),
    ("archive", "압축파일"),
    ("document", "문서"),
    ("executable", "실행파일"),
    ("folder", "폴더"),
    ("image", "이미지"),
    ("video", "비디오"),
)
# A typo'd category here wouldn't raise at runtime - matches_category falls
# back to "matches everything" for an unrecognized category (mirroring
# src/query.cpp's own default: case) - so this would otherwise fail silently
# instead of loudly. Catches the mismatch immediately at import time instead.
assert {category for category, _label in _CATEGORY_OPTIONS} == set(CATEGORIES)

_BATCH_FORCE_DELETE_OPTIONS = ExecuteOptions(
    kill_locking_processes=False,
    take_ownership_on_failure=True,
    secure_shred=False,
    reboot_delete_fallback=True,
)


def _fixed_drive_roots() -> List[str]:
    """Every currently-mounted drive except optical media (an empty
    CD/DVD drive can hang or error on enumeration, and isn't useful to
    index anyway) - this is what "그냥 모든 파일 폴더 보여주는 식" (just show
    everything) resolves to: no folder picker, whole-machine coverage.
    Only used by the slow os.walk backend - the fast backend detects fixed
    NTFS volumes itself (src/volume_utils.cpp's DetectNtfsFixedDrives).
    """
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception:
        return []
    roots = []
    for part in partitions:
        opts = part.opts.split(",") if part.opts else []
        if "cdrom" in opts:
            continue
        roots.append(part.mountpoint)
    return roots


def _column_text(entry: FileEntry, col: int) -> str:
    if col == _COL_NAME:
        return entry.name
    if col == _COL_PATH:
        return entry.path
    if col == _COL_SIZE:
        return "-" if entry.is_dir else format_bytes(entry.size_bytes)
    if col == _COL_MODIFIED:
        return format_datetime(entry.modified_at)
    if col == _COL_CREATED:
        return format_datetime(entry.created_at)
    if col == _COL_ACCESSED:
        return format_datetime(entry.accessed_at)
    if col == _COL_EXTENSION:
        return format_extension(entry.name, entry.is_dir)
    if col == _COL_ATTRIBUTES:
        return format_attributes(entry.attributes)
    return ""


class _ResultsTableModel(QAbstractTableModel):
    """Virtualized backing store for _ResultsTable (QTableView) - Qt only
    ever calls data()/headerData() for cells actually on screen (plus a
    small readahead), unlike the old QTableWidget-based _render_results
    which eagerly built one QTableWidgetItem per cell for every row up
    front. That eager construction was the main UI-lag source once result
    counts got large (large index -> large result set -> thousands of
    QTableWidgetItem objects built and laid out synchronously) - this model
    only ever materializes what's actually visible.
    """

    _HEADERS = ["이름", "경로", "크기", "수정한 날짜", "생성한 날짜", "액세스한 날짜", "확장자", "속성"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: List[FileEntry] = []

    def set_entries(self, entries: List[FileEntry]) -> None:
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()

    def entries(self) -> List[FileEntry]:
        return self._entries

    def entry_at(self, row: int) -> Optional[FileEntry]:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._HEADERS)

    def headerData(self, section: int, orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return self._HEADERS[section]
            return None
        # Falls back to QAbstractItemModel's own default (1-based row
        # numbers) for the vertical header, same as QTableWidget showed
        # before - only the horizontal (column) headers are ever customized.
        return super().headerData(section, orientation, role)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        entry = self._entries[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            return _column_text(entry, col)
        if role == Qt.DecorationRole and col == _COL_NAME:
            pixmap = get_icon_for_path(entry.path, entry.is_dir)
            return QIcon(pixmap) if pixmap is not None else None
        return None


class _ResultsTable(QTableView):
    """Adds Del-key support on top of QTableView - there is no existing
    precedent for this anywhere else in the app, so it's kept local rather
    than added as a generic shared widget for a single consumer.
    """

    deleteRequested = pyqtSignal()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Delete:
            self.deleteRequested.emit()
        else:
            super().keyPressEvent(event)


class _PreviewLabel(QLabel):
    """Keeps the original, full-resolution QPixmap it was last given and
    rescales *from that* (not from whatever's currently displayed) on every
    resize - not just at selection time. Plain QLabel.setPixmap() only ever
    draws at whatever size it's called with, so without this, dragging the
    results/preview QSplitter handle after already selecting an image would
    leave the preview pinned at its old size instead of growing/shrinking
    live with the panel; rescaling from a re-shrunk copy on every resize
    would also visibly degrade quality each time the panel grows back.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original: Optional[QPixmap] = None

    def set_original_pixmap(self, pixmap: QPixmap) -> None:
        self._original = pixmap
        self._rescale()

    def clear_pixmap(self) -> None:
        self._original = None
        self.setPixmap(QPixmap())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._original is None or self._original.isNull():
            return
        box = self.size()
        if box.width() < 10 or box.height() < 10:
            # Layout may not have settled yet (e.g. right after the page's
            # very first show) - the label's own minimum size is a
            # reliable floor since it was set explicitly, not derived from
            # a layout pass that may not have run.
            box = self.minimumSize()
        self.setPixmap(self._original.scaled(box, Qt.KeepAspectRatio, Qt.SmoothTransformation))


def _extract_video_thumbnail(path: str) -> QPixmap:
    """First-frame thumbnail via OpenCV (already a dependency -
    core/image_scanner.py's ORB duplicate-image matching uses the same
    cv2 family) - QPixmap/QImageReader can't decode video containers at
    all, so this is the lightest way to get *something* to show for a
    video in the same preview panel images already use. Returns a null
    QPixmap if the file can't be opened/decoded (corrupt file, a codec
    OpenCV doesn't support, ...) - same "null = show a placeholder
    instead" convention the image path already relies on. Runs on a
    background thread (see _VideoThumbnailWorker) - opening a video
    container and decoding a frame is meaningfully slower than a plain
    image load, and unlike that already-synchronous path, doing this on
    the UI thread would freeze selection/browsing.
    """
    import cv2

    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            return QPixmap()
        ok, frame = cap.read()
        if not ok or frame is None:
            return QPixmap()
    finally:
        cap.release()

    # BGR (OpenCV's native channel order) -> RGB (what QImage expects) -
    # skipping this wouldn't crash, just silently swap red and blue.
    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    height, width, _channels = frame.shape
    bytes_per_line = 3 * width
    # .copy(): QImage wraps the raw buffer it's given rather than owning
    # it - without a copy, `frame` (this function's local numpy array)
    # could be garbage-collected while the QImage/QPixmap built from it is
    # still in use elsewhere, a real dangling-buffer risk, not a
    # theoretical one.
    image = QImage(frame.data, width, height, bytes_per_line, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(image)


class _VideoThumbnailWorker(QThread):
    """Runs _extract_video_thumbnail() off the UI thread. generation is
    echoed back unchanged, same reasoning as _SearchWorker's - the user
    can click through several videos before an earlier extraction
    finishes, and a slow one finishing last must not clobber whatever's
    actually selected by then.
    """

    thumbnailReady = pyqtSignal(QPixmap, int)  # possibly-null pixmap, generation

    def __init__(self, path: str, generation: int, parent=None):
        super().__init__(parent)
        self._path = path
        self._generation = generation

    def run(self) -> None:
        try:
            pixmap = _extract_video_thumbnail(self._path)
        except Exception:
            logger.exception("video thumbnail extraction failed for %s", self._path)
            pixmap = QPixmap()
        self.thumbnailReady.emit(pixmap, self._generation)


class _SearchWorker(QThread):
    """Runs one search off the UI thread so a slow query (large index, or
    the slow os.walk-backed fallback) can't freeze typing/scrolling.
    search_fn is a zero-arg closure that already captured its query/
    category/backend snapshot on the main thread at construction time (Qt
    widgets, and self._index/self._fast_engine, must not be touched from
    run(), which executes on this worker's own thread). generation is
    echoed back unchanged so SearchPage._on_search_result_ready can discard
    a result if a newer search was started before this one finished -
    debounce alone doesn't rule out overlap (e.g. a slow query still
    running when the next debounced search fires).
    """

    resultReady = pyqtSignal(list, str, int)  # List[FileEntry], status_text, generation

    def __init__(self, search_fn: Callable[[], Tuple[List[FileEntry], str]], generation: int, parent=None):
        super().__init__(parent)
        self._search_fn = search_fn
        self._generation = generation

    def run(self) -> None:
        try:
            results, status_text = self._search_fn()
        except Exception:
            # Must never let an exception escape a QThread.run() override -
            # there's no Python call stack on the other side of this call
            # for it to propagate into (Qt's C++ side invokes this
            # directly), so PyQt5 can silently abort the whole process
            # instead of raising anything catchable - and under a
            # PyInstaller --windowed build there's no console for even a
            # printed traceback to appear on, so it looks like the app just
            # vanished. Logging it here means a future failure at least
            # leaves a trace in
            # %LOCALAPPDATA%\MemoryMaster\logs\memory_master.log (see
            # core/logging_setup.py) instead of zero diagnostic information.
            logger.exception("search failed")
            self.resultReady.emit([], "검색 중 오류가 발생했습니다", self._generation)
            return
        self.resultReady.emit(results, status_text, self._generation)


class _IndexWorker(QThread):
    """Slow backend: builds core/file_search.py's os.walk-based index."""

    progress = pyqtSignal(int, int)
    resultReady = pyqtSignal(list)  # List[FileEntry]

    def __init__(self, roots: List[str], parent=None):
        super().__init__(parent)
        self._roots = roots
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        entries = build_index(
            self._roots,
            on_progress=lambda i, n: self.progress.emit(i, n),
            should_cancel=lambda: self._cancel_requested,
            compute_total=False,  # a whole-drive pre-count would double an already-long walk
        )
        self.resultReady.emit(entries)


class _FastIndexWorker(QThread):
    """Fast backend: runs FastSearchEngine.build_index() (blocking - a
    first-ever run with no saved snapshot to catch up from walks the whole
    MFT) off the UI thread, same reasoning as _IndexWorker above. No
    cancel() - EC_BuildIndex is one blocking C++ call with no cancellation
    hook exposed (see SearchPage.stop()'s docstring for how shutdown
    handles a build that's still running when the app closes).
    """

    resultReady = pyqtSignal(int)  # total indexed record count; 0 = not elevated

    def __init__(self, engine: FastSearchEngine, parent=None):
        super().__init__(parent)
        self._engine = engine

    def run(self) -> None:
        try:
            count = self._engine.build_index()
        except Exception:
            # Same reasoning as _SearchWorker.run() above - never let an
            # exception escape this override. Reports 0, which
            # _on_fast_index_ready already treats as "인덱싱 실패" - no new
            # error-display path needed.
            logger.exception("build_index failed")
            count = 0
        self.resultReady.emit(count)


class _TrashDeleteWorker(QThread):
    resultReady = pyqtSignal(list)  # List[Tuple[str, bool]]

    def __init__(self, paths: List[str], parent=None):
        super().__init__(parent)
        self._paths = paths
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        results = []
        for path in self._paths:
            if self._cancel_requested:
                break
            results.append((path, send_to_trash(path).ok))
        self.resultReady.emit(results)


class _BatchForceDeleteWorker(QThread):
    resultReady = pyqtSignal(list)  # List[Tuple[str, bool]]

    def __init__(self, paths: List[str], parent=None):
        super().__init__(parent)
        self._paths = paths
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        results = []
        for path in self._paths:
            if self._cancel_requested:
                break
            result = execute(path, kill_pids=[], options=_BATCH_FORCE_DELETE_OPTIONS)
            results.append((path, result.failed_count == 0))
        self.resultReady.emit(results)


class SearchPage(QWidget):
    """See the module docstring. Same class name/constructor signature as
    before the fast-engine merge, so nothing outside this file needed to
    change to keep using it.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._index: List[FileEntry] = []  # only populated/used by the slow backend
        self._worker: Optional[QThread] = None  # one at a time, mirrors CleanupPage
        self._search_worker: Optional[QThread] = None  # separate slot: can overlap self._worker
        self._search_generation = 0  # bumped per _apply_search() call; discards stale results
        self._video_thumbnail_worker: Optional[QThread] = None  # separate slot: can overlap either worker above
        self._preview_generation = 0  # bumped per _update_preview() call; discards stale thumbnails
        self._auto_indexed = False  # first showEvent kicks off indexing, not __init__
        self._active_category = "all"
        self._fast_engine: Optional[FastSearchEngine] = self._create_fast_engine()

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

        if self._fast_engine is None:
            root.addWidget(self._build_admin_banner())
        root.addWidget(self._build_index_section())
        root.addWidget(self._build_search_section(), 1)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(_SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._apply_search)

        # Required, not optional: the fast engine can own live USN-watcher
        # threads (and an unsaved index) running inside this process.
        # MainWindow.shutdown() is only ever called by the offscreen smoke
        # check, not a real run - a real app exit goes through the tray's
        # quit action calling QApplication.quit() directly, which fires
        # aboutToQuit. Without connecting to it here, every ordinary quit
        # would skip EC_SaveIndexes (full MFT rescan next launch) and leak
        # the DLL's background threads.
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    @staticmethod
    def _create_fast_engine() -> Optional[FastSearchEngine]:
        """None whenever the fast backend can't be used - not elevated,
        EverythingCore.dll missing (any dev/test environment, or an install
        that predates it), or present but failed to load for some other
        reason. Callers treat None as "use the slow os.walk backend
        instead", exactly like today's not-elevated case already works.
        """
        if not is_running_as_admin() or not fast_search_available():
            return None
        try:
            return FastSearchEngine()
        except OSError:
            return None

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._auto_indexed:
            self._auto_indexed = True
            self._start_indexing()

    # -- construction -------------------------------------------------------

    def _build_admin_banner(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QHBoxLayout(frame)
        label = QLabel(
            "빠른 검색(EverythingClone 엔진)을 쓰려면 관리자 권한이 필요합니다. 지금은 느린 기본 검색을 사용 중입니다."
        )
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        restart_btn = QPushButton("관리자 권한으로 다시 시작")
        restart_btn.setProperty("role", "primary")
        restart_btn.clicked.connect(self._on_restart_as_admin)
        layout.addWidget(restart_btn)
        return frame

    def _build_index_section(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.addWidget(_section_title("인덱스"))

        row = QHBoxLayout()
        self._reindex_btn = QPushButton("다시 인덱싱")
        self._reindex_btn.setProperty("role", "primary")
        self._reindex_btn.clicked.connect(self._start_indexing)
        row.addWidget(self._reindex_btn)
        row.addStretch(1)
        self._index_status_label = QLabel("")
        self._index_status_label.setStyleSheet("color: #8c92a4; font-size: 12px;")
        row.addWidget(self._index_status_label)
        layout.addLayout(row)
        return frame

    def _build_category_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.setEnabled(False)  # matches self._search_box - enabled together once indexed
        for category, label in _CATEGORY_OPTIONS:
            combo.addItem(label, category)
        combo.setCurrentIndex(0)  # 전체 selected by default
        self._category_combo = combo
        combo.currentIndexChanged.connect(self._on_category_selected)
        return combo

    def _on_category_selected(self, _index: int) -> None:
        self._active_category = self._category_combo.currentData()
        self._apply_search()

    def _build_search_section(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.addWidget(_section_title("검색"))

        search_row = QHBoxLayout()
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("인덱싱 중...")
        self._search_box.setEnabled(False)
        self._search_box.textChanged.connect(self._on_search_text_changed)
        search_row.addWidget(self._search_box, 1)
        search_row.addWidget(self._build_category_combo())
        layout.addLayout(search_row)

        self._results_status_label = QLabel("")
        self._results_status_label.setStyleSheet("color: #8c92a4; font-size: 12px;")
        layout.addWidget(self._results_status_label)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)  # dragging a handle to the far edge shouldn't hide a panel entirely
        self._results_table = self._build_results_table()
        splitter.addWidget(self._results_table)
        self._preview_label = self._build_preview_label()
        splitter.addWidget(self._preview_label)
        # Initial split only - same ~2:1 proportion the old fixed
        # QHBoxLayout stretch factors gave it, but now user-draggable via
        # the splitter handle instead of fixed for the page's lifetime.
        splitter.setSizes([700, 350])
        layout.addWidget(splitter, 1)
        return frame

    def _build_results_table(self) -> _ResultsTable:
        # 8 columns, same headers/order as the separate C++ EverythingClone's
        # own native ListView (src/main.cpp) - same reasoning as the
        # category filter dropdown: this page and EverythingClone's window
        # are meant to look/behave the same regardless of which backend is
        # actually running underneath (see the module docstring). Backed by
        # _ResultsTableModel (QAbstractTableModel) instead of QTableWidget
        # items so only visible rows are ever materialized - see that
        # class's docstring for why.
        table = _ResultsTable()
        self._results_model = _ResultsTableModel(self)
        table.setModel(self._results_model)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        header = table.horizontalHeader()
        header.setSectionResizeMode(_COL_NAME, QHeaderView.Interactive)
        header.setSectionResizeMode(_COL_PATH, QHeaderView.Stretch)
        header.setSectionResizeMode(_COL_SIZE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_MODIFIED, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_CREATED, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_ACCESSED, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_EXTENSION, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_ATTRIBUTES, QHeaderView.ResizeToContents)
        table.setColumnWidth(_COL_NAME, 300)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(self._show_context_menu)
        table.deleteRequested.connect(self._on_delete_requested)
        # setModel() must run before selectionModel() exists - connected
        # here rather than up front for that reason.
        table.selectionModel().selectionChanged.connect(lambda *_args: self._update_preview())
        return table

    @staticmethod
    def _build_preview_label() -> _PreviewLabel:
        label = _PreviewLabel(_PREVIEW_PLACEHOLDER_TEXT)
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumSize(280, 280)
        label.setWordWrap(True)
        label.setStyleSheet("background-color: #0f131d; border-radius: 4px; color: #8c92a4;")
        return label

    # -- indexing ---------------------------------------------------------

    def _start_indexing(self) -> None:
        if self._fast_engine is not None:
            self._reindex_btn.setEnabled(False)
            self._search_box.setEnabled(False)
            self._category_combo.setEnabled(False)
            self._results_model.set_entries([])
            self._index_status_label.setText("인덱싱 중...")

            worker = _FastIndexWorker(self._fast_engine, self)
            worker.resultReady.connect(self._on_fast_index_ready)
            worker.finished.connect(worker.deleteLater)
            self._worker = worker
            worker.start()
            return

        roots = _fixed_drive_roots()
        if not roots:
            QMessageBox.warning(self, "드라이브 없음", "검색 가능한 드라이브를 찾지 못했습니다.")
            return
        self._reindex_btn.setEnabled(False)
        self._search_box.setEnabled(False)
        self._category_combo.setEnabled(False)
        self._results_model.set_entries([])
        self._index_status_label.setText("인덱싱 중... (전체 드라이브, 다소 시간이 걸릴 수 있습니다)")

        worker = _IndexWorker(roots, self)
        worker.progress.connect(self._on_index_progress)
        worker.resultReady.connect(self._on_index_ready)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_index_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._index_status_label.setText(f"인덱싱 중... {done}/{total}")
        else:
            self._index_status_label.setText(f"인덱싱 중... {done}개 처리됨")

    def _on_index_ready(self, entries: List[FileEntry]) -> None:
        self._reindex_btn.setEnabled(True)
        self._index = entries
        self._index_status_label.setText(f"{len(entries)}개 항목 인덱싱됨")
        self._search_box.setEnabled(True)
        self._search_box.setPlaceholderText("검색어 입력...")
        self._category_combo.setEnabled(True)
        self._apply_search()

    def _on_fast_index_ready(self, count: int) -> None:
        self._reindex_btn.setEnabled(True)
        if count == 0:
            self._index_status_label.setText("인덱싱 실패 - 관리자 권한이 필요할 수 있습니다")
            return
        self._index_status_label.setText(f"{count}개 항목 인덱싱됨")
        self._search_box.setEnabled(True)
        self._search_box.setPlaceholderText("검색어 입력...")
        self._category_combo.setEnabled(True)
        self._apply_search()

    # -- search -------------------------------------------------------------

    def _on_search_text_changed(self, _text: str) -> None:
        self._search_timer.start()

    def _apply_search(self) -> None:
        """Builds a zero-arg do_search() closure that snapshots everything
        it needs (query text, category, and either the fast engine or the
        current slow-backend index) right here on the main thread, then runs
        it on a _SearchWorker so a slow query can't freeze the UI. do_search
        itself must not touch any QWidget - see _SearchWorker's docstring.
        """
        self._search_generation += 1
        generation = self._search_generation
        query = self._search_box.text()
        category = self._active_category

        if self._fast_engine is not None:
            engine = self._fast_engine

            def do_search() -> Tuple[List[FileEntry], str]:
                # Category filtering already happened inside the engine
                # (src/query.cpp's MatchesCategory, via EC_Search's category
                # argument) - unlike the slow backend below, no separate
                # matches_category pass is needed here.
                results = engine.search(query, category, _MAX_DISPLAYED_RESULTS)
                # "X / Y개 표시" (shown / total indexed) rather than the slow
                # backend's "shown out of N matches" - matches
                # EverythingClone's own status text convention (main.cpp),
                # since this engine has no cheap way to report a true match
                # count once maxResults truncates the scan (see
                # src/ntfs_index.cpp's Search).
                status = f"{len(results)} / {engine.total_count()}개 표시"
                return results, status
        else:
            index = self._index

            def do_search() -> Tuple[List[FileEntry], str]:
                results = search_entries(index, query) if index else []
                if category != "all":
                    results = [e for e in results if matches_category(e, category)]
                total = len(results)
                capped = results[:_MAX_DISPLAYED_RESULTS]
                if total > _MAX_DISPLAYED_RESULTS:
                    status = f"{_MAX_DISPLAYED_RESULTS}개 표시 중 (전체 {total}개 일치)"
                else:
                    status = f"{total}개 일치"
                return capped, status

        worker = _SearchWorker(do_search, generation, self)
        worker.resultReady.connect(self._on_search_result_ready)
        worker.finished.connect(worker.deleteLater)
        self._search_worker = worker
        worker.start()

    def _on_search_result_ready(self, results: List[FileEntry], status_text: str, generation: int) -> None:
        if generation != self._search_generation:
            return  # superseded by a newer search started before this one finished
        self._render_results(results, status_text)

    def _render_results(self, results: List[FileEntry], status_text: str) -> None:
        self._results_model.set_entries(results)
        self._results_status_label.setText(status_text)

    def _selected_entries(self) -> List[FileEntry]:
        selection_model = self._results_table.selectionModel()
        if selection_model is None:
            return []
        rows = sorted({index.row() for index in selection_model.selectedRows()})
        entries = []
        for row in rows:
            entry = self._results_model.entry_at(row)
            if entry is not None:
                entries.append(entry)
        return entries

    # -- image preview --------------------------------------------------

    def _update_preview(self) -> None:
        self._preview_generation += 1
        generation = self._preview_generation
        entries = self._selected_entries()

        if len(entries) == 1 and not entries[0].is_dir:
            path = entries[0].path
            if is_image_file(path):
                pixmap = QPixmap(path)
                if not pixmap.isNull():
                    self._preview_label.set_original_pixmap(pixmap)
                    return
            elif is_video_file(path):
                self._preview_label.clear_pixmap()
                self._preview_label.setText(_PREVIEW_LOADING_TEXT)
                worker = _VideoThumbnailWorker(path, generation, self)
                worker.thumbnailReady.connect(self._on_video_thumbnail_ready)
                worker.finished.connect(worker.deleteLater)
                self._video_thumbnail_worker = worker
                worker.start()
                return

        self._preview_label.clear_pixmap()
        self._preview_label.setText(_PREVIEW_PLACEHOLDER_TEXT)

    def _on_video_thumbnail_ready(self, pixmap: QPixmap, generation: int) -> None:
        if generation != self._preview_generation:
            return  # a newer selection superseded this one
        if not pixmap.isNull():
            self._preview_label.set_original_pixmap(pixmap)
        else:
            self._preview_label.clear_pixmap()
            self._preview_label.setText(_PREVIEW_VIDEO_FAILED_TEXT)

    # -- Del key / context menu --------------------------------------------

    def _on_delete_requested(self) -> None:
        entries = self._selected_entries()
        if entries:
            self._trash_delete(entries)

    def _build_context_menu(self, entries: List[FileEntry]) -> QMenu:
        menu = QMenu(self)
        self._ctx_open_action = menu.addAction("열기")
        self._ctx_open_folder_action = menu.addAction("폴더 열기")
        menu.addSeparator()
        self._ctx_trash_action = menu.addAction("삭제 (휴지통으로 이동)")
        self._ctx_force_delete_action = menu.addAction("강제 삭제")
        return menu

    def _show_context_menu(self, pos) -> None:
        row = self._results_table.rowAt(pos.y())
        if row < 0:
            return
        index = self._results_model.index(row, _COL_NAME)
        if not self._results_table.selectionModel().isSelected(index):
            self._results_table.selectRow(row)

        entries = self._selected_entries()
        if not entries:
            return

        menu = self._build_context_menu(entries)
        action = menu.exec_(self._results_table.viewport().mapToGlobal(pos))
        if action == self._ctx_open_action:
            for entry in entries:
                _open_path(entry.path)
        elif action == self._ctx_open_folder_action:
            folders = {entry.path if entry.is_dir else os.path.dirname(entry.path) for entry in entries}
            for folder in folders:
                _open_path(folder)
        elif action == self._ctx_trash_action:
            self._trash_delete(entries)
        elif action == self._ctx_force_delete_action:
            self._force_delete(entries)

    # -- normal delete (Recycle Bin) ----------------------------------------

    def _trash_delete(self, entries: List[FileEntry]) -> None:
        reply = QMessageBox.question(
            self,
            "휴지통으로 이동",
            f"{len(entries)}개 항목을 휴지통으로 이동하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._set_busy(True)
        worker = _TrashDeleteWorker([e.path for e in entries], self)
        worker.resultReady.connect(self._on_trash_delete_done)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_trash_delete_done(self, results: List[Tuple[str, bool]]) -> None:
        self._set_busy(False)
        failed = [path for path, ok in results if not ok]
        if failed:
            QMessageBox.warning(self, "일부 이동 실패", "\n".join(failed))
        self._remove_deleted_paths({path for path, ok in results if ok})

    # -- force delete: single (full confirm flow) / batch --------------------

    def _force_delete(self, entries: List[FileEntry]) -> None:
        if len(entries) == 1:
            self._force_delete_single(entries[0].path)
        else:
            self._force_delete_batch(entries)

    def _force_delete_single(self, path: str) -> None:
        self._set_busy(True)
        worker = AnalyzeWorker(path, self)
        worker.resultReady.connect(self._on_analyze_ready)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_analyze_ready(self, result: AnalyzeResult) -> None:
        self._set_busy(False)
        if result.blocked:
            QMessageBox.warning(self, "삭제할 수 없음", result.block_reason)
            return
        dialog = ConfirmForceDeleteDialog(result, self)
        if dialog.exec_() == QDialog.Accepted:
            self._execute_single_delete(result.path, dialog.kill_pids, dialog.build_options())

    def _execute_single_delete(self, path: str, kill_pids: List[int], options: ExecuteOptions) -> None:
        self._set_busy(True)
        worker = ExecuteWorker(path, kill_pids, options, self)
        worker.resultReady.connect(lambda result: self._on_single_execute_done(path, result))
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_single_execute_done(self, path: str, result: ExecuteResult) -> None:
        self._set_busy(False)
        QMessageBox.information(self, "삭제 완료", f"{result.deleted_count}개 삭제, {result.failed_count}개 실패")
        if result.deleted_count > 0:
            self._remove_deleted_paths({path})

    def _force_delete_batch(self, entries: List[FileEntry]) -> None:
        reply = QMessageBox.question(
            self,
            "강제 삭제 확인",
            f"{len(entries)}개 항목을 영구적으로 삭제하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._set_busy(True)
        worker = _BatchForceDeleteWorker([e.path for e in entries], self)
        worker.resultReady.connect(self._on_batch_force_delete_done)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_batch_force_delete_done(self, results: List[Tuple[str, bool]]) -> None:
        self._set_busy(False)
        failed = [path for path, ok in results if not ok]
        if failed:
            QMessageBox.warning(self, "일부 삭제 실패", "\n".join(failed))
        self._remove_deleted_paths({path for path, ok in results if ok})

    # -- shared helpers -------------------------------------------------

    def _remove_deleted_paths(self, paths) -> None:
        """Removes successfully-deleted paths (and, for a deleted
        directory, everything currently displayed under it) directly from
        the results model, rather than re-running a search - correct for
        both backends without depending on either one's index having caught
        up with the delete first. That matters more for the fast backend
        than it used to for the slow one alone: its index only updates once
        the live USN watcher processes the change, which isn't necessarily
        instant, so an immediate re-search right after a delete could still
        briefly show the just-deleted file. The trailing separator on each
        prefix is what stops "Downloads" from matching "Downloads2" (same
        boundary bug path_guard.py's is_within_or_equal fixes, applied here
        just for display hygiene rather than a safety check).
        """
        if not paths:
            return
        prefixes = tuple(os.path.join(p, "") for p in paths)

        def is_deleted(path: str) -> bool:
            return path in paths or path.startswith(prefixes)

        remaining = [e for e in self._results_model.entries() if not is_deleted(e.path)]
        self._results_model.set_entries(remaining)

        if self._fast_engine is None:
            self._index = [e for e in self._index if not is_deleted(e.path)]

    def _set_busy(self, busy: bool) -> None:
        self._reindex_btn.setEnabled(not busy)
        self._results_table.setEnabled(not busy)

    def _on_restart_as_admin(self) -> None:
        if request_admin_restart(extra_args=["--start-page", "search"]):
            app = QApplication.instance()
            if app is not None:
                app.quit()
        else:
            QMessageBox.warning(
                self,
                "다시 시작 실패",
                "관리자 권한으로 다시 시작하지 못했습니다. 직접 관리자 권한으로 실행해 주세요.",
            )

    @staticmethod
    def _wait_for_worker(worker: Optional[QThread]) -> bool:
        """Cancels/waits for one worker, returning whether it's confirmed
        stopped (used by stop() below to gate closing the fast engine).

        The RuntimeError guard covers a real, confirmed-reachable case: a
        worker that already finished (every worker here connects
        finished -> deleteLater) has its underlying Qt object destroyed the
        next time the event loop runs - which, in a long-running real app,
        has near-certainly already happened by the time the user gets
        around to closing it. The passed-in reference is then a dangling
        wrapper around a deleted object; touching it (getattr included -
        sip raises RuntimeError, not AttributeError, so getattr's own
        default doesn't catch it) raises instead of behaving like None.
        There's nothing to cancel or wait for in that case - the worker
        (and whatever DLL call it may have been running) has definitely
        already finished, so this reports it as confirmed-stopped.
        """
        if worker is None:
            return True
        try:
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()
            return worker.wait(10000)
        except RuntimeError:
            return True

    def stop(self) -> None:
        """Cancels/waits for any in-flight index/delete worker, search
        worker, and video-thumbnail worker, then closes the fast engine if
        one was created - same reasoning as before this class ever touched
        a DLL (destroying a live QThread is undefined behavior in Qt), but
        closing the fast engine adds a sharper failure mode than a merely-
        orphaned QThread: closing it while a _FastIndexWorker's
        EC_BuildIndex call or a _SearchWorker's engine.search() call is
        *still running* on another thread would free state that call is
        still touching - a real C++ use-after-free, not just Python/Qt
        object churn. So the fast engine is only closed once *every*
        worker is confirmed stopped (the video-thumbnail worker never
        touches the fast engine itself, but is included for the same
        blanket "no QThread this class owns may still be running when the
        process tears down" reasoning). If any wait() times out, the fast
        engine (and its background USN-watcher threads) is deliberately
        left to leak for the rest of this process's life rather than risk
        that - safe, since this only ever runs from aboutToQuit, i.e. the
        process is exiting anyway.
        """
        worker_stopped = self._wait_for_worker(self._worker)
        search_worker_stopped = self._wait_for_worker(self._search_worker)
        video_worker_stopped = self._wait_for_worker(self._video_thumbnail_worker)

        if self._fast_engine is not None and worker_stopped and search_worker_stopped and video_worker_stopped:
            self._fast_engine.save_indexes()
            self._fast_engine.close()


def _open_path(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - user-triggered, not a scripted action
    else:
        subprocess.run(["xdg-open", path], check=False)


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 13px; font-weight: 600; color: #8c92a4;")
    return label
