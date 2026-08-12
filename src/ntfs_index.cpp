#include "ntfs_index.h"

#include <cstdio>

#include "mft_record.h"
#include "query.h"

namespace {
constexpr size_t kEnumBufferSize = 64 * 1024;
}

bool NtfsIndex::BuildFromVolume(wchar_t driveLetter, std::wstring& errorOut) {
    driveLetter_ = driveLetter;

    wchar_t volumePath[8];
    swprintf_s(volumePath, L"\\\\.\\%c:", driveLetter);

    HANDLE hVol = CreateFileW(volumePath, GENERIC_READ,
                               FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                               OPEN_EXISTING, 0, nullptr);
    if (hVol == INVALID_HANDLE_VALUE) {
        errorOut = L"cannot open volume (needs Administrator)";
        return false;
    }

    // The USN journal only gives us (name, parent FRN) pairs, so path building
    // needs a known stopping point: resolve the drive root's own FRN up front.
    std::wstring rootPath = std::wstring(1, driveLetter) + L":\\";
    HANDLE hRoot = CreateFileW(rootPath.c_str(), GENERIC_READ,
                                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    if (hRoot == INVALID_HANDLE_VALUE) {
        CloseHandle(hVol);
        errorOut = L"cannot open drive root";
        return false;
    }
    BY_HANDLE_FILE_INFORMATION info{};
    bool gotRoot = GetFileInformationByHandle(hRoot, &info);
    CloseHandle(hRoot);
    if (!gotRoot) {
        CloseHandle(hVol);
        errorOut = L"cannot query drive root info";
        return false;
    }
    rootFrn_ = (static_cast<uint64_t>(info.nFileIndexHigh) << 32) | info.nFileIndexLow;

    std::vector<BYTE> buffer(kEnumBufferSize);
    MFT_ENUM_DATA_V0 med{};
    med.StartFileReferenceNumber = 0;
    med.LowUsn = 0;
    med.HighUsn = MAXLONGLONG;

    std::unordered_map<uint64_t, FileEntry> newRecords;
    newRecords.reserve(1 << 20);

    DWORD bytesReturned = 0;
    while (DeviceIoControl(hVol, FSCTL_ENUM_USN_DATA, &med, sizeof(med),
                            buffer.data(), static_cast<DWORD>(buffer.size()),
                            &bytesReturned, nullptr)) {
        BYTE* cursor = buffer.data() + sizeof(USN);
        BYTE* end = buffer.data() + bytesReturned;

        while (cursor < end) {
            auto* record = reinterpret_cast<PUSN_RECORD>(cursor);
            if (record->RecordLength == 0) break;

            FileEntry entry;
            entry.frn = record->FileReferenceNumber;
            entry.parentFrn = record->ParentFileReferenceNumber;
            entry.name.assign(reinterpret_cast<wchar_t*>(cursor + record->FileNameOffset),
                               record->FileNameLength / sizeof(wchar_t));
            entry.attributes = record->FileAttributes;
            newRecords[entry.frn] = std::move(entry);

            cursor += record->RecordLength;
        }

        med.StartFileReferenceNumber = *reinterpret_cast<USN*>(buffer.data());
    }

    DWORD err = GetLastError();
    if (err != ERROR_HANDLE_EOF) {
        CloseHandle(hVol);
        errorOut = L"MFT enumeration failed (error " + std::to_wstring(err) + L")";
        return false;
    }

    // Second pass: FSCTL_ENUM_USN_DATA above only gave us names/attributes.
    // Size and timestamps live in each file's own raw MFT record.
    for (auto& [frn, entry] : newRecords) {
        MftRecordInfo mftInfo = ReadMftRecordInfo(hVol, frn);
        if (mftInfo.valid) {
            entry.size = mftInfo.size;
            entry.createdTime = mftInfo.createdTime;
            entry.modifiedTime = mftInfo.modifiedTime;
            entry.accessedTime = mftInfo.accessedTime;
        }
    }

    CloseHandle(hVol);

    std::lock_guard<std::mutex> lock(mutex_);
    records_ = std::move(newRecords);
    pathCache_.clear();
    return true;
}

std::wstring NtfsIndex::ResolvePathLocked(uint64_t frn) const {
    if (frn == rootFrn_) {
        return std::wstring(1, driveLetter_) + L":";
    }

    auto cacheIt = pathCache_.find(frn);
    if (cacheIt != pathCache_.end()) {
        return cacheIt->second;
    }

    auto it = records_.find(frn);
    if (it == records_.end()) {
        return L"?";
    }

    std::wstring full = ResolvePathLocked(it->second.parentFrn) + L"\\" + it->second.name;
    pathCache_[frn] = full;
    return full;
}

void NtfsIndex::ApplyUsnRecord(const USN_RECORD* record) {
    std::wstring name(reinterpret_cast<const wchar_t*>(
                           reinterpret_cast<const BYTE*>(record) + record->FileNameOffset),
                       record->FileNameLength / sizeof(wchar_t));

    std::lock_guard<std::mutex> lock(mutex_);

    if (record->Reason & USN_REASON_FILE_DELETE) {
        records_.erase(record->FileReferenceNumber);
        pathCache_.erase(record->FileReferenceNumber);
        return;
    }

    bool isDirectory = (record->FileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
    bool isRename = (record->Reason & (USN_REASON_RENAME_NEW_NAME | USN_REASON_RENAME_OLD_NAME)) != 0;

    FileEntry entry;
    entry.frn = record->FileReferenceNumber;
    entry.parentFrn = record->ParentFileReferenceNumber;
    entry.name = name;
    entry.attributes = record->FileAttributes;
    records_[entry.frn] = entry;

    if (isRename && isDirectory) {
        // A renamed/moved directory invalidates every cached descendant path;
        // clearing the whole cache is cheap next to how rarely this fires.
        pathCache_.clear();
    } else {
        pathCache_.erase(entry.frn);
    }
}

std::vector<SearchResult> NtfsIndex::Search(const std::wstring& queryText,
                                             size_t maxResults) const {
    std::vector<SearchResult> results;
    if (queryText.empty()) return results;

    Query query = ParseQuery(queryText);
    if (query.groups.empty()) return results;

    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& [frn, entry] : records_) {
        std::wstring path;
        if (query.options.matchPath) {
            path = ResolvePathLocked(frn);
            if (!MatchesQuery(query, entry.name, path, entry.attributes)) continue;
        } else {
            if (!MatchesQuery(query, entry.name, std::wstring(), entry.attributes)) continue;
            path = ResolvePathLocked(frn);
        }
        results.push_back({path, entry.size, entry.modifiedTime, entry.attributes});
        if (results.size() >= maxResults) break;
    }
    return results;
}

size_t NtfsIndex::Count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return records_.size();
}
