#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winioctl.h>

#include <atomic>
#include <chrono>
#include <conio.h>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "ntfs_index.h"
#include "privileges.h"
#include "usn_watcher.h"
#include "volume_utils.h"

namespace {

std::vector<std::wstring> SearchAll(std::vector<std::unique_ptr<NtfsIndex>>& volumes,
                                     const std::wstring& query, size_t maxResults) {
    std::vector<std::wstring> results;
    for (auto& volume : volumes) {
        if (results.size() >= maxResults) break;
        auto partial = volume->Search(query, maxResults - results.size());
        results.insert(results.end(), partial.begin(), partial.end());
    }
    return results;
}

void RunInteractiveSearch(std::vector<std::unique_ptr<NtfsIndex>>& volumes) {
    std::wstring query;
    constexpr size_t kMaxShown = 20;

    while (true) {
        system("cls");
        std::wcout << L"Search: " << query << L"_\n";
        std::wcout << L"(ESC to quit, Backspace to edit)\n";
        std::wcout << L"------------------------------------------------------------\n";

        auto results = SearchAll(volumes, query, kMaxShown);
        for (auto& path : results) {
            std::wcout << path << L"\n";
        }
        if (!query.empty() && results.empty()) {
            std::wcout << L"(no matches)\n";
        }

        int ch = _getch();
        if (ch == 27) {
            break;
        } else if (ch == 8) {
            if (!query.empty()) query.pop_back();
        } else if (ch == 0 || ch == 224) {
            _getch(); // discard the 2nd byte of a special-key sequence (arrows, F-keys, ...)
        } else if (ch == 13) {
            // Enter: no-op, just redraw
        } else if (ch >= 32 && ch < 127) {
            query.push_back(static_cast<wchar_t>(ch));
        }
    }
}

} // namespace

int wmain() {
    if (!EnablePrivilege(SE_BACKUP_NAME)) {
        std::wcerr << L"Warning: could not enable SeBackupPrivilege. "
                       L"Right-click the exe and \"Run as administrator\" if indexing fails.\n";
    }

    auto drives = DetectNtfsFixedDrives();
    if (drives.empty()) {
        std::wcerr << L"No NTFS fixed drives found.\n";
        return 1;
    }

    std::vector<std::unique_ptr<NtfsIndex>> volumes;
    for (size_t i = 0; i < drives.size(); i++) {
        volumes.push_back(std::make_unique<NtfsIndex>());
    }

    std::wcout << L"Indexing " << drives.size() << L" NTFS volume(s)...\n";

    std::vector<std::wstring> errors(drives.size());
    std::vector<char> ok(drives.size(), 0); // std::vector<bool> is bit-packed and not
                                             // safe to write from multiple threads
    std::vector<std::thread> buildThreads;

    auto start = std::chrono::steady_clock::now();
    for (size_t i = 0; i < drives.size(); i++) {
        buildThreads.emplace_back([&, i]() {
            ok[i] = volumes[i]->BuildFromVolume(drives[i], errors[i]) ? 1 : 0;
        });
    }
    for (auto& t : buildThreads) t.join();
    auto elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(
                          std::chrono::steady_clock::now() - start)
                          .count();

    size_t total = 0;
    for (size_t i = 0; i < drives.size(); i++) {
        if (ok[i]) {
            std::wcout << L"  " << drives[i] << L": " << volumes[i]->Count() << L" entries\n";
            total += volumes[i]->Count();
        } else {
            std::wcerr << L"  " << drives[i] << L": failed - " << errors[i] << L"\n";
        }
    }
    std::wcout << total << L" files/folders indexed in " << elapsedMs << L" ms\n";
    if (total == 0) {
        std::wcerr << L"Nothing indexed - re-run this as Administrator.\n";
        return 1;
    }

    std::atomic<bool> running{true};
    std::vector<std::thread> watcherThreads;
    for (size_t i = 0; i < drives.size(); i++) {
        if (!ok[i]) continue;
        watcherThreads.emplace_back(UsnWatcher::WatchLoop, drives[i],
                                     std::ref(*volumes[i]), std::ref(running));
    }
    for (auto& t : watcherThreads) t.detach();

    std::wcout << L"Press any key to search...";
    _getch();
    RunInteractiveSearch(volumes);

    running = false;
    return 0;
}
