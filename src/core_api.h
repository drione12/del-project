#pragma once

// Flat C ABI wrapping the NTFS indexing/search engine (ntfs_index.*,
// usn_watcher.*, query.*, mft_record.*, volume_utils.*, privileges.*,
// settings.*) as a DLL, so Memory Master (memory_master/core/fast_search.py,
// via ctypes) can call straight into the same engine EverythingClone.exe
// uses, in-process, instead of spawning and window-embedding a separate
// EverythingClone.exe (the old approach - see memory_master/README.md for
// why that changed). extern "C" + plain types only (no STL/C++ classes
// across the boundary) since ctypes needs a flat C ABI to call into.
//
// EC_Search followed by EC_GetResult* is a "compute into an internal
// buffer, then pull rows" pattern rather than returning a dynamic array of
// variable-length strings directly - much simpler and safer to marshal
// across ctypes than the alternatives (caller-sized output arrays, a
// per-row callback). Each handle's result buffer holds exactly one
// search's results at a time, overwritten by the next EC_Search call -
// callers must finish reading (EC_GetResult*) before searching again.

#ifdef _WIN32
#ifdef EVERYTHINGCORE_EXPORTS
#define EC_API extern "C" __declspec(dllexport)
#else
#define EC_API extern "C" __declspec(dllimport)
#endif
#else
#define EC_API extern "C"
#endif

typedef void* EC_Handle;

// ResultCategory as plain ints - must match src/query.h's ResultCategory
// enum order exactly (All=0 ... Video=7), which itself matches
// memory_master/core/file_search.py's CATEGORIES tuple order. Not a shared
// header across the ctypes boundary, so this ordering is load-bearing -
// keep the three in sync by hand if the category list ever changes.
enum EC_Category {
    EC_CATEGORY_ALL = 0,
    EC_CATEGORY_MUSIC = 1,
    EC_CATEGORY_ARCHIVE = 2,
    EC_CATEGORY_DOCUMENT = 3,
    EC_CATEGORY_EXECUTABLE = 4,
    EC_CATEGORY_FOLDER = 5,
    EC_CATEGORY_IMAGE = 6,
    EC_CATEGORY_VIDEO = 7,
};

// -- lifecycle ---------------------------------------------------------

EC_API EC_Handle EC_Create();

// Stops every volume's USN watcher thread and joins it (not detach - this
// DLL lives inside a long-running host process that can destroy a handle
// long before the process itself exits, unlike EverythingClone.exe's own
// main.cpp where detached watcher threads are fine because process exit
// cleans them up regardless), then frees every NtfsIndex. Safe to call
// even if EC_BuildIndex was never called or found no volumes.
EC_API void EC_Destroy(EC_Handle handle);

// -- indexing ------------------------------------------------------------

// Blocking - call from a background thread, not the UI thread (mirrors
// main.cpp's own IndexingThread). Enables SE_BACKUP_NAME, detects fixed
// NTFS drives, loads each volume's saved snapshot + USN catch-up when
// valid, else does a full MFT scan (parallelized across volumes
// internally, same as IndexingThread's buildThreads), then starts one live
// USN watcher thread per successfully-indexed volume. Returns the total
// indexed record count across every volume - 0 usually means "not
// elevated" (opening a raw volume handle needs SeBackupPrivilege, which
// EnablePrivilege can only grant when the process token actually has it
// available, i.e. running as administrator), not "no files exist".
EC_API unsigned long long EC_BuildIndex(EC_Handle handle);

// Persists every volume's current index to its snapshot file
// (settings.h's GetIndexFilePath - same %APPDATA%\EverythingClone location
// the standalone EverythingClone.exe already uses, so either one's snapshot
// benefits the other's next startup). Call before EC_Destroy on a clean
// exit so the next EC_BuildIndex can use the fast snapshot+catchup path.
EC_API void EC_SaveIndexes(EC_Handle handle);

EC_API unsigned long long EC_TotalCount(EC_Handle handle);

// Folders hidden from search results, ';'-separated (matches how the
// caller already has to join a list into one wide string to cross the
// ctypes boundary in a single call). Persisted via settings.h - same
// shared location/format the standalone EverythingClone.exe's own Tools >
// Options dialog reads/writes, so a change from either app is visible to
// the other's next search.
EC_API void EC_SetExcludeFolders(EC_Handle handle, const wchar_t* semicolonSeparated);

// -- search ----------------------------------------------------------

// Runs the query across every indexed volume (same cross-volume
// kMaxResults-style truncation main.cpp's own SearchAll does - maxResults
// is a hard cap on the total, not per-volume) and returns the result
// count. Populates this handle's internal result buffer; read rows via
// EC_GetResult* below before the next EC_Search call overwrites them.
// query is the *effective* query text - if the caller has persistent
// match-option toggles (case/whole word/path/regex), it must prepend the
// same "case: "/"wholeword: "/"path: "/"regex: " keyword prefixes
// BuildEffectiveQuery does in main.cpp before calling this, since
// ParseQuery (inside NtfsIndex::Search) is what actually reads them - this
// API doesn't take separate boolean flags for them.
EC_API int EC_Search(EC_Handle handle, const wchar_t* query, int category, int maxResults);

// Copies the result at index into outBuf (outBufChars including the null
// terminator). Returns the path's true length in characters (excluding the
// terminator) - if that's >= outBufChars, the copy was truncated and the
// caller should retry with a bigger buffer. index must be in
// [0, last EC_Search's return value); out-of-range returns 0 and writes an
// empty string if outBuf/outBufChars allow it.
EC_API int EC_GetResultPath(EC_Handle handle, int index, wchar_t* outBuf, int outBufChars);

// FILETIME values as a single 100ns-tick uint64 (0 = unknown), matching
// SearchResult/FileEntry elsewhere in this codebase.
EC_API unsigned long long EC_GetResultSize(EC_Handle handle, int index);
EC_API unsigned long long EC_GetResultModifiedTime(EC_Handle handle, int index);
EC_API unsigned long long EC_GetResultCreatedTime(EC_Handle handle, int index);
EC_API unsigned long long EC_GetResultAccessedTime(EC_Handle handle, int index);
EC_API unsigned int EC_GetResultAttributes(EC_Handle handle, int index);
