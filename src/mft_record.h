#pragma once

#include <cstdint>
#include <windows.h>

// Reads size and timestamp info directly from a file's raw MFT record via
// FSCTL_GET_NTFS_FILE_RECORD - the USN enumeration used to build the base
// index only gives names/attributes, not size or dates.
struct MftRecordInfo {
    bool valid = false;
    uint64_t size = 0;
    uint64_t createdTime = 0;   // FILETIME as a single 100ns-tick uint64
    uint64_t modifiedTime = 0;
    uint64_t accessedTime = 0;
};

// hVolume must be an open handle to the volume (e.g. "\\.\C:") containing frn.
// Returns MftRecordInfo{} (valid=false) if the record couldn't be read or
// parsed - callers should treat that as "unknown", not an error.
MftRecordInfo ReadMftRecordInfo(HANDLE hVolume, uint64_t frn);
