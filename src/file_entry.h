#pragma once

#include <cstdint>
#include <string>
#include <windows.h>

struct FileEntry {
    uint64_t frn = 0;
    uint64_t parentFrn = 0;
    std::wstring name;
    DWORD attributes = 0;

    // From $STANDARD_INFORMATION / unnamed $DATA in the raw MFT record - not
    // available from the USN enumeration used to build the base index, so
    // these stay 0 ("unknown") until a size/date pass fills them in.
    uint64_t size = 0;
    uint64_t createdTime = 0;   // FILETIME as a single 100ns-tick uint64, 0 = unknown
    uint64_t modifiedTime = 0;
    uint64_t accessedTime = 0;
};
