// EVERYTHINGCORE_EXPORTS is defined by CMakeLists.txt's EverythingCore
// target (target_compile_definitions) - core_api.h uses it to select
// __declspec(dllexport) here vs __declspec(dllimport) for any other
// consumer that includes just the header.
#include "core_api.h"

#include <windows.h>

#include <algorithm>
#include <atomic>
#include <exception>
#include <memory>
#include <sstream>
#include <thread>
#include <vector>

#include "ntfs_index.h"
#include "privileges.h"
#include "query.h"
#include "settings.h"
#include "usn_watcher.h"
#include "volume_utils.h"

namespace {

std::vector<std::wstring> SplitSemicolons(const wchar_t* text) {
    std::vector<std::wstring> parts;
    if (!text) return parts;
    std::wstringstream stream(text);
    std::wstring part;
    while (std::getline(stream, part, L';')) {
        if (!part.empty()) parts.push_back(part);
    }
    return parts;
}

}  // namespace

// Every EC_API function below is a try/catch(...) boundary: a C++ exception
// (std::bad_alloc from the large per-volume reserve()s in EC_BuildIndex is
// the realistic one, but any std::wstring/std::vector/std::thread operation
// can throw under memory pressure) must never cross an extern "C" DLL
// boundary back into ctypes - the behavior is undefined, and in practice it
// either aborts the whole process outright or surfaces as something ctypes
// has no way to turn into a catchable Python exception. Each function
// returns its natural "nothing happened" value (0/nullptr/false) on
// failure, which every existing caller already treats as "not indexed yet"
// or "no results" - no new error-handling path needed on the Python side.
// EC_BuildIndex needs a second, inner try/catch too: an exception thrown
// inside a std::thread's own entry function calls std::terminate()
// (killing the whole process) before it can ever reach a try/catch in the
// *calling* thread - only a try/catch physically inside that thread's own
// call stack can stop it. See UsnWatcher::WatchLoop for the same reasoning
// applied to the long-running watcher thread.

// Mirrors main.cpp's own g_volumes/g_excludeFolders/g_running globals, just
// scoped to one handle instead of process-wide - a DLL loaded into a
// long-running host process (unlike EverythingClone.exe, which is the whole
// process) needs its state ownable/destroyable independent of process exit.
struct EC_State {
    std::vector<std::unique_ptr<NtfsIndex>> volumes;
    std::vector<wchar_t> driveLetters;  // parallel to volumes
    std::atomic<bool> watcherRunning{true};
    std::vector<std::thread> watcherThreads;
    std::vector<std::wstring> excludeFolders;
    std::vector<SearchResult> lastResults;
};

EC_Handle EC_Create() {
    try {
        return new EC_State();
    } catch (...) {
        return nullptr;
    }
}

void EC_Destroy(EC_Handle handle) {
    if (!handle) return;
    auto* state = static_cast<EC_State*>(handle);
    try {
        // Signal every watcher thread to stop and join (not detach) before
        // freeing the NtfsIndex objects they reference - see core_api.h's
        // EC_Destroy doc comment for why this matters here specifically.
        state->watcherRunning = false;
        for (auto& t : state->watcherThreads) {
            if (t.joinable()) t.join();
        }
        delete state;
    } catch (...) {
        // Nothing left to safely retry from here - deliberately not
        // rethrowing (this runs from SearchPage.stop(), the app's own
        // shutdown path; letting an exception escape would crash the
        // process on the way out instead of just leaking this handle).
    }
}

unsigned long long EC_BuildIndex(EC_Handle handle) {
    if (!handle) return 0;
    auto* state = static_cast<EC_State*>(handle);

    try {
        EnablePrivilege(SE_BACKUP_NAME);
        state->excludeFolders = LoadExcludeFolders();

        auto drives = DetectNtfsFixedDrives();
        state->driveLetters = drives;
        for (size_t i = 0; i < drives.size(); i++) {
            state->volumes.push_back(std::make_unique<NtfsIndex>());
        }

        // Parallel per-volume build, same reasoning as main.cpp's own
        // IndexingThread: a saved snapshot + bounded USN catch-up is much
        // cheaper than a full MFT re-enumeration, so try that first and only
        // fall back to BuildFromVolume if there's no usable snapshot.
        std::vector<std::thread> buildThreads;
        for (size_t i = 0; i < drives.size(); i++) {
            buildThreads.emplace_back([state, i, &drives]() {
                // Deliberately its own try/catch, not just the one around
                // the rest of EC_BuildIndex below: an exception thrown here
                // runs on this lambda's own std::thread, so an uncaught one
                // calls std::terminate() (killing the whole process) before
                // it could ever reach a catch block back on the calling
                // thread - seen in practice as EC_BuildIndex triggering an
                // instant, silent crash on a machine with several volumes,
                // where the large per-volume NtfsIndex allocations
                // (reserve(1<<20) records each) running in parallel are
                // enough to exhaust memory on a std::bad_alloc. Catching it
                // here also means one bad volume (BitLocker, a failing
                // external disk, any other volume-specific failure) leaves
                // that volume's NtfsIndex simply empty instead of taking
                // every other volume's already-succeeding build down with
                // it.
                try {
                    DWORDLONG savedJournalId = 0;
                    USN savedUsn = 0;
                    bool usedSnapshot =
                        state->volumes[i]->LoadFromFile(GetIndexFilePath(drives[i]), drives[i],
                                                         savedJournalId, savedUsn) &&
                        UsnWatcher::CatchUp(drives[i], *state->volumes[i], savedJournalId, savedUsn);
                    if (!usedSnapshot) {
                        std::wstring error;
                        state->volumes[i]->BuildFromVolume(drives[i], error);
                    }
                } catch (...) {
                }
            });
        }
        for (auto& t : buildThreads) t.join();

        for (size_t i = 0; i < drives.size(); i++) {
            if (state->volumes[i]->Count() > 0) {
                state->watcherThreads.emplace_back(UsnWatcher::WatchLoop, drives[i],
                                                    std::ref(*state->volumes[i]),
                                                    std::ref(state->watcherRunning));
            }
        }

        unsigned long long total = 0;
        for (auto& v : state->volumes) total += v->Count();
        return total;
    } catch (...) {
        return 0;
    }
}

void EC_SaveIndexes(EC_Handle handle) {
    if (!handle) return;
    auto* state = static_cast<EC_State*>(handle);
    try {
        for (size_t i = 0; i < state->volumes.size(); i++) {
            state->volumes[i]->SaveToFile(GetIndexFilePath(state->driveLetters[i]));
        }
    } catch (...) {
    }
}

unsigned long long EC_TotalCount(EC_Handle handle) {
    if (!handle) return 0;
    auto* state = static_cast<EC_State*>(handle);
    try {
        unsigned long long total = 0;
        for (auto& v : state->volumes) total += v->Count();
        return total;
    } catch (...) {
        return 0;
    }
}

void EC_SetExcludeFolders(EC_Handle handle, const wchar_t* semicolonSeparated) {
    if (!handle) return;
    auto* state = static_cast<EC_State*>(handle);
    try {
        state->excludeFolders = SplitSemicolons(semicolonSeparated);
        SaveExcludeFolders(state->excludeFolders);
    } catch (...) {
    }
}

int EC_Search(EC_Handle handle, const wchar_t* query, int category, int maxResults) {
    if (!handle) return 0;
    auto* state = static_cast<EC_State*>(handle);
    try {
        std::wstring queryText = query ? query : L"";
        auto resultCategory = static_cast<ResultCategory>(category);

        state->lastResults.clear();
        for (auto& volume : state->volumes) {
            if (static_cast<int>(state->lastResults.size()) >= maxResults) break;
            auto partial = volume->Search(queryText, maxResults - state->lastResults.size(),
                                           state->excludeFolders, resultCategory);
            state->lastResults.insert(state->lastResults.end(), partial.begin(), partial.end());
        }
        return static_cast<int>(state->lastResults.size());
    } catch (...) {
        state->lastResults.clear();
        return 0;
    }
}

namespace {
const SearchResult* GetResult(EC_Handle handle, int index) {
    if (!handle) return nullptr;
    auto* state = static_cast<EC_State*>(handle);
    if (index < 0 || static_cast<size_t>(index) >= state->lastResults.size()) return nullptr;
    return &state->lastResults[index];
}
}  // namespace

int EC_GetResultPath(EC_Handle handle, int index, wchar_t* outBuf, int outBufChars) {
    try {
        const SearchResult* result = GetResult(handle, index);
        if (!result) {
            if (outBuf && outBufChars > 0) outBuf[0] = L'\0';
            return 0;
        }
        int length = static_cast<int>(result->path.size());
        if (outBuf && outBufChars > 0) {
            // Parenthesized as (std::min) rather than a plain call: windows.h
            // #defines min/max as macros (no NOMINMAX here - see this file's
            // top), which would otherwise rewrite this into garbage tokens
            // right after "std::" (MSVC error C2589) before the compiler ever
            // sees it as the std::min template call it's meant to be.
            int toCopy = (std::min)(length, outBufChars - 1);
            std::copy(result->path.begin(), result->path.begin() + toCopy, outBuf);
            outBuf[toCopy] = L'\0';
        }
        return length;
    } catch (...) {
        if (outBuf && outBufChars > 0) outBuf[0] = L'\0';
        return 0;
    }
}

unsigned long long EC_GetResultSize(EC_Handle handle, int index) {
    try {
        const SearchResult* result = GetResult(handle, index);
        return result ? result->size : 0;
    } catch (...) {
        return 0;
    }
}

unsigned long long EC_GetResultModifiedTime(EC_Handle handle, int index) {
    try {
        const SearchResult* result = GetResult(handle, index);
        return result ? result->modifiedTime : 0;
    } catch (...) {
        return 0;
    }
}

unsigned long long EC_GetResultCreatedTime(EC_Handle handle, int index) {
    try {
        const SearchResult* result = GetResult(handle, index);
        return result ? result->createdTime : 0;
    } catch (...) {
        return 0;
    }
}

unsigned long long EC_GetResultAccessedTime(EC_Handle handle, int index) {
    try {
        const SearchResult* result = GetResult(handle, index);
        return result ? result->accessedTime : 0;
    } catch (...) {
        return 0;
    }
}

unsigned int EC_GetResultAttributes(EC_Handle handle, int index) {
    try {
        const SearchResult* result = GetResult(handle, index);
        return result ? result->attributes : 0;
    } catch (...) {
        return 0;
    }
}
