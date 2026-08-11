#pragma once

#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>
#include <windows.h>
#include <winioctl.h>

#include "file_entry.h"

// Holds the full name+path index for a single NTFS volume: an initial full
// scan via FSCTL_ENUM_USN_DATA (reads MFT records through the USN interface,
// not a directory walk), plus incremental updates fed in from the USN
// journal by UsnWatcher.
class NtfsIndex {
public:
    bool BuildFromVolume(wchar_t driveLetter, std::wstring& errorOut);
    void ApplyUsnRecord(const USN_RECORD* record);

    std::vector<std::wstring> Search(const std::wstring& query, size_t maxResults) const;
    size_t Count() const;
    wchar_t Drive() const { return driveLetter_; }

private:
    std::wstring ResolvePathLocked(uint64_t frn) const;

    mutable std::mutex mutex_;
    std::unordered_map<uint64_t, FileEntry> records_;
    mutable std::unordered_map<uint64_t, std::wstring> pathCache_;
    uint64_t rootFrn_ = 0;
    wchar_t driveLetter_ = L'\0';
};
