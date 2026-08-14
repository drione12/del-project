#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commctrl.h>
#include <commdlg.h>
#include <objbase.h>
#include <oleidl.h>
#include <shellapi.h>
#include <shlobj.h>
#include <winioctl.h>

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <functional>
#include <memory>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>
#include <wchar.h>

#include "ntfs_index.h"
#include "privileges.h"
#include "resource.h"
#include "settings.h"
#include "text_match.h"
#include "usn_watcher.h"
#include "volume_utils.h"

namespace {

constexpr int kSearchBoxId = 101;
constexpr int kStatusId = 102;
constexpr int kResultsId = 103;
constexpr UINT kMsgIndexReady = WM_APP + 1;
constexpr UINT kMsgTrayIcon = WM_APP + 2;
constexpr size_t kMaxResults = 2000;
constexpr UINT kTrayIconId = 1;
constexpr int kShowHideHotkeyId = 1;
const wchar_t* kSingleInstanceMutexName = L"EverythingClone_SingleInstance_ceb2f6a1";
const wchar_t* kWindowClassName = L"EverythingCloneWindow";

// Menu/accelerator command IDs now live in resource.h - the resource
// compiler (app.rc's new ACCELERATORS table) only understands #define, not
// this C++ enum they used to be, so both files need the same plain
// numeric constants. Menu structure/labels are pulled from the real
// Everything.exe's own strings (File/Edit/Search/View are wired to
// existing functionality; Bookmarks shows real labels but is disabled -
// nothing backs it yet. ETP/FTP server items are deliberately omitted from Tools
// per the user's request.

std::vector<std::unique_ptr<NtfsIndex>> g_volumes;
std::vector<SearchResult> g_results;
std::atomic<bool> g_running{true};
HWND g_searchBox = nullptr;
HWND g_status = nullptr;
HWND g_resultsView = nullptr;
int g_sortColumn = 0;  // 0 = Name, 1 = Path, 2 = Size, 3 = Date modified
bool g_sortAscending = true;
std::unordered_map<std::wstring, int> g_iconCache;  // extension (or a sentinel) -> icon index
std::vector<std::wstring> g_excludeFolders;  // hidden from results, see settings.h

// Persistent search toggles set from the Search menu - applied to every
// search regardless of what's typed, matching real Everything's checkable
// Match Case/Whole Word/Path/Regex menu items.
bool g_matchCase = false;
bool g_matchWholeWord = false;
bool g_matchPath = false;
bool g_useRegex = false;

NOTIFYICONDATAW g_trayIcon{};
HFONT g_resultsFont = nullptr;  // owned; replaced (old one deleted) on each font change

// Shows+focuses or hides the main window, shared by tray icon
// click/double-click and the global show/hide hotkey.
void ToggleMainWindow(HWND hwnd) {
    if (IsWindowVisible(hwnd)) {
        ShowWindow(hwnd, SW_HIDE);
    } else {
        ShowWindow(hwnd, SW_SHOW);
        if (IsIconic(hwnd)) ShowWindow(hwnd, SW_RESTORE);
        SetForegroundWindow(hwnd);
        SetFocus(g_searchBox);
    }
}

void SplitNameAndDir(const std::wstring& fullPath, std::wstring& name, std::wstring& dir) {
    size_t pos = fullPath.find_last_of(L'\\');
    if (pos == std::wstring::npos) {
        name = fullPath;
        dir.clear();
    } else {
        name = fullPath.substr(pos + 1);
        dir = fullPath.substr(0, pos);
    }
}

std::wstring FormatSize(uint64_t bytes, bool isDirectory) {
    if (isDirectory) return L"";
    const wchar_t* units[] = {L"bytes", L"KB", L"MB", L"GB", L"TB"};
    double size = static_cast<double>(bytes);
    int unitIndex = 0;
    while (size >= 1024.0 && unitIndex < 4) {
        size /= 1024.0;
        unitIndex++;
    }
    wchar_t buf[64];
    if (unitIndex == 0) {
        swprintf_s(buf, L"%llu %s", static_cast<unsigned long long>(bytes), units[0]);
    } else {
        swprintf_s(buf, L"%.1f %s", size, units[unitIndex]);
    }
    return buf;
}

std::wstring FormatFileTime(uint64_t fileTimeValue) {
    if (fileTimeValue == 0) return L"";

    FILETIME ft;
    ft.dwLowDateTime = static_cast<DWORD>(fileTimeValue & 0xFFFFFFFFu);
    ft.dwHighDateTime = static_cast<DWORD>(fileTimeValue >> 32);

    FILETIME localFt;
    if (!FileTimeToLocalFileTime(&ft, &localFt)) return L"";

    SYSTEMTIME st;
    if (!FileTimeToSystemTime(&localFt, &st)) return L"";

    wchar_t buf[64];
    swprintf_s(buf, L"%04d-%02d-%02d %02d:%02d", st.wYear, st.wMonth, st.wDay, st.wHour,
               st.wMinute);
    return buf;
}

// Compact attrib-style letter string (e.g. "RHA"), using the same
// letter<->FILE_ATTRIBUTE_* mapping as query.cpp's attrib: filter so the
// column and the search syntax stay consistent.
std::wstring FormatAttributes(DWORD attributes) {
    struct { DWORD bit; wchar_t letter; } kFlags[] = {
        {FILE_ATTRIBUTE_READONLY, L'R'},  {FILE_ATTRIBUTE_HIDDEN, L'H'},
        {FILE_ATTRIBUTE_SYSTEM, L'S'},    {FILE_ATTRIBUTE_DIRECTORY, L'D'},
        {FILE_ATTRIBUTE_ARCHIVE, L'A'},   {FILE_ATTRIBUTE_COMPRESSED, L'C'},
        {FILE_ATTRIBUTE_ENCRYPTED, L'E'}, {FILE_ATTRIBUTE_TEMPORARY, L'T'},
        {FILE_ATTRIBUTE_OFFLINE, L'O'},   {FILE_ATTRIBUTE_REPARSE_POINT, L'L'},
    };
    std::wstring out;
    for (auto& f : kFlags) {
        if (attributes & f.bit) out += f.letter;
    }
    return out;
}

// Extension without the leading dot, for display - empty for directories,
// extension-less files, and dotfiles (a leading dot with nothing before it
// is a name, not an extension, matching Explorer's convention).
std::wstring GetExtensionDisplay(const SearchResult& r, const std::wstring& name) {
    if (r.attributes & FILE_ATTRIBUTE_DIRECTORY) return L"";
    size_t dot = name.find_last_of(L'.');
    if (dot == std::wstring::npos || dot == 0) return L"";
    return name.substr(dot + 1);
}

// Extensions whose icon isn't determined by the extension alone - each
// individual .exe/.dll/etc. can embed its own distinct icon resource, unlike
// e.g. .txt where every file shares one icon for the type.
bool HasPerFileIcon(const std::wstring& ext) {
    static const std::unordered_map<std::wstring, bool> kPerFile = {
        {L".exe", true}, {L".dll", true}, {L".ico", true},
        {L".lnk", true}, {L".scr", true}, {L".cpl", true}, {L".url", true},
    };
    return kPerFile.count(ext) != 0;
}

// Looks up the shared shell icon index for a result. Folders and most file
// types are cached by extension (or a sentinel) via a fast
// SHGFI_USEFILEATTRIBUTES lookup that never touches the actual file, since
// every file of that type shares one icon. Types with a real per-file icon
// (see HasPerFileIcon) instead look up - and cache by - the actual path, the
// only way to get the icon embedded in that specific file rather than a
// generic placeholder.
int GetIconIndex(const SearchResult& r) {
    bool isDir = (r.attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;

    std::wstring ext;
    if (!isDir) {
        std::wstring name, dir;
        SplitNameAndDir(r.path, name, dir);
        size_t dot = name.find_last_of(L'.');
        ext = (dot == std::wstring::npos) ? std::wstring() : ToLower(name.substr(dot));
    }

    if (!isDir && HasPerFileIcon(ext)) {
        auto it = g_iconCache.find(r.path);
        if (it != g_iconCache.end()) return it->second;
        SHFILEINFOW sfi{};
        SHGetFileInfoW(r.path.c_str(), 0, &sfi, sizeof(sfi), SHGFI_SYSICONINDEX | SHGFI_SMALLICON);
        g_iconCache[r.path] = sfi.iIcon;
        return sfi.iIcon;
    }

    std::wstring key = isDir ? L"\\dir" : (ext.empty() ? L"\\noext" : ext);
    auto it = g_iconCache.find(key);
    if (it != g_iconCache.end()) return it->second;

    std::wstring probe = isDir ? L"folder" : (ext.empty() ? L"file" : ext);
    DWORD attrs = isDir ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_NORMAL;
    SHFILEINFOW sfi{};
    SHGetFileInfoW(probe.c_str(), attrs, &sfi, sizeof(sfi),
                   SHGFI_SYSICONINDEX | SHGFI_SMALLICON | SHGFI_USEFILEATTRIBUTES);
    g_iconCache[key] = sfi.iIcon;
    return sfi.iIcon;
}

void SortResults() {
    std::sort(g_results.begin(), g_results.end(),
               [](const SearchResult& a, const SearchResult& b) {
                   switch (g_sortColumn) {
                       case 2:
                           return g_sortAscending ? a.size < b.size : a.size > b.size;
                       case 3:
                           return g_sortAscending ? a.modifiedTime < b.modifiedTime
                                                   : a.modifiedTime > b.modifiedTime;
                       case 4:
                           return g_sortAscending ? a.createdTime < b.createdTime
                                                   : a.createdTime > b.createdTime;
                       case 5:
                           return g_sortAscending ? a.accessedTime < b.accessedTime
                                                   : a.accessedTime > b.accessedTime;
                       case 6: {
                           std::wstring na, nb, dirA, dirB;
                           SplitNameAndDir(a.path, na, dirA);
                           SplitNameAndDir(b.path, nb, dirB);
                           std::wstring extA = GetExtensionDisplay(a, na);
                           std::wstring extB = GetExtensionDisplay(b, nb);
                           int cmp = _wcsicmp(extA.c_str(), extB.c_str());
                           return g_sortAscending ? cmp < 0 : cmp > 0;
                       }
                       case 7:
                           return g_sortAscending ? a.attributes < b.attributes
                                                   : a.attributes > b.attributes;
                       case 0: {
                           std::wstring na, nb, dirA, dirB;
                           SplitNameAndDir(a.path, na, dirA);
                           SplitNameAndDir(b.path, nb, dirB);
                           int cmp = _wcsicmp(na.c_str(), nb.c_str());
                           return g_sortAscending ? cmp < 0 : cmp > 0;
                       }
                       default: {
                           int cmp = _wcsicmp(a.path.c_str(), b.path.c_str());
                           return g_sortAscending ? cmp < 0 : cmp > 0;
                       }
                   }
               });
}

void UpdateSortHeaderIndicator() {
    HWND header = ListView_GetHeader(g_resultsView);
    int count = Header_GetItemCount(header);
    for (int i = 0; i < count; i++) {
        HDITEMW hdi{};
        hdi.mask = HDI_FORMAT;
        Header_GetItem(header, i, &hdi);
        hdi.fmt &= ~(HDF_SORTUP | HDF_SORTDOWN);
        if (i == g_sortColumn) {
            hdi.fmt |= g_sortAscending ? HDF_SORTUP : HDF_SORTDOWN;
        }
        Header_SetItem(header, i, &hdi);
    }
}

std::vector<SearchResult> SearchAll(const std::wstring& query) {
    std::vector<SearchResult> results;
    for (auto& volume : g_volumes) {
        if (results.size() >= kMaxResults) break;
        auto partial = volume->Search(query, kMaxResults - results.size(), g_excludeFolders);
        results.insert(results.end(), partial.begin(), partial.end());
    }
    return results;
}

size_t TotalCount() {
    size_t total = 0;
    for (auto& v : g_volumes) total += v->Count();
    return total;
}

// Prepends the query.cpp keyword for each active Search-menu toggle, so menu
// state applies regardless of what's typed - reuses the existing parser
// as-is instead of threading toggle state through NtfsIndex::Search.
std::wstring BuildEffectiveQuery(const std::wstring& typed) {
    std::wstring q = typed;
    if (g_matchCase) q = L"case: " + q;
    if (g_matchWholeWord) q = L"wholeword: " + q;
    if (g_matchPath) q = L"path: " + q;
    if (g_useRegex) q = L"regex: " + q;
    return q;
}

void RunSearch() {
    wchar_t buf[1024];
    GetWindowTextW(g_searchBox, buf, 1024);
    g_results = SearchAll(BuildEffectiveQuery(buf));
    SortResults();

    ListView_SetItemCountEx(g_resultsView, g_results.size(), LVSICF_NOSCROLL);
    InvalidateRect(g_resultsView, nullptr, FALSE);

    wchar_t status[256];
    swprintf_s(status, L"%zu / %zu개 표시", g_results.size(), TotalCount());
    SetWindowTextW(g_status, status);
}

// Runs on a background thread: builds the index for every NTFS volume, starts
// a USN watcher per volume, then hands control back to the UI thread.
void IndexingThread(HWND hwnd) {
    EnablePrivilege(SE_BACKUP_NAME);

    auto drives = DetectNtfsFixedDrives();
    for (size_t i = 0; i < drives.size(); i++) {
        g_volumes.push_back(std::make_unique<NtfsIndex>());
    }

    std::vector<std::thread> buildThreads;
    for (size_t i = 0; i < drives.size(); i++) {
        buildThreads.emplace_back([i, &drives]() {
            // A saved snapshot from a clean previous exit plus a bounded USN
            // catch-up read is much cheaper than a full MFT re-enumeration -
            // fall back to that full scan only if there's no snapshot, it's
            // corrupt, or its journal position can no longer be trusted
            // (journal reset while the app was closed).
            DWORDLONG savedJournalId = 0;
            USN savedUsn = 0;
            bool usedSnapshot =
                g_volumes[i]->LoadFromFile(GetIndexFilePath(drives[i]), drives[i], savedJournalId,
                                            savedUsn) &&
                UsnWatcher::CatchUp(drives[i], *g_volumes[i], savedJournalId, savedUsn);
            if (!usedSnapshot) {
                std::wstring error;
                g_volumes[i]->BuildFromVolume(drives[i], error);
            }
        });
    }
    for (auto& t : buildThreads) t.join();

    for (size_t i = 0; i < drives.size(); i++) {
        if (g_volumes[i]->Count() > 0) {
            std::thread(UsnWatcher::WatchLoop, drives[i], std::ref(*g_volumes[i]),
                        std::ref(g_running))
                .detach();
        }
    }

    PostMessage(hwnd, kMsgIndexReady, 0, 0);
}

bool GetSelectedResult(int& indexOut, std::wstring& pathOut) {
    int selected = ListView_GetNextItem(g_resultsView, -1, LVNI_SELECTED);
    if (selected < 0 || static_cast<size_t>(selected) >= g_results.size()) return false;
    indexOut = selected;
    pathOut = g_results[selected].path;
    return true;
}

// Collects every selected row's path - GetSelectedResult above only ever
// returns the first, which was fine while the results view was
// LVS_SINGLESEL but undercounts now that it supports multi-select.
void GetSelectedResults(std::vector<std::wstring>& pathsOut) {
    pathsOut.clear();
    int i = -1;
    while ((i = ListView_GetNextItem(g_resultsView, i, LVNI_SELECTED)) != -1) {
        if (static_cast<size_t>(i) < g_results.size()) {
            pathsOut.push_back(g_results[i].path);
        }
    }
}

void ActionOpen(const std::wstring& path) {
    ShellExecuteW(nullptr, L"open", path.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
}

void ActionOpenContainingFolder(const std::wstring& path) {
    std::wstring arg = L"/select,\"" + path + L"\"";
    ShellExecuteW(nullptr, L"open", L"explorer.exe", arg.c_str(), nullptr, SW_SHOWNORMAL);
}

void CopyTextToClipboard(HWND hwnd, const std::wstring& text) {
    if (!OpenClipboard(hwnd)) return;
    EmptyClipboard();
    size_t bytes = (text.size() + 1) * sizeof(wchar_t);
    HGLOBAL mem = GlobalAlloc(GMEM_MOVEABLE, bytes);
    if (mem) {
        void* dst = GlobalLock(mem);
        memcpy(dst, text.c_str(), bytes);
        GlobalUnlock(mem);
        SetClipboardData(CF_UNICODETEXT, mem);
    }
    CloseClipboard();
}

// Copies (or, with cut=true, marks-for-move) the file(s) themselves (not
// just path text) to the clipboard as a CF_HDROP, so they can be pasted
// into Explorer like a real Ctrl+C/Ctrl+X would - DROPFILES supports any
// number of items, back to back, each individually null-terminated, with
// one extra null terminating the whole list. "Preferred DropEffect" is a
// Windows-Explorer-specific registered clipboard format (not part of the
// CF_HDROP standard itself) that Explorer checks on paste to decide move
// vs. copy, and whether to dim the source icons in the meantime.
void ActionCopyAsFileObject(HWND hwnd, const std::vector<std::wstring>& paths, bool cut = false) {
    if (paths.empty()) return;
    if (!OpenClipboard(hwnd)) return;
    EmptyClipboard();

    size_t charCount = 1;  // final list terminator
    for (auto& p : paths) charCount += p.size() + 1;  // item text + its own terminator
    size_t dropSize = sizeof(DROPFILES) + charCount * sizeof(wchar_t);
    HGLOBAL mem = GlobalAlloc(GHND, dropSize);
    if (mem) {
        auto* df = static_cast<DROPFILES*>(GlobalLock(mem));
        df->pFiles = sizeof(DROPFILES);
        df->fWide = TRUE;
        auto* dst = reinterpret_cast<wchar_t*>(reinterpret_cast<BYTE*>(df) + sizeof(DROPFILES));
        for (auto& p : paths) {
            wcscpy_s(dst, p.size() + 1, p.c_str());
            dst += p.size() + 1;
        }
        // GHND zero-initializes the allocation, so the final list null
        // terminator is already in place after the last item's own.
        GlobalUnlock(mem);
        SetClipboardData(CF_HDROP, mem);
    }

    if (cut) {
        UINT cfDropEffect = RegisterClipboardFormatW(L"Preferred DropEffect");
        HGLOBAL effectMem = GlobalAlloc(GHND, sizeof(DWORD));
        if (effectMem) {
            auto* effect = static_cast<DWORD*>(GlobalLock(effectMem));
            *effect = DROPEFFECT_MOVE;
            GlobalUnlock(effectMem);
            SetClipboardData(cfDropEffect, effectMem);
        }
    }

    CloseClipboard();
}

// Recycle-Bin delete for one or more selected items.
void ActionDelete(HWND hwnd, const std::vector<std::wstring>& paths) {
    if (paths.empty()) return;
    std::wstring msg = paths.size() == 1
                            ? (L"다음을 삭제하시겠습니까?\n\n" + paths[0])
                            : (L"선택한 " + std::to_wstring(paths.size()) + L"개 항목을 삭제하시겠습니까?");
    if (MessageBoxW(hwnd, msg.c_str(), L"삭제 확인", MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) != IDYES) return;

    std::wstring doubleNull;
    for (auto& p : paths) {
        doubleNull += p;
        doubleNull += L'\0';
    }
    doubleNull += L'\0';

    SHFILEOPSTRUCTW op{};
    op.hwnd = hwnd;
    op.wFunc = FO_DELETE;
    op.pFrom = doubleNull.c_str();
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION;
    int result = SHFileOperationW(&op);

    if (result != 0 || op.fAnyOperationsAborted) {
        MessageBoxW(hwnd, L"일부 또는 전체 항목을 삭제하지 못했습니다.", L"삭제 실패", MB_OK | MB_ICONERROR);
    }
    // Re-run the search rather than assume which specific rows succeeded -
    // SHFileOperationW doesn't report per-item results for a multi-item
    // pFrom, and this also fixes the previous version's real bug of
    // removing every selected row from g_results unconditionally, even on
    // failure.
    RunSearch();
}

// Runs an external command line and waits for it to finish, with no
// visible console window - used for the attrib/takeown/icacls fallback
// below. None of the three commands' own exit codes are checked
// individually (mirroring memory_master/core/ownership.py's Python
// equivalent in the other app in this repo, which does the same) - the
// real signal is whether the delete retry succeeds afterward.
void RunCommandAndWait(std::wstring commandLine) {
    STARTUPINFOW si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};
    // CreateProcessW may write into this buffer in place, so it can't be a
    // string literal or other read-only data - commandLine is taken by
    // value specifically so &commandLine[0] is always a mutable,
    // null-terminated buffer this call owns.
    if (CreateProcessW(nullptr, &commandLine[0], nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr,
                        nullptr, &si, &pi)) {
        WaitForSingleObject(pi.hProcess, 10000);
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
    }
}

// Clears read-only/system/hidden attributes and takes ownership so a
// subsequent delete retry has a real chance of succeeding where a plain
// one just failed - the same attrib/takeown/icacls sequence
// memory_master/core/ownership.py already uses successfully for the same
// purpose in this repo's other app.
void TryOwnershipOverride(const std::wstring& path) {
    RunCommandAndWait(L"attrib -r -s -h \"" + path + L"\"");
    RunCommandAndWait(L"takeown /f \"" + path + L"\"");

    wchar_t username[256]{};
    GetEnvironmentVariableW(L"USERNAME", username, 256);
    RunCommandAndWait(L"icacls \"" + path + L"\" /grant \"" + username + L":F\" /c /q");
}

bool DeletePermanently(HWND hwnd, const std::wstring& path) {
    std::wstring doubleNull = path + L'\0' + L'\0';
    SHFILEOPSTRUCTW op{};
    op.hwnd = hwnd;
    op.wFunc = FO_DELETE;
    op.pFrom = doubleNull.c_str();
    op.fFlags = FOF_NOCONFIRMATION;  // no FOF_ALLOWUNDO - bypasses the Recycle Bin, unlike ActionDelete
    int result = SHFileOperationW(&op);
    return result == 0 && !op.fAnyOperationsAborted;
}

// Permanent delete for one or more selected items - the one real
// difference from ActionDelete above. On failure, retries once after
// TryOwnershipOverride. Does not attempt to detect or kill a locking
// process (Memory Master's Python force-delete does, via a much larger
// API surface - Restart Manager - out of scope for this round); a locked
// file just stays reported as a failure.
void ActionForceDelete(HWND hwnd, const std::vector<std::wstring>& paths) {
    if (paths.empty()) return;
    std::wstring msg =
        paths.size() == 1
            ? (L"다음을 영구적으로 삭제하시겠습니까? 이 작업은 휴지통을 거치지 않으며 되돌릴 수 "
               L"없습니다.\n\n" +
               paths[0])
            : (L"선택한 " + std::to_wstring(paths.size()) +
               L"개 항목을 영구적으로 삭제하시겠습니까? 이 작업은 휴지통을 거치지 않으며 되돌릴 수 "
               L"없습니다.");
    if (MessageBoxW(hwnd, msg.c_str(), L"강제 삭제 확인", MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) !=
        IDYES) {
        return;
    }

    int failedCount = 0;
    for (auto& path : paths) {
        if (DeletePermanently(hwnd, path)) continue;
        TryOwnershipOverride(path);
        if (!DeletePermanently(hwnd, path)) failedCount++;
    }

    if (failedCount > 0) {
        MessageBoxW(hwnd, (std::to_wstring(failedCount) + L"개 항목을 삭제하지 못했습니다.").c_str(),
                    L"삭제 실패", MB_OK | MB_ICONERROR);
    }
    RunSearch();
}

void ActionProperties(HWND hwnd, const std::wstring& path) {
    SHELLEXECUTEINFOW sei{};
    sei.cbSize = sizeof(sei);
    sei.fMask = SEE_MASK_INVOKEIDLIST;
    sei.hwnd = hwnd;
    sei.lpVerb = L"properties";
    sei.lpFile = path.c_str();
    sei.nShow = SW_SHOWNORMAL;
    ShellExecuteExW(&sei);
}

// Resizes+centers the main window on its monitor's work area. Restores
// first if maximized, since SetWindowPos on a zoomed window is a no-op.
void ApplyWindowSizePreset(HWND hwnd, int width, int height) {
    if (IsZoomed(hwnd)) ShowWindow(hwnd, SW_RESTORE);
    RECT workArea{};
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &workArea, 0);
    int x = workArea.left + ((workArea.right - workArea.left) - width) / 2;
    int y = workArea.top + ((workArea.bottom - workArea.top) - height) / 2;
    SetWindowPos(hwnd, nullptr, x, y, width, height, SWP_NOZORDER);
}

// Builds an HFONT from saved settings and applies it plus the saved
// text/background colors to the results list. No-op when settings.valid
// is false (nothing saved yet) - the control just keeps its normal
// default appearance.
void ApplyDisplaySettings(HWND resultsView, const DisplaySettings& s) {
    if (!s.valid) return;

    LOGFONTW lf{};
    HDC screenDc = GetDC(nullptr);
    lf.lfHeight = -MulDiv(s.fontSize, GetDeviceCaps(screenDc, LOGPIXELSY), 72);
    ReleaseDC(nullptr, screenDc);
    lf.lfWeight = s.bold ? FW_BOLD : FW_NORMAL;
    lf.lfItalic = s.italic ? TRUE : FALSE;
    lf.lfCharSet = DEFAULT_CHARSET;
    lf.lfOutPrecision = OUT_DEFAULT_PRECIS;
    lf.lfClipPrecision = CLIP_DEFAULT_PRECIS;
    lf.lfQuality = DEFAULT_QUALITY;
    lf.lfPitchAndFamily = DEFAULT_PITCH | FF_DONTCARE;
    wcsncpy_s(lf.lfFaceName, s.fontFace.c_str(), _TRUNCATE);

    HFONT newFont = CreateFontIndirectW(&lf);
    if (!newFont) return;
    if (g_resultsFont) DeleteObject(g_resultsFont);
    g_resultsFont = newFont;

    SendMessageW(resultsView, WM_SETFONT, reinterpret_cast<WPARAM>(g_resultsFont), TRUE);
    ListView_SetTextColor(resultsView, s.textColor);
    ListView_SetBkColor(resultsView, s.bgColor);
    ListView_SetTextBkColor(resultsView, s.bgColor);
}

// Standard Windows font picker (which itself includes a text-color combo
// via CF_EFFECTS) followed by a standard color picker for the background -
// two system dialogs plus an explanatory prompt in between, rather than a
// custom single dialog with a live preview like real Everything's. A
// hand-built preview dialog isn't something worth the risk of shipping
// unverified (see the Options window comment for why).
void ShowFontAndColorDialog(HWND hwnd) {
    DisplaySettings current = LoadDisplaySettings();

    LOGFONTW lf{};
    if (current.valid) {
        HDC screenDc = GetDC(nullptr);
        lf.lfHeight = -MulDiv(current.fontSize, GetDeviceCaps(screenDc, LOGPIXELSY), 72);
        ReleaseDC(nullptr, screenDc);
        lf.lfWeight = current.bold ? FW_BOLD : FW_NORMAL;
        lf.lfItalic = current.italic ? TRUE : FALSE;
        wcsncpy_s(lf.lfFaceName, current.fontFace.c_str(), _TRUNCATE);
    } else {
        lf.lfHeight = -12;
        wcscpy_s(lf.lfFaceName, L"Segoe UI");
    }

    CHOOSEFONTW cf{};
    cf.lStructSize = sizeof(cf);
    cf.hwndOwner = hwnd;
    cf.lpLogFont = &lf;
    cf.rgbColors = current.valid ? current.textColor : RGB(0, 0, 0);
    cf.Flags = CF_SCREENFONTS | CF_EFFECTS | CF_INITTOLOGFONTSTRUCT;
    if (!ChooseFontW(&cf)) return;

    MessageBoxW(hwnd, L"이제 배경색을 선택하세요.", L"글꼴 및 색", MB_OK | MB_ICONINFORMATION);

    static COLORREF customColors[16] = {};
    CHOOSECOLORW cc{};
    cc.lStructSize = sizeof(cc);
    cc.hwndOwner = hwnd;
    cc.rgbResult = current.valid ? current.bgColor : RGB(255, 255, 255);
    cc.lpCustColors = customColors;
    cc.Flags = CC_FULLOPEN | CC_RGBINIT;
    if (!ChooseColorW(&cc)) return;

    DisplaySettings s;
    s.valid = true;
    s.fontFace = lf.lfFaceName;
    HDC screenDc = GetDC(nullptr);
    s.fontSize = -MulDiv(lf.lfHeight, 72, GetDeviceCaps(screenDc, LOGPIXELSY));
    ReleaseDC(nullptr, screenDc);
    s.bold = lf.lfWeight >= FW_BOLD;
    s.italic = lf.lfItalic != 0;
    s.textColor = cf.rgbColors;
    s.bgColor = cc.rgbResult;

    SaveDisplaySettings(s);
    ApplyDisplaySettings(g_resultsView, s);
}

constexpr int kOptionsListId = 201;
constexpr int kOptionsAddBtnId = 202;
constexpr int kOptionsRemoveBtnId = 203;
HWND g_optionsWnd = nullptr;

void RefreshOptionsList(HWND listBox) {
    SendMessageW(listBox, LB_RESETCONTENT, 0, 0);
    for (auto& folder : g_excludeFolders) {
        SendMessageW(listBox, LB_ADDSTRING, 0, reinterpret_cast<LPARAM>(folder.c_str()));
    }
}

// Minimal options window: manage the exclude-folder list (settings.h).
// Modeless (not a true modal dialog) and mouse-only (no IsDialogMessage
// Tab-navigation) to keep this a plain owned CreateWindowExW window like
// the main one, rather than a hand-built DLGTEMPLATE - there's no .rc
// dialog resource to author, and this can't be runtime-tested locally
// before shipping.
LRESULT CALLBACK OptionsWndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
        case WM_CREATE: {
            CreateWindowExW(0, L"STATIC", L"검색 결과에서 숨길 폴더:", WS_CHILD | WS_VISIBLE, 8,
                             8, 360, 18, hwnd, nullptr, nullptr, nullptr);
            HWND list = CreateWindowExW(
                WS_EX_CLIENTEDGE, L"LISTBOX", L"",
                WS_CHILD | WS_VISIBLE | WS_VSCROLL | LBS_NOTIFY, 8, 28, 360, 160, hwnd,
                reinterpret_cast<HMENU>(static_cast<INT_PTR>(kOptionsListId)), nullptr, nullptr);
            RefreshOptionsList(list);
            CreateWindowExW(
                0, L"BUTTON", L"폴더 추가...(&A)", WS_CHILD | WS_VISIBLE, 8, 196, 120, 28, hwnd,
                reinterpret_cast<HMENU>(static_cast<INT_PTR>(kOptionsAddBtnId)), nullptr, nullptr);
            CreateWindowExW(
                0, L"BUTTON", L"제거(&R)", WS_CHILD | WS_VISIBLE, 136, 196, 120, 28, hwnd,
                reinterpret_cast<HMENU>(static_cast<INT_PTR>(kOptionsRemoveBtnId)), nullptr,
                nullptr);
            return 0;
        }
        case WM_COMMAND: {
            HWND list = GetDlgItem(hwnd, kOptionsListId);
            if (LOWORD(wParam) == kOptionsAddBtnId) {
                wchar_t pathBuf[MAX_PATH]{};
                wchar_t displayName[MAX_PATH]{};
                BROWSEINFOW bi{};
                bi.hwndOwner = hwnd;
                bi.pszDisplayName = displayName;
                bi.lpszTitle = L"검색 결과에서 숨길 폴더를 선택하세요";
                bi.ulFlags = BIF_RETURNONLYFSDIRS;
                LPITEMIDLIST pidl = SHBrowseForFolderW(&bi);
                if (pidl) {
                    if (SHGetPathFromIDListW(pidl, pathBuf)) {
                        g_excludeFolders.emplace_back(pathBuf);
                        SaveExcludeFolders(g_excludeFolders);
                        RefreshOptionsList(list);
                    }
                    CoTaskMemFree(pidl);
                }
            } else if (LOWORD(wParam) == kOptionsRemoveBtnId) {
                int sel = static_cast<int>(SendMessageW(list, LB_GETCURSEL, 0, 0));
                if (sel != LB_ERR && static_cast<size_t>(sel) < g_excludeFolders.size()) {
                    g_excludeFolders.erase(g_excludeFolders.begin() + sel);
                    SaveExcludeFolders(g_excludeFolders);
                    RefreshOptionsList(list);
                }
            }
            return 0;
        }
        case WM_CLOSE:
            DestroyWindow(hwnd);
            return 0;
        case WM_DESTROY:
            g_optionsWnd = nullptr;
            return 0;
    }
    return DefWindowProcW(hwnd, msg, wParam, lParam);
}

void ShowOptionsWindow(HWND owner) {
    if (g_optionsWnd) {
        SetForegroundWindow(g_optionsWnd);
        return;
    }

    static bool classRegistered = false;
    if (!classRegistered) {
        WNDCLASSEXW wc{};
        wc.cbSize = sizeof(wc);
        wc.lpfnWndProc = OptionsWndProc;
        wc.hInstance = GetModuleHandleW(nullptr);
        wc.lpszClassName = L"EverythingCloneOptionsWindow";
        wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
        wc.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_BTNFACE + 1);
        RegisterClassExW(&wc);
        classRegistered = true;
    }

    g_optionsWnd = CreateWindowExW(WS_EX_DLGMODALFRAME, L"EverythingCloneOptionsWindow", L"옵션",
                                    WS_POPUP | WS_CAPTION | WS_SYSMENU, CW_USEDEFAULT,
                                    CW_USEDEFAULT, 392, 270, owner, nullptr,
                                    GetModuleHandleW(nullptr), nullptr);
    if (g_optionsWnd) ShowWindow(g_optionsWnd, SW_SHOW);
}

// Menu structure and labels pulled from the real Everything.exe's own
// strings. Bookmarks shows the real labels for visual parity but is
// disabled - nothing backs it yet. ETP/FTP server items are deliberately
// left out of Tools.
HMENU CreateAppMenu() {
    HMENU menuBar = CreateMenu();

    HMENU fileMenu = CreatePopupMenu();
    AppendMenuW(fileMenu, MF_STRING, IDM_FILE_OPEN, L"열기(&O)");
    AppendMenuW(fileMenu, MF_STRING, IDM_FILE_OPENPATH, L"경로 열기(&P)");
    AppendMenuW(fileMenu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(fileMenu, MF_STRING, IDM_FILE_COPYPATH, L"경로를 클립보드로 복사");
    AppendMenuW(fileMenu, MF_STRING, IDM_FILE_COPYFULLNAME, L"전체 이름을 클립보드로 복사");
    AppendMenuW(fileMenu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(fileMenu, MF_STRING, IDM_FILE_PROPERTIES, L"속성(&R)");
    AppendMenuW(fileMenu, MF_STRING, IDM_FILE_DELETE, L"삭제(&D)");
    AppendMenuW(fileMenu, MF_STRING, IDM_FILE_REFRESH, L"새로 고침(&F)");
    AppendMenuW(fileMenu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(fileMenu, MF_STRING, IDM_FILE_CLOSE, L"닫기(&C)");
    AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(fileMenu), L"파일(&F)");

    HMENU editMenu = CreatePopupMenu();
    AppendMenuW(editMenu, MF_STRING, IDM_EDIT_COPY, L"복사(&C)");
    AppendMenuW(editMenu, MF_STRING, IDM_EDIT_CUT, L"잘라내기(&T)");
    AppendMenuW(editMenu, MF_STRING, IDM_EDIT_SELECTALL, L"모두 선택(&A)");
    AppendMenuW(editMenu, MF_STRING | MF_GRAYED, 0, L"선택 반전(&I)");
    AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(editMenu), L"편집(&E)");

    HMENU winSizeMenu = CreatePopupMenu();
    AppendMenuW(winSizeMenu, MF_STRING, IDM_VIEW_WINSIZE_SMALL, L"작게 (700 x 560)");
    AppendMenuW(winSizeMenu, MF_STRING, IDM_VIEW_WINSIZE_MEDIUM, L"보통 (950 x 700)");
    AppendMenuW(winSizeMenu, MF_STRING, IDM_VIEW_WINSIZE_LARGE, L"크게 (1200 x 850)");
    AppendMenuW(winSizeMenu, MF_STRING, IDM_VIEW_WINSIZE_MAXIMIZE, L"최대화(&M)");

    HMENU viewMenu = CreatePopupMenu();
    AppendMenuW(viewMenu, MF_POPUP, reinterpret_cast<UINT_PTR>(winSizeMenu), L"창 크기(&W)");
    AppendMenuW(viewMenu, MF_STRING, IDM_VIEW_FONTCOLOR, L"글꼴 및 색(&N)...");
    AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(viewMenu), L"보기(&V)");

    HMENU searchMenu = CreatePopupMenu();
    AppendMenuW(searchMenu, MF_STRING, IDM_SEARCH_MATCHCASE, L"대소문자 구분(&C)");
    AppendMenuW(searchMenu, MF_STRING, IDM_SEARCH_MATCHWHOLEWORD, L"전체 단어 일치(&W)");
    AppendMenuW(searchMenu, MF_STRING, IDM_SEARCH_MATCHPATH, L"전체 경로 일치(&P)");
    AppendMenuW(searchMenu, MF_STRING, IDM_SEARCH_REGEX, L"정규식 사용(&X)");
    AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(searchMenu), L"검색(&S)");

    HMENU bookmarksMenu = CreatePopupMenu();
    AppendMenuW(bookmarksMenu, MF_STRING | MF_GRAYED, 0, L"북마크에 추가...(&A)");
    AppendMenuW(bookmarksMenu, MF_STRING | MF_GRAYED, 0, L"북마크 관리...(&O)");
    AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(bookmarksMenu), L"책갈피(&B)");

    HMENU toolsMenu = CreatePopupMenu();
    AppendMenuW(toolsMenu, MF_STRING | MF_GRAYED, 0, L"폴더 인덱스...");
    AppendMenuW(toolsMenu, MF_STRING | MF_GRAYED, 0, L"파일 목록...");
    AppendMenuW(toolsMenu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(toolsMenu, MF_STRING, IDM_TOOLS_OPTIONS, L"옵션...(&O)");
    AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(toolsMenu), L"도구(&T)");
    // ETP/FTP server connect/disconnect/start/stop items intentionally omitted.

    return menuBar;
}

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
        case WM_CREATE: {
            g_excludeFolders = LoadExcludeFolders();

            g_status = CreateWindowExW(0, L"STATIC", L"NTFS 볼륨 인덱싱 중...",
                                        WS_CHILD | WS_VISIBLE, 8, 8, 600, 20, hwnd,
                                        reinterpret_cast<HMENU>(static_cast<INT_PTR>(kStatusId)),
                                        nullptr, nullptr);

            g_searchBox = CreateWindowExW(
                WS_EX_CLIENTEDGE, L"EDIT", L"",
                WS_CHILD | WS_VISIBLE | WS_DISABLED | ES_AUTOHSCROLL, 8, 32, 600, 26, hwnd,
                reinterpret_cast<HMENU>(static_cast<INT_PTR>(kSearchBoxId)), nullptr, nullptr);

            g_resultsView = CreateWindowExW(
                0, WC_LISTVIEWW, L"",
                WS_CHILD | WS_VISIBLE | WS_BORDER | LVS_REPORT | LVS_OWNERDATA | LVS_EDITLABELS,
                8, 64, 600, 400, hwnd,
                reinterpret_cast<HMENU>(static_cast<INT_PTR>(kResultsId)), nullptr, nullptr);
            ListView_SetExtendedListViewStyle(g_resultsView, LVS_EX_FULLROWSELECT);

            // One-time: grab the shell's shared small-icon image list (a real
            // existing path is expected here, unlike the per-result lookups
            // below) and attach it so rows can show file-type icons.
            SHFILEINFOW sysSfi{};
            HIMAGELIST sysImageList = reinterpret_cast<HIMAGELIST>(SHGetFileInfoW(
                L"C:\\", 0, &sysSfi, sizeof(sysSfi), SHGFI_SYSICONINDEX | SHGFI_SMALLICON));
            if (sysImageList) {
                ListView_SetImageList(g_resultsView, sysImageList, LVSIL_SMALL);
            }

            LVCOLUMNW col{};
            col.mask = LVCF_TEXT | LVCF_WIDTH;
            col.cx = 180;
            col.pszText = const_cast<LPWSTR>(L"이름");
            ListView_InsertColumn(g_resultsView, 0, &col);

            col.cx = 260;
            col.pszText = const_cast<LPWSTR>(L"경로");
            ListView_InsertColumn(g_resultsView, 1, &col);

            col.cx = 80;
            col.pszText = const_cast<LPWSTR>(L"크기");
            ListView_InsertColumn(g_resultsView, 2, &col);

            col.cx = 130;
            col.pszText = const_cast<LPWSTR>(L"수정한 날짜");
            ListView_InsertColumn(g_resultsView, 3, &col);

            col.cx = 130;
            col.pszText = const_cast<LPWSTR>(L"생성한 날짜");
            ListView_InsertColumn(g_resultsView, 4, &col);

            col.cx = 130;
            col.pszText = const_cast<LPWSTR>(L"액세스한 날짜");
            ListView_InsertColumn(g_resultsView, 5, &col);

            col.cx = 70;
            col.pszText = const_cast<LPWSTR>(L"확장자");
            ListView_InsertColumn(g_resultsView, 6, &col);

            col.cx = 70;
            col.pszText = const_cast<LPWSTR>(L"속성");
            ListView_InsertColumn(g_resultsView, 7, &col);

            ApplyDisplaySettings(g_resultsView, LoadDisplaySettings());

            g_trayIcon.cbSize = sizeof(g_trayIcon);
            g_trayIcon.hWnd = hwnd;
            g_trayIcon.uID = kTrayIconId;
            g_trayIcon.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP;
            g_trayIcon.uCallbackMessage = kMsgTrayIcon;
            g_trayIcon.hIcon = LoadIconW(nullptr, IDI_APPLICATION);
            wcscpy_s(g_trayIcon.szTip, L"EverythingClone");
            Shell_NotifyIconW(NIM_ADD, &g_trayIcon);

            RegisterHotKey(hwnd, kShowHideHotkeyId, MOD_CONTROL | MOD_ALT, VK_SPACE);

            std::thread(IndexingThread, hwnd).detach();
            return 0;
        }
        case WM_SIZE: {
            if (wParam == SIZE_MINIMIZED) {
                // Minimize-to-tray instead of leaving a taskbar entry - the
                // window is still there (SW_HIDE, not destroyed), the tray
                // icon's click handler brings it back.
                ShowWindow(hwnd, SW_HIDE);
                return 0;
            }
            int w = LOWORD(lParam), h = HIWORD(lParam);
            MoveWindow(g_status, 8, 8, w - 16, 20, TRUE);
            MoveWindow(g_searchBox, 8, 32, w - 16, 26, TRUE);
            MoveWindow(g_resultsView, 8, 64, w - 16, h - 72, TRUE);
            int totalW = w - 16 - 20;  // minus scrollbar allowance
            ListView_SetColumnWidth(g_resultsView, 0, totalW * 16 / 100);
            ListView_SetColumnWidth(g_resultsView, 1, totalW * 24 / 100);
            ListView_SetColumnWidth(g_resultsView, 2, totalW * 8 / 100);
            ListView_SetColumnWidth(g_resultsView, 3, totalW * 13 / 100);
            ListView_SetColumnWidth(g_resultsView, 4, totalW * 13 / 100);
            ListView_SetColumnWidth(g_resultsView, 5, totalW * 13 / 100);
            ListView_SetColumnWidth(g_resultsView, 6, totalW * 6 / 100);
            ListView_SetColumnWidth(g_resultsView, 7, totalW * 7 / 100);
            return 0;
        }
        case kMsgIndexReady: {
            if (TotalCount() == 0) {
                SetWindowTextW(g_status, L"인덱싱 실패 - 이 프로그램을 관리자 권한으로 다시 실행하세요");
            } else {
                EnableWindow(g_searchBox, TRUE);
                SetFocus(g_searchBox);
                // Populate the list immediately (real Everything shows every
                // indexed item by default, not a blank screen until you type)
                // - RunSearch sets its own "N / M개 표시" status text.
                RunSearch();
            }
            return 0;
        }
        case kMsgTrayIcon: {
            switch (lParam) {
                case WM_LBUTTONUP:
                case WM_LBUTTONDBLCLK:
                    ToggleMainWindow(hwnd);
                    break;
                case WM_RBUTTONUP: {
                    POINT pt;
                    GetCursorPos(&pt);
                    HMENU menu = CreatePopupMenu();
                    AppendMenuW(menu, MF_STRING, IDM_TRAY_SHOW, L"열기");
                    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
                    AppendMenuW(menu, MF_STRING, IDM_TRAY_EXIT, L"종료");
                    SetForegroundWindow(hwnd);
                    TrackPopupMenu(menu, TPM_RIGHTBUTTON, pt.x, pt.y, 0, hwnd, nullptr);
                    PostMessage(hwnd, WM_NULL, 0, 0);
                    DestroyMenu(menu);
                    break;
                }
            }
            return 0;
        }
        case WM_HOTKEY: {
            if (wParam == kShowHideHotkeyId) {
                ToggleMainWindow(hwnd);
            }
            return 0;
        }
        case WM_COMMAND: {
            if (LOWORD(wParam) == kSearchBoxId && HIWORD(wParam) == EN_CHANGE) {
                RunSearch();
                return 0;
            }

            int index;
            std::wstring path;
            switch (LOWORD(wParam)) {
                case IDM_FILE_OPEN:
                    if (GetSelectedResult(index, path)) ActionOpen(path);
                    break;
                case IDM_FILE_OPENPATH:
                    if (GetSelectedResult(index, path)) ActionOpenContainingFolder(path);
                    break;
                case IDM_FILE_COPYPATH:
                    if (GetSelectedResult(index, path)) CopyTextToClipboard(hwnd, path);
                    break;
                case IDM_FILE_COPYFULLNAME: {
                    if (GetSelectedResult(index, path)) {
                        std::wstring name, dir;
                        SplitNameAndDir(path, name, dir);
                        CopyTextToClipboard(hwnd, name);
                    }
                    break;
                }
                case IDM_FILE_PROPERTIES:
                    if (GetSelectedResult(index, path)) ActionProperties(hwnd, path);
                    break;
                case IDM_FILE_DELETE: {
                    std::vector<std::wstring> paths;
                    GetSelectedResults(paths);
                    ActionDelete(hwnd, paths);
                    break;
                }
                case IDM_FILE_FORCEDELETE: {
                    std::vector<std::wstring> paths;
                    GetSelectedResults(paths);
                    ActionForceDelete(hwnd, paths);
                    break;
                }
                case IDM_FILE_REFRESH:
                    RunSearch();
                    break;
                case IDM_FILE_CLOSE:
                    PostMessage(hwnd, WM_CLOSE, 0, 0);
                    break;
                case IDM_EDIT_COPY: {
                    std::vector<std::wstring> paths;
                    GetSelectedResults(paths);
                    ActionCopyAsFileObject(hwnd, paths);
                    break;
                }
                case IDM_EDIT_CUT: {
                    std::vector<std::wstring> paths;
                    GetSelectedResults(paths);
                    ActionCopyAsFileObject(hwnd, paths, /*cut=*/true);
                    break;
                }
                case IDM_EDIT_SELECTALL:
                    ListView_SetItemState(g_resultsView, -1, LVIS_SELECTED, LVIS_SELECTED);
                    break;
                case IDM_FILE_RENAME: {
                    int selected = ListView_GetNextItem(g_resultsView, -1, LVNI_SELECTED);
                    if (selected >= 0) {
                        SetFocus(g_resultsView);
                        ListView_EditLabel(g_resultsView, selected);
                    }
                    break;
                }
                case IDM_SEARCH_MATCHCASE:
                    g_matchCase = !g_matchCase;
                    CheckMenuItem(GetMenu(hwnd), IDM_SEARCH_MATCHCASE,
                                  MF_BYCOMMAND | (g_matchCase ? MF_CHECKED : MF_UNCHECKED));
                    RunSearch();
                    break;
                case IDM_SEARCH_MATCHWHOLEWORD:
                    g_matchWholeWord = !g_matchWholeWord;
                    CheckMenuItem(GetMenu(hwnd), IDM_SEARCH_MATCHWHOLEWORD,
                                  MF_BYCOMMAND | (g_matchWholeWord ? MF_CHECKED : MF_UNCHECKED));
                    RunSearch();
                    break;
                case IDM_SEARCH_MATCHPATH:
                    g_matchPath = !g_matchPath;
                    CheckMenuItem(GetMenu(hwnd), IDM_SEARCH_MATCHPATH,
                                  MF_BYCOMMAND | (g_matchPath ? MF_CHECKED : MF_UNCHECKED));
                    RunSearch();
                    break;
                case IDM_SEARCH_REGEX:
                    g_useRegex = !g_useRegex;
                    CheckMenuItem(GetMenu(hwnd), IDM_SEARCH_REGEX,
                                  MF_BYCOMMAND | (g_useRegex ? MF_CHECKED : MF_UNCHECKED));
                    RunSearch();
                    break;
                case IDM_VIEW_WINSIZE_SMALL:
                    ApplyWindowSizePreset(hwnd, 700, 560);
                    break;
                case IDM_VIEW_WINSIZE_MEDIUM:
                    ApplyWindowSizePreset(hwnd, 950, 700);
                    break;
                case IDM_VIEW_WINSIZE_LARGE:
                    ApplyWindowSizePreset(hwnd, 1200, 850);
                    break;
                case IDM_VIEW_WINSIZE_MAXIMIZE:
                    ShowWindow(hwnd, SW_MAXIMIZE);
                    break;
                case IDM_VIEW_FONTCOLOR:
                    ShowFontAndColorDialog(hwnd);
                    break;
                case IDM_TOOLS_OPTIONS:
                    ShowOptionsWindow(hwnd);
                    break;
                case IDM_TRAY_SHOW:
                    ShowWindow(hwnd, SW_SHOW);
                    if (IsIconic(hwnd)) ShowWindow(hwnd, SW_RESTORE);
                    SetForegroundWindow(hwnd);
                    SetFocus(g_searchBox);
                    break;
                case IDM_TRAY_EXIT:
                    PostMessage(hwnd, WM_CLOSE, 0, 0);
                    break;
            }
            return 0;
        }
        case WM_NOTIFY: {
            auto* hdr = reinterpret_cast<LPNMHDR>(lParam);
            if (hdr->idFrom != static_cast<UINT_PTR>(kResultsId)) break;

            if (hdr->code == LVN_GETDISPINFOW) {
                auto* di = reinterpret_cast<NMLVDISPINFOW*>(lParam);
                int i = di->item.iItem;
                if ((di->item.mask & LVIF_TEXT) && i >= 0 &&
                    static_cast<size_t>(i) < g_results.size()) {
                    const SearchResult& r = g_results[i];
                    std::wstring text;
                    switch (di->item.iSubItem) {
                        case 0: {
                            std::wstring name, dir;
                            SplitNameAndDir(r.path, name, dir);
                            text = name;
                            break;
                        }
                        case 1: {
                            std::wstring name, dir;
                            SplitNameAndDir(r.path, name, dir);
                            text = dir;
                            break;
                        }
                        case 2:
                            text = FormatSize(r.size, (r.attributes & FILE_ATTRIBUTE_DIRECTORY) != 0);
                            break;
                        case 3:
                            text = FormatFileTime(r.modifiedTime);
                            break;
                        case 4:
                            text = FormatFileTime(r.createdTime);
                            break;
                        case 5:
                            text = FormatFileTime(r.accessedTime);
                            break;
                        case 6: {
                            std::wstring name, dir;
                            SplitNameAndDir(r.path, name, dir);
                            text = GetExtensionDisplay(r, name);
                            break;
                        }
                        case 7:
                            text = FormatAttributes(r.attributes);
                            break;
                    }
                    wcsncpy_s(di->item.pszText, di->item.cchTextMax, text.c_str(), _TRUNCATE);
                }
                if ((di->item.mask & LVIF_IMAGE) && di->item.iSubItem == 0 && i >= 0 &&
                    static_cast<size_t>(i) < g_results.size()) {
                    di->item.iImage = GetIconIndex(g_results[i]);
                }
            } else if (hdr->code == NM_DBLCLK) {
                auto* nm = reinterpret_cast<NMITEMACTIVATE*>(lParam);
                if (nm->iItem >= 0 && static_cast<size_t>(nm->iItem) < g_results.size()) {
                    ShellExecuteW(nullptr, L"open", g_results[nm->iItem].path.c_str(), nullptr,
                                  nullptr, SW_SHOWNORMAL);
                }
            } else if (hdr->code == LVN_COLUMNCLICK) {
                auto* nmlv = reinterpret_cast<NMLISTVIEW*>(lParam);
                if (g_sortColumn == nmlv->iSubItem) {
                    g_sortAscending = !g_sortAscending;
                } else {
                    g_sortColumn = nmlv->iSubItem;
                    g_sortAscending = true;
                }
                SortResults();
                UpdateSortHeaderIndicator();
                InvalidateRect(g_resultsView, nullptr, FALSE);
            } else if (hdr->code == LVN_ENDLABELEDIT) {
                // F2 rename (ListView_EditLabel), via its built-in inline
                // edit box - LVS_OWNERDATA means the view never caches its
                // own copy of the text, so this only needs to touch
                // g_results; a later LVN_GETDISPINFOW naturally re-reads
                // whatever's there.
                auto* di = reinterpret_cast<NMLVDISPINFOW*>(lParam);
                if (di->item.pszText != nullptr && di->item.iItem >= 0 &&
                    static_cast<size_t>(di->item.iItem) < g_results.size()) {
                    std::wstring newName = di->item.pszText;
                    if (!newName.empty()) {
                        std::wstring& oldPath = g_results[di->item.iItem].path;
                        std::wstring name, dir;
                        SplitNameAndDir(oldPath, name, dir);
                        std::wstring newPath = dir.empty() ? newName : (dir + L"\\" + newName);
                        if (MoveFileW(oldPath.c_str(), newPath.c_str())) {
                            oldPath = newPath;
                            InvalidateRect(g_resultsView, nullptr, FALSE);
                        } else {
                            MessageBoxW(hwnd, L"이름을 바꾸지 못했습니다.", L"이름 바꾸기 실패",
                                        MB_OK | MB_ICONERROR);
                        }
                    }
                }
                return 0;
            }
            return 0;
        }
        case WM_CONTEXTMENU: {
            if (reinterpret_cast<HWND>(wParam) != g_resultsView) break;

            int selected = ListView_GetNextItem(g_resultsView, -1, LVNI_SELECTED);
            if (selected < 0 || static_cast<size_t>(selected) >= g_results.size()) return 0;
            const std::wstring path = g_results[selected].path;

            int x = static_cast<int>(static_cast<short>(LOWORD(lParam)));
            int y = static_cast<int>(static_cast<short>(HIWORD(lParam)));
            if (x == -1 && y == -1) {
                POINT pt;
                GetCursorPos(&pt);
                x = pt.x;
                y = pt.y;
            }

            HMENU menu = CreatePopupMenu();
            AppendMenuW(menu, MF_STRING, 1, L"열기");
            AppendMenuW(menu, MF_STRING, 2, L"포함 폴더 열기");
            AppendMenuW(menu, MF_STRING, 3, L"경로 복사");
            AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
            AppendMenuW(menu, MF_STRING, 4, L"삭제");
            AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
            AppendMenuW(menu, MF_STRING, 5, L"속성");

            // SetForegroundWindow + the WM_NULL nudge afterward is the documented fix for
            // the popup not dismissing correctly when the user clicks outside it.
            SetForegroundWindow(hwnd);
            int cmd = TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, 0, hwnd, nullptr);
            PostMessage(hwnd, WM_NULL, 0, 0);
            DestroyMenu(menu);

            switch (cmd) {
                case 1:
                    ActionOpen(path);
                    break;
                case 2:
                    ActionOpenContainingFolder(path);
                    break;
                case 3:
                    CopyTextToClipboard(hwnd, path);
                    break;
                case 4:
                    ActionDelete(hwnd, {path});
                    break;
                case 5:
                    ActionProperties(hwnd, path);
                    break;
            }
            return 0;
        }
        case WM_DESTROY:
            g_running = false;
            // Snapshot each volume so the next launch can load-and-catch-up
            // instead of re-enumerating the whole MFT again.
            for (auto& volume : g_volumes) {
                volume->SaveToFile(GetIndexFilePath(volume->Drive()));
            }
            UnregisterHotKey(hwnd, kShowHideHotkeyId);
            Shell_NotifyIconW(NIM_DELETE, &g_trayIcon);
            if (g_resultsFont) DeleteObject(g_resultsFont);
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProcW(hwnd, msg, wParam, lParam);
}

}  // namespace

int APIENTRY wWinMain(HINSTANCE hInstance, HINSTANCE, LPWSTR, int nCmdShow) {
    // Single instance: a second launch just wakes up the first one instead
    // of starting a second full volume index. The mutex handle is
    // intentionally never closed - it only needs to outlive this process,
    // and Windows cleans it up on exit.
    HANDLE singleInstanceMutex = CreateMutexW(nullptr, TRUE, kSingleInstanceMutexName);
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        HWND existing = FindWindowW(kWindowClassName, nullptr);
        if (existing) {
            ShowWindow(existing, SW_SHOW);
            if (IsIconic(existing)) ShowWindow(existing, SW_RESTORE);
            SetForegroundWindow(existing);
        }
        return 0;
    }

    // Needed for the Shell IContextMenu/IShellFolder and OLE drag-and-drop
    // (DoDragDrop) work - nothing in this app initialized COM before.
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

    INITCOMMONCONTROLSEX icc{sizeof(icc), ICC_LISTVIEW_CLASSES};
    InitCommonControlsEx(&icc);

    WNDCLASSEXW wc{};
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hInstance;
    wc.lpszClassName = kWindowClassName;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    RegisterClassExW(&wc);

    HWND hwnd = CreateWindowExW(0, kWindowClassName, L"EverythingClone", WS_OVERLAPPEDWINDOW,
                                 CW_USEDEFAULT, CW_USEDEFAULT, 700, 560, nullptr, CreateAppMenu(),
                                 hInstance, nullptr);
    if (!hwnd) return 1;

    ShowWindow(hwnd, nCmdShow);
    UpdateWindow(hwnd);

    HACCEL accel = LoadAcceleratorsW(hInstance, MAKEINTRESOURCEW(IDR_ACCELERATORS));

    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0)) {
        // Skipping TranslateAccelerator whenever the search box has focus
        // is deliberate: without it, e.g. Delete/Ctrl+A/Ctrl+C while typing
        // a query would hit the results-list accelerators below instead of
        // normal text editing in the box.
        bool searchBoxFocused = (GetFocus() == g_searchBox);
        if (searchBoxFocused || !TranslateAccelerator(hwnd, accel, &msg)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    CoUninitialize();
    return static_cast<int>(msg.wParam);
}
