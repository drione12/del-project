#pragma once

#include <string>
#include <vector>
#include <windows.h>

// %APPDATA%\EverythingClone - created on first access if it doesn't exist
// yet. Falls back to "." (the working directory) if the shell can't resolve
// %APPDATA% for some reason, so callers always get back a usable path.
std::wstring GetSettingsDirectory();

// Where a given drive's saved index snapshot lives, e.g.
// %APPDATA%\EverythingClone\index_C.bin for drive C:.
std::wstring GetIndexFilePath(wchar_t driveLetter);

// Folders hidden from search results (see README for why this filters
// results rather than the index itself). Paths are compared
// case-insensitively with a path-separator boundary check, so excluding
// "C:\Temp" doesn't also exclude "C:\TempFiles".
std::vector<std::wstring> LoadExcludeFolders();
void SaveExcludeFolders(const std::vector<std::wstring>& folders);

// Results-list font/colors chosen via View > Font and Colors. valid=false
// (LoadDisplaySettings's default when nothing's saved yet, or the save is
// corrupt) means "use the control's normal default appearance" - callers
// shouldn't build an HFONT/apply colors in that case.
struct DisplaySettings {
    bool valid = false;
    std::wstring fontFace;
    int fontSize = 9;
    bool bold = false;
    bool italic = false;
    COLORREF textColor = RGB(0, 0, 0);
    COLORREF bgColor = RGB(255, 255, 255);
};
DisplaySettings LoadDisplaySettings();
void SaveDisplaySettings(const DisplaySettings& settings);
