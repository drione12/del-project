#pragma once

#include <cstdint>
#include <string>
#include <windows.h>

struct FileEntry {
    uint64_t frn = 0;
    uint64_t parentFrn = 0;
    std::wstring name;
    DWORD attributes = 0;
};
