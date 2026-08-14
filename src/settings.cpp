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

DisplaySettings LoadDisplaySettings() {
    std::wstring iniPath = GetSettingsDirectory() + L"\\" + kSettingsFileName;

    DisplaySettings s;
    s.valid = GetPrivateProfileIntW(L"Display", L"Valid", 0, iniPath.c_str()) != 0;
    if (!s.valid) return s;

    wchar_t face[LF_FACESIZE]{};
    GetPrivateProfileStringW(L"Display", L"FontFace", L"", face, LF_FACESIZE, iniPath.c_str());
    s.fontFace = face;
    s.fontSize = GetPrivateProfileIntW(L"Display", L"FontSize", 9, iniPath.c_str());
    s.bold = GetPrivateProfileIntW(L"Display", L"Bold", 0, iniPath.c_str()) != 0;
    s.italic = GetPrivateProfileIntW(L"Display", L"Italic", 0, iniPath.c_str()) != 0;
    s.textColor =
        static_cast<COLORREF>(GetPrivateProfileIntW(L"Display", L"TextColor", 0, iniPath.c_str()));
    s.bgColor = static_cast<COLORREF>(
        GetPrivateProfileIntW(L"Display", L"BgColor", 0x00FFFFFF, iniPath.c_str()));

    // A blank saved face name means the dialog was cancelled mid-save or the
    // file is corrupt - treat it the same as "nothing saved".
    if (s.fontFace.empty()) s.valid = false;
    return s;
}

void SaveDisplaySettings(const DisplaySettings& settings) {
    std::wstring iniPath = GetSettingsDirectory() + L"\\" + kSettingsFileName;

    WritePrivateProfileStringW(L"Display", L"Valid", settings.valid ? L"1" : L"0", iniPath.c_str());
    WritePrivateProfileStringW(L"Display", L"FontFace", settings.fontFace.c_str(), iniPath.c_str());

    wchar_t buf[16];
    swprintf_s(buf, L"%d", settings.fontSize);
    WritePrivateProfileStringW(L"Display", L"FontSize", buf, iniPath.c_str());
    WritePrivateProfileStringW(L"Display", L"Bold", settings.bold ? L"1" : L"0", iniPath.c_str());
    WritePrivateProfileStringW(L"Display", L"Italic", settings.italic ? L"1" : L"0", iniPath.c_str());

    swprintf_s(buf, L"%lu", static_cast<unsigned long>(settings.textColor));
    WritePrivateProfileStringW(L"Display", L"TextColor", buf, iniPath.c_str());
    swprintf_s(buf, L"%lu", static_cast<unsigned long>(settings.bgColor));
    WritePrivateProfileStringW(L"Display", L"BgColor", buf, iniPath.c_str());
}
