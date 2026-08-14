#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commctrl.h>
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
#include "text_match.h"
#include "usn_watcher.h"
#include "volume_utils.h"

namespace {

constexpr int kSearchBoxId = 101;
constexpr int kStatusId = 102;
constexpr int kResultsId = 103;
constexpr UINT kMsgIndexReady = WM_APP + 1;
constexpr size_t kMaxResults = 2000;

// Menu command IDs. Menu structure/labels are pulled from the real
// Everything.exe's own strings (File/Edit/Search/Help are wired to existing
// functionality; View/Bookmarks/Tools show real labels but are disabled -
// nothing backs them yet. ETP/FTP server items are deliberately omitted
// from Tools per the user's request.
enum MenuCommand {
    IDM_FILE_OPEN = 2001,
    IDM_FILE_OPENPATH,
    IDM_FILE_COPYPATH,
    IDM_FILE_COPYFULLNAME,
    IDM_FILE_PROPERTIES,
    IDM_FILE_DELETE,
    IDM_FILE_REFRESH,
    IDM_FILE_CLOSE,
    IDM_EDIT_COPY,
    IDM_SEARCH_MATCHCASE,
    IDM_SEARCH_MATCHWHOLEWORD,
    IDM_SEARCH_MATCHPATH,
    IDM_SEARCH_REGEX,
    IDM_HELP_SYNTAX,
    IDM_HELP_ABOUT,
};

std::vector<std::unique_ptr<NtfsIndex>> g_volumes;
std::vector<SearchResult> g_results;
std::atomic<bool> g_running{true};
HWND g_searchBox = nullptr;
HWND g_status = nullptr;
HWND g_resultsView = nullptr;
int g_sortColumn = 0;  // 0 = Name, 1 = Path, 2 = Size, 3 = Date modified
bool g_sortAscending = true;
std::unordered_map<std::wstring, int> g_iconCache;  // extension (or a sentinel) -> icon index

// Persistent search toggles set from the Search menu - applied to every
// search regardless of what's typed, matching real Everything's checkable
// Match Case/Whole Word/Path/Regex menu items.
bool g_matchCase = false;
bool g_matchWholeWord = false;
bool g_matchPath = false;
bool g_useRegex = false;

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

// Looks up the shared shell icon index for a result, caching by extension
// (or a sentinel for folders/no-extension) so repeated files of the same
// type don't each cost a SHGetFileInfoW call.
int GetIconIndex(const SearchResult& r) {
    bool isDir = (r.attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;

    std::wstring key;
    std::wstring probe;
    DWORD attrs;
    if (isDir) {
        key = L"\\dir";
        probe = L"folder";
        attrs = FILE_ATTRIBUTE_DIRECTORY;
    } else {
        std::wstring name, dir;
        SplitNameAndDir(r.path, name, dir);
        size_t dot = name.find_last_of(L'.');
        std::wstring ext = (dot == std::wstring::npos) ? std::wstring() : ToLower(name.substr(dot));
        key = ext.empty() ? L"\\noext" : ext;
        probe = ext.empty() ? L"file" : ext;
        attrs = FILE_ATTRIBUTE_NORMAL;
    }

    auto it = g_iconCache.find(key);
    if (it != g_iconCache.end()) return it->second;

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
        auto partial = volume->Search(query, kMaxResults - results.size());
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
            std::wstring error;
            g_volumes[i]->BuildFromVolume(drives[i], error);
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

// Copies the file itself (not just its path text) to the clipboard as a
// CF_HDROP, so it can be pasted into Explorer like a real Ctrl+C would.
void ActionCopyAsFileObject(HWND hwnd, const std::wstring& path) {
    if (!OpenClipboard(hwnd)) return;
    EmptyClipboard();

    size_t charCount = path.size() + 1 /* item terminator */ + 1 /* list terminator */;
    size_t dropSize = sizeof(DROPFILES) + charCount * sizeof(wchar_t);
    HGLOBAL mem = GlobalAlloc(GHND, dropSize);
    if (mem) {
        auto* df = static_cast<DROPFILES*>(GlobalLock(mem));
        df->pFiles = sizeof(DROPFILES);
        df->fWide = TRUE;
        auto* dst = reinterpret_cast<wchar_t*>(reinterpret_cast<BYTE*>(df) + sizeof(DROPFILES));
        wcscpy_s(dst, path.size() + 1, path.c_str());
        // GHND zero-initializes the allocation, so both the per-item and
        // final list null terminators are already in place.
        GlobalUnlock(mem);
        SetClipboardData(CF_HDROP, mem);
    }
    CloseClipboard();
}

void ActionDelete(HWND hwnd, int index, const std::wstring& path) {
    std::wstring msg = L"다음을 삭제하시겠습니까?\n\n" + path;
    if (MessageBoxW(hwnd, msg.c_str(), L"삭제 확인", MB_YESNO | MB_ICONWARNING) != IDYES) return;

    std::wstring doubleNull = path + L'\0';
    SHFILEOPSTRUCTW op{};
    op.hwnd = hwnd;
    op.wFunc = FO_DELETE;
    op.pFrom = doubleNull.c_str();
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION;
    SHFileOperationW(&op);

    g_results.erase(g_results.begin() + index);
    ListView_SetItemCountEx(g_resultsView, g_results.size(), LVSICF_NOSCROLL);
    InvalidateRect(g_resultsView, nullptr, FALSE);
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

// Menu structure and labels pulled from the real Everything.exe's own
// strings. View/Bookmarks/Tools show the real labels for visual parity but
// are disabled - nothing backs them yet. ETP/FTP server items are
// deliberately left out of Tools.
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
    AppendMenuW(editMenu, MF_STRING | MF_GRAYED, 0, L"모두 선택(&A)");
    AppendMenuW(editMenu, MF_STRING | MF_GRAYED, 0, L"선택 반전(&I)");
    AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(editMenu), L"편집(&E)");

    HMENU viewMenu = CreatePopupMenu();
    AppendMenuW(viewMenu, MF_STRING | MF_GRAYED, 0, L"창 크기(&W)");
    AppendMenuW(viewMenu, MF_STRING | MF_GRAYED, 0, L"글꼴 및 색(&N)");
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
    AppendMenuW(toolsMenu, MF_STRING | MF_GRAYED, 0, L"옵션...(&O)");
    AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(toolsMenu), L"도구(&T)");
    // ETP/FTP server connect/disconnect/start/stop items intentionally omitted.

    HMENU helpMenu = CreatePopupMenu();
    AppendMenuW(helpMenu, MF_STRING, IDM_HELP_SYNTAX, L"검색 문법 도움말");
    AppendMenuW(helpMenu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(helpMenu, MF_STRING, IDM_HELP_ABOUT, L"Everything 정보(&A)");
    AppendMenuW(menuBar, MF_POPUP, reinterpret_cast<UINT_PTR>(helpMenu), L"도움말(&H)");

    return menuBar;
}

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
        case WM_CREATE: {
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
                WS_CHILD | WS_VISIBLE | WS_BORDER | LVS_REPORT | LVS_OWNERDATA | LVS_SINGLESEL,
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

            std::thread(IndexingThread, hwnd).detach();
            return 0;
        }
        case WM_SIZE: {
            int w = LOWORD(lParam), h = HIWORD(lParam);
            MoveWindow(g_status, 8, 8, w - 16, 20, TRUE);
            MoveWindow(g_searchBox, 8, 32, w - 16, 26, TRUE);
            MoveWindow(g_resultsView, 8, 64, w - 16, h - 72, TRUE);
            int totalW = w - 16 - 20;  // minus scrollbar allowance
            ListView_SetColumnWidth(g_resultsView, 0, totalW * 27 / 100);
            ListView_SetColumnWidth(g_resultsView, 1, totalW * 38 / 100);
            ListView_SetColumnWidth(g_resultsView, 2, totalW * 12 / 100);
            ListView_SetColumnWidth(g_resultsView, 3, totalW * 23 / 100);
            return 0;
        }
        case kMsgIndexReady: {
            size_t total = TotalCount();
            wchar_t status[256];
            if (total == 0) {
                swprintf_s(status, L"인덱싱 실패 - 이 프로그램을 관리자 권한으로 다시 실행하세요");
            } else {
                EnableWindow(g_searchBox, TRUE);
                SetFocus(g_searchBox);
                swprintf_s(status, L"%zu개 인덱싱 완료 - 검색어를 입력하세요", total);
            }
            SetWindowTextW(g_status, status);
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
                case IDM_FILE_DELETE:
                    if (GetSelectedResult(index, path)) ActionDelete(hwnd, index, path);
                    break;
                case IDM_FILE_REFRESH:
                    RunSearch();
                    break;
                case IDM_FILE_CLOSE:
                    PostMessage(hwnd, WM_CLOSE, 0, 0);
                    break;
                case IDM_EDIT_COPY:
                    if (GetSelectedResult(index, path)) ActionCopyAsFileObject(hwnd, path);
                    break;
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
                case IDM_HELP_SYNTAX:
                    MessageBoxW(hwnd,
                        L"report            이름/경로에 포함\n"
                        L"*.jpg             와일드카드\n"
                        L"\"exact phrase\"    정확한 구문\n"
                        L"budget | invoice  OR\n"
                        L"!temp             제외 (NOT)\n"
                        L"ext:jpg;png       확장자\n"
                        L"folder: / file:   폴더만 / 파일만\n"
                        L"attrib:h          속성\n"
                        L"case: / path: / wholeword:  전역 토글\n"
                        L"regex:            정규식\n"
                        L"size:>10mb        크기 필터\n"
                        L"dm:today          수정일 필터 (dc:/da:도 동일)",
                        L"검색 문법 도움말", MB_OK | MB_ICONINFORMATION);
                    break;
                case IDM_HELP_ABOUT:
                    MessageBoxW(hwnd,
                        L"EverythingClone\n\n"
                        L"voidtools Everything의 NTFS MFT/USN 기반 실시간 파일 검색 방식을 "
                        L"재구현한 클론입니다.",
                        L"Everything 정보", MB_OK | MB_ICONINFORMATION);
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
                    ActionDelete(hwnd, selected, path);
                    break;
                case 5:
                    ActionProperties(hwnd, path);
                    break;
            }
            return 0;
        }
        case WM_DESTROY:
            g_running = false;
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProcW(hwnd, msg, wParam, lParam);
}

}  // namespace

int APIENTRY wWinMain(HINSTANCE hInstance, HINSTANCE, LPWSTR, int nCmdShow) {
    INITCOMMONCONTROLSEX icc{sizeof(icc), ICC_LISTVIEW_CLASSES};
    InitCommonControlsEx(&icc);

    const wchar_t* className = L"EverythingCloneWindow";
    WNDCLASSEXW wc{};
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hInstance;
    wc.lpszClassName = className;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    RegisterClassExW(&wc);

    HWND hwnd = CreateWindowExW(0, className, L"EverythingClone", WS_OVERLAPPEDWINDOW,
                                 CW_USEDEFAULT, CW_USEDEFAULT, 700, 560, nullptr, CreateAppMenu(),
                                 hInstance, nullptr);
    if (!hwnd) return 1;

    ShowWindow(hwnd, nCmdShow);
    UpdateWindow(hwnd);

    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return static_cast<int>(msg.wParam);
}
