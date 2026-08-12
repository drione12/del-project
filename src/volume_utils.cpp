#include "volume_utils.h"

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>
#include <string>

std::vector<wchar_t> DetectNtfsFixedDrives() {
    std::vector<wchar_t> drives;
    DWORD mask = GetLogicalDrives();

    for (wchar_t letter = L'A'; letter <= L'Z'; letter++) {
        if (!(mask & (1u << (letter - L'A')))) continue;

        std::wstring root = std::wstring(1, letter) + L":\\";
        if (GetDriveTypeW(root.c_str()) != DRIVE_FIXED) continue;

        wchar_t fsName[MAX_PATH]{};
        if (GetVolumeInformationW(root.c_str(), nullptr, 0, nullptr, nullptr, nullptr,
                                   fsName, MAX_PATH) &&
            _wcsicmp(fsName, L"NTFS") == 0) {
            drives.push_back(letter);
        }
    }
    return drives;
}
