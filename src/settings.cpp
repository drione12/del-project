#include "settings.h"

#include <shlobj.h>

namespace {
constexpr wchar_t kAppFolderName[] = L"EverythingClone";
constexpr wchar_t kSettingsFileName[] = L"settings.ini";
}  // namespace

std::wstring GetSettingsDirectory() {
    wchar_t base[MAX_PATH];
    if (FAILED(SHGetFolderPathW(nullptr, CSIDL_APPDATA, nullptr, SHGFP_TYPE_CURRENT, base))) {
        return L".";
    }
    std::wstring dir = std::wstring(base) + L"\\" + kAppFolderName;
    CreateDirectoryW(dir.c_str(), nullptr);  // no-op (ERROR_ALREADY_EXISTS) after the first run
    return dir;
}

std::wstring GetIndexFilePath(wchar_t driveLetter) {
    return GetSettingsDirectory() + L"\\index_" + driveLetter + L".bin";
}

std::vector<std::wstring> LoadExcludeFolders() {
    std::wstring iniPath = GetSettingsDirectory() + L"\\" + kSettingsFileName;

    std::vector<std::wstring> folders;
    int count = GetPrivateProfileIntW(L"ExcludeFolders", L"Count", 0, iniPath.c_str());
    for (int i = 0; i < count; i++) {
        wchar_t key[32];
        swprintf_s(key, L"Folder%d", i);
        wchar_t buf[MAX_PATH];
        DWORD len =
            GetPrivateProfileStringW(L"ExcludeFolders", key, L"", buf, MAX_PATH, iniPath.c_str());
        if (len > 0) folders.emplace_back(buf);
    }
    return folders;
}

void SaveExcludeFolders(const std::vector<std::wstring>& folders) {
    std::wstring iniPath = GetSettingsDirectory() + L"\\" + kSettingsFileName;

    // Clear the whole section first so a shrinking list doesn't leave stale
    // trailing FolderN entries behind from a previously-longer list.
    WritePrivateProfileStringW(L"ExcludeFolders", nullptr, nullptr, iniPath.c_str());

    wchar_t countBuf[16];
    swprintf_s(countBuf, L"%zu", folders.size());
    WritePrivateProfileStringW(L"ExcludeFolders", L"Count", countBuf, iniPath.c_str());

    for (size_t i = 0; i < folders.size(); i++) {
        wchar_t key[32];
        swprintf_s(key, L"Folder%zu", i);
        WritePrivateProfileStringW(L"ExcludeFolders", key, folders[i].c_str(), iniPath.c_str());
    }
}
