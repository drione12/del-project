#include "ntfs_index.h"

#include <cstdio>
#include <unordered_set>
#include <utility>

#include "mft_record.h"
#include "query.h"
#include "text_match.h"

namespace {
constexpr size_t kEnumBufferSize = 64 * 1024;

// Reasons that can plausibly change size/dates (or mean this is a brand-new
// entry) - worth the blocking FSCTL_GET_NTFS_FILE_RECORD call. Purely
// cosmetic changes (ACL/security, encryption, compression, object id,
// alternate-stream writes) are deliberately excluded so a write-heavy file
// (e.g. a log being appended to) doesn't hammer the volume with a raw MFT
// read on every single journal record.
constexpr DWORD kMftRefreshReasonMask =
    USN_REASON_FILE_CREATE | USN_REASON_DATA_OVERWRITE | USN_REASON_DATA_EXTEND |
    USN_REASON_DATA_TRUNCATION | USN_REASON_BASIC_INFO_CHANGE |
    USN_REASON_RENAME_NEW_NAME | USN_REASON_HARD_LINK_CHANGE;

// Groups every record by keyFn(entry) (skipping any entry skipFn flags) and
// returns the FRNs that share a key with at least one other record - i.e.
// the dupe: family's definition of "duplicate". Used with the mutex_ already
// held, over the full index rather than just the current search hits, since
// "is this a duplicate" is a whole-population question.
template <typename KeyFn, typename SkipFn>
std::unordered_set<uint64_t> BuildDupeSet(const std::unordered_map<uint64_t, FileEntry>& records,
                                           KeyFn keyFn, SkipFn skipFn) {
    std::unordered_map<decltype(keyFn(std::declval<const FileEntry&>())), std::vector<uint64_t>>
        groups;
    for (const auto& [frn, entry] : records) {
        if (skipFn(entry)) continue;
        groups[keyFn(entry)].push_back(frn);
    }

    std::unordered_set<uint64_t> dupes;
    for (const auto& [key, frns] : groups) {
        if (frns.size() < 2) continue;
        for (uint64_t frn : frns) dupes.insert(frn);
    }
    return dupes;
}

bool NeverSkip(const FileEntry&) { return false; }

constexpr uint32_t kIndexFileMagic = 0x58444945;  // "EIDX" (little-endian on disk)
constexpr uint32_t kIndexFileVersion = 1;

// Small enough to be its own helper rather than pulling in usn_watcher.h
// (which depends on ntfs_index.h - keeping the dependency one-directional).
bool QueryJournalPosition(HANDLE hVolume, DWORDLONG& journalIdOut, USN& nextUsnOut) {
    USN_JOURNAL_DATA_V0 data{};
    DWORD bytesReturned = 0;
    if (!DeviceIoControl(hVolume, FSCTL_QUERY_USN_JOURNAL, nullptr, 0, &data, sizeof(data),
                          &bytesReturned, nullptr)) {
        return false;
    }
    journalIdOut = data.UsnJournalID;
    nextUsnOut = data.NextUsn;
    return true;
}

// Case-insensitive "is path inside folder" with a path-separator boundary
// check, so excluding "C:\Temp" doesn't also exclude "C:\TempFiles".
bool IsUnderFolder(const std::wstring& path, const std::wstring& folder) {
    if (path.size() < folder.size()) return false;
    if (_wcsnicmp(path.c_str(), folder.c_str(), folder.size()) != 0) return false;
    return path.size() == folder.size() || path[folder.size()] == L'\\';
}

bool IsUnderAnyFolder(const std::wstring& path, const std::vector<std::wstring>& folders) {
    for (const auto& folder : folders) {
        if (IsUnderFolder(path, folder)) return true;
    }
    return false;
}

}  // namespace

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

void NtfsIndex::ApplyUsnRecord(const USN_RECORD* record, HANDLE hVolume) {
    std::wstring name(reinterpret_cast<const wchar_t*>(
                           reinterpret_cast<const BYTE*>(record) + record->FileNameOffset),
                       record->FileNameLength / sizeof(wchar_t));

    if (record->Reason & USN_REASON_FILE_DELETE) {
        std::lock_guard<std::mutex> lock(mutex_);
        records_.erase(record->FileReferenceNumber);
        pathCache_.erase(record->FileReferenceNumber);
        return;
    }

    bool isDirectory = (record->FileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
    bool isRename = (record->Reason & (USN_REASON_RENAME_NEW_NAME | USN_REASON_RENAME_OLD_NAME)) != 0;
    bool refreshMft = (record->Reason & kMftRefreshReasonMask) != 0;

    FileEntry entry;
    entry.frn = record->FileReferenceNumber;
    entry.parentFrn = record->ParentFileReferenceNumber;
    entry.name = name;
    entry.attributes = record->FileAttributes;

    // Read outside the lock, same as the initial volume scan - this is a
    // blocking DeviceIoControl call and shouldn't hold up a concurrent
    // Search() on the UI thread.
    if (refreshMft) {
        MftRecordInfo mftInfo = ReadMftRecordInfo(hVolume, entry.frn);
        if (mftInfo.valid) {
            entry.size = mftInfo.size;
            entry.createdTime = mftInfo.createdTime;
            entry.modifiedTime = mftInfo.modifiedTime;
            entry.accessedTime = mftInfo.accessedTime;
        }
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (!refreshMft) {
        // This reason doesn't imply size/dates changed - keep whatever we
        // already had cached instead of clobbering it back to unknown.
        auto it = records_.find(entry.frn);
        if (it != records_.end()) {
            entry.size = it->second.size;
            entry.createdTime = it->second.createdTime;
            entry.modifiedTime = it->second.modifiedTime;
            entry.accessedTime = it->second.accessedTime;
        }
    }
    records_[entry.frn] = entry;

    if (isRename && isDirectory) {
        // A renamed/moved directory invalidates every cached descendant path;
        // clearing the whole cache is cheap next to how rarely this fires.
        pathCache_.clear();
    } else {
        pathCache_.erase(entry.frn);
    }
}

std::vector<SearchResult> NtfsIndex::Search(const std::wstring& queryText, size_t maxResults,
                                             const std::vector<std::wstring>& excludeFolders) const {
    std::vector<SearchResult> results;
    if (queryText.empty()) return results;

    Query query = ParseQuery(queryText);
    if (query.groups.empty()) return results;

    bool needDupe = false, needSizeDupe = false, needNamePartDupe = false, needAttribDupe = false;
    bool needDaDupe = false, needDcDupe = false, needDmDupe = false;
    for (const auto& group : query.groups) {
        for (const auto& term : group.terms) {
            switch (term.kind) {
                case QueryTerm::Kind::Dupe: needDupe = true; break;
                case QueryTerm::Kind::SizeDupe: needSizeDupe = true; break;
                case QueryTerm::Kind::NamePartDupe: needNamePartDupe = true; break;
                case QueryTerm::Kind::AttribDupe: needAttribDupe = true; break;
                case QueryTerm::Kind::DateAccessedDupe: needDaDupe = true; break;
                case QueryTerm::Kind::DateCreatedDupe: needDcDupe = true; break;
                case QueryTerm::Kind::DateModifiedDupe: needDmDupe = true; break;
                default: break;
            }
        }
    }

    std::lock_guard<std::mutex> lock(mutex_);

    std::unordered_set<uint64_t> dupeSet, sizeDupeSet, namePartDupeSet, attribDupeSet;
    std::unordered_set<uint64_t> daDupeSet, dcDupeSet, dmDupeSet;
    DupeMembership dupes;
    if (needDupe) {
        dupeSet = BuildDupeSet(
            records_, [](const FileEntry& e) { return ToLower(e.name); }, NeverSkip);
        dupes.dupe = &dupeSet;
    }
    if (needSizeDupe) {
        sizeDupeSet = BuildDupeSet(
            records_, [](const FileEntry& e) { return e.size; }, NeverSkip);
        dupes.sizeDupe = &sizeDupeSet;
    }
    if (needNamePartDupe) {
        namePartDupeSet = BuildDupeSet(
            records_,
            [](const FileEntry& e) {
                size_t dot = e.name.find_last_of(L'.');
                return ToLower(dot == std::wstring::npos ? e.name : e.name.substr(0, dot));
            },
            NeverSkip);
        dupes.namePartDupe = &namePartDupeSet;
    }
    if (needAttribDupe) {
        attribDupeSet = BuildDupeSet(
            records_, [](const FileEntry& e) { return e.attributes; }, NeverSkip);
        dupes.attribDupe = &attribDupeSet;
    }
    if (needDaDupe) {
        daDupeSet = BuildDupeSet(
            records_, [](const FileEntry& e) { return e.accessedTime; },
            [](const FileEntry& e) { return e.accessedTime == 0; });
        dupes.dateAccessedDupe = &daDupeSet;
    }
    if (needDcDupe) {
        dcDupeSet = BuildDupeSet(
            records_, [](const FileEntry& e) { return e.createdTime; },
            [](const FileEntry& e) { return e.createdTime == 0; });
        dupes.dateCreatedDupe = &dcDupeSet;
    }
    if (needDmDupe) {
        dmDupeSet = BuildDupeSet(
            records_, [](const FileEntry& e) { return e.modifiedTime; },
            [](const FileEntry& e) { return e.modifiedTime == 0; });
        dupes.dateModifiedDupe = &dmDupeSet;
    }

    for (const auto& [frn, entry] : records_) {
        std::wstring path;
        bool matched;
        if (query.options.matchPath) {
            path = ResolvePathLocked(frn);
            matched = MatchesQuery(query, entry.name, path, entry.attributes, entry.size,
                                    entry.createdTime, entry.modifiedTime, entry.accessedTime,
                                    frn, &dupes);
        } else {
            matched = MatchesQuery(query, entry.name, std::wstring(), entry.attributes,
                                    entry.size, entry.createdTime, entry.modifiedTime,
                                    entry.accessedTime, frn, &dupes);
            if (matched) path = ResolvePathLocked(frn);
        }
        if (!matched) continue;
        if (!excludeFolders.empty() && IsUnderAnyFolder(path, excludeFolders)) continue;

        results.push_back(
            {path, entry.size, entry.createdTime, entry.modifiedTime, entry.accessedTime, entry.attributes});
        if (results.size() >= maxResults) break;
    }
    return results;
}

size_t NtfsIndex::Count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return records_.size();
}

bool NtfsIndex::SaveToFile(const std::wstring& filePath) const {
    wchar_t volumePath[8];
    swprintf_s(volumePath, L"\\\\.\\%c:", driveLetter_);
    HANDLE hVol = CreateFileW(volumePath, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                               nullptr, OPEN_EXISTING, 0, nullptr);
    if (hVol == INVALID_HANDLE_VALUE) return false;

    DWORDLONG journalId = 0;
    USN nextUsn = 0;
    bool gotJournal = QueryJournalPosition(hVol, journalId, nextUsn);
    CloseHandle(hVol);
    if (!gotJournal) return false;

    FILE* f = nullptr;
    if (_wfopen_s(&f, filePath.c_str(), L"wb") != 0 || !f) return false;

    fwrite(&kIndexFileMagic, sizeof(kIndexFileMagic), 1, f);
    fwrite(&kIndexFileVersion, sizeof(kIndexFileVersion), 1, f);
    fwrite(&rootFrn_, sizeof(rootFrn_), 1, f);
    fwrite(&journalId, sizeof(journalId), 1, f);
    fwrite(&nextUsn, sizeof(nextUsn), 1, f);

    std::lock_guard<std::mutex> lock(mutex_);
    uint64_t count = records_.size();
    fwrite(&count, sizeof(count), 1, f);
    for (const auto& [frn, entry] : records_) {
        fwrite(&entry.frn, sizeof(entry.frn), 1, f);
        fwrite(&entry.parentFrn, sizeof(entry.parentFrn), 1, f);
        fwrite(&entry.attributes, sizeof(entry.attributes), 1, f);
        fwrite(&entry.size, sizeof(entry.size), 1, f);
        fwrite(&entry.createdTime, sizeof(entry.createdTime), 1, f);
        fwrite(&entry.modifiedTime, sizeof(entry.modifiedTime), 1, f);
        fwrite(&entry.accessedTime, sizeof(entry.accessedTime), 1, f);
        uint32_t nameLen = static_cast<uint32_t>(entry.name.size());
        fwrite(&nameLen, sizeof(nameLen), 1, f);
        if (nameLen > 0) fwrite(entry.name.data(), sizeof(wchar_t), nameLen, f);
    }

    fclose(f);
    return true;
}

bool NtfsIndex::LoadFromFile(const std::wstring& filePath, wchar_t driveLetter,
                              DWORDLONG& savedJournalIdOut, USN& savedUsnOut) {
    FILE* f = nullptr;
    if (_wfopen_s(&f, filePath.c_str(), L"rb") != 0 || !f) return false;

    uint32_t magic = 0, version = 0;
    uint64_t rootFrn = 0;
    DWORDLONG journalId = 0;
    USN nextUsn = 0;
    uint64_t count = 0;
    bool ok = fread(&magic, sizeof(magic), 1, f) == 1 && magic == kIndexFileMagic &&
              fread(&version, sizeof(version), 1, f) == 1 && version == kIndexFileVersion &&
              fread(&rootFrn, sizeof(rootFrn), 1, f) == 1 &&
              fread(&journalId, sizeof(journalId), 1, f) == 1 &&
              fread(&nextUsn, sizeof(nextUsn), 1, f) == 1 &&
              fread(&count, sizeof(count), 1, f) == 1;

    // 64MB record-count sanity bound: a snapshot for a real volume is never
    // remotely this large, so this only rejects a corrupt/truncated file
    // rather than attempting a huge reserve() off garbage bytes.
    if (ok && count > (64ull << 20)) ok = false;

    std::unordered_map<uint64_t, FileEntry> loaded;
    if (ok) {
        loaded.reserve(static_cast<size_t>(count));
        for (uint64_t i = 0; ok && i < count; i++) {
            FileEntry entry;
            uint32_t nameLen = 0;
            ok = fread(&entry.frn, sizeof(entry.frn), 1, f) == 1 &&
                 fread(&entry.parentFrn, sizeof(entry.parentFrn), 1, f) == 1 &&
                 fread(&entry.attributes, sizeof(entry.attributes), 1, f) == 1 &&
                 fread(&entry.size, sizeof(entry.size), 1, f) == 1 &&
                 fread(&entry.createdTime, sizeof(entry.createdTime), 1, f) == 1 &&
                 fread(&entry.modifiedTime, sizeof(entry.modifiedTime), 1, f) == 1 &&
                 fread(&entry.accessedTime, sizeof(entry.accessedTime), 1, f) == 1 &&
                 fread(&nameLen, sizeof(nameLen), 1, f) == 1;
            if (ok && nameLen > 32768) ok = false;  // sanity bound, see above
            if (ok && nameLen > 0) {
                entry.name.resize(nameLen);
                ok = fread(&entry.name[0], sizeof(wchar_t), nameLen, f) == nameLen;
            }
            if (ok) loaded[entry.frn] = std::move(entry);
        }
    }

    fclose(f);
    if (!ok) return false;

    driveLetter_ = driveLetter;
    rootFrn_ = rootFrn;
    savedJournalIdOut = journalId;
    savedUsnOut = nextUsn;

    std::lock_guard<std::mutex> lock(mutex_);
    records_ = std::move(loaded);
    pathCache_.clear();
    return true;
}
