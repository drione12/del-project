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
