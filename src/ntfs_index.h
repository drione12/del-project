#pragma once

#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>
#include <windows.h>
#include <winioctl.h>

#include "file_entry.h"
#include "query.h"  // ResultCategory, used by Search's category parameter below

// One search hit: the resolved full path plus the metadata a results list
// wants to show (and, later, filter/sort on) without re-resolving anything.
struct SearchResult {
    std::wstring path;
    uint64_t size = 0;
    uint64_t createdTime = 0;   // FILETIME as a single 100ns-tick uint64
    uint64_t modifiedTime = 0;
    uint64_t accessedTime = 0;
    DWORD attributes = 0;
};

// Holds the full name+path index for a single NTFS volume: an initial full
// scan via FSCTL_ENUM_USN_DATA (reads MFT records through the USN interface,
// not a directory walk), plus incremental updates fed in from the USN
// journal by UsnWatcher.
class NtfsIndex {
public:
    bool BuildFromVolume(wchar_t driveLetter, std::wstring& errorOut);
    void ApplyUsnRecord(const USN_RECORD* record, HANDLE hVolume);

    std::vector<SearchResult> Search(const std::wstring& query, size_t maxResults,
                                      const std::vector<std::wstring>& excludeFolders = {},
                                      ResultCategory category = ResultCategory::All) const;
    size_t Count() const;
    wchar_t Drive() const { return driveLetter_; }

    // Serializes records_ to filePath, tagged with the volume's current USN
    // journal position so a future LoadFromFile can catch up on whatever
    // changed while the app wasn't running instead of trusting stale data.
    bool SaveToFile(const std::wstring& filePath) const;

    // Loads a previously-saved snapshot. Returns false (leaving this index
    // untouched) if the file is missing, corrupt, or from an incompatible
    // version - callers should fall back to BuildFromVolume. On success,
    // savedJournalIdOut/savedUsnOut receive the journal position the caller
    // needs to catch up from (see UsnWatcher::CatchUp) before the snapshot
    // can be trusted as current.
    bool LoadFromFile(const std::wstring& filePath, wchar_t driveLetter,
                       DWORDLONG& savedJournalIdOut, USN& savedUsnOut);

private:
    std::wstring ResolvePathLocked(uint64_t frn) const;

    mutable std::mutex mutex_;
    std::unordered_map<uint64_t, FileEntry> records_;
    mutable std::unordered_map<uint64_t, std::wstring> pathCache_;
    uint64_t rootFrn_ = 0;
    wchar_t driveLetter_ = L'\0';
};
