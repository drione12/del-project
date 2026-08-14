#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commctrl.h>
#include <shellapi.h>
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

std::vector<std::unique_ptr<NtfsIndex>> g_volumes;
std::vector<SearchResult> g_results;
std::atomic<bool> g_running{true};
HWND g_searchBox = nullptr;
HWND g_status = nullptr;
HWND g_resultsView = nullptr;
int g_sortColumn = 0;  // 0 = Name, 1 = Path, 2 = Size, 3 = Date modified
bool g_sortAscending = true;
std::unordered_map<std::wstring, int> g_iconCache;  // extension (or a sentinel) -> icon index

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

void RunSearch() {
    wchar_t buf[1024];
    GetWindowTextW(g_searchBox, buf, 1024);
    g_results = SearchAll(buf);
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
                    ShellExecuteW(nullptr, L"open", path.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
                    break;
                case 2: {
                    std::wstring arg = L"/select,\"" + path + L"\"";
                    ShellExecuteW(nullptr, L"open", L"explorer.exe", arg.c_str(), nullptr,
                                  SW_SHOWNORMAL);
                    break;
                }
                case 3: {
                    if (OpenClipboard(hwnd)) {
                        EmptyClipboard();
                        size_t bytes = (path.size() + 1) * sizeof(wchar_t);
                        HGLOBAL mem = GlobalAlloc(GMEM_MOVEABLE, bytes);
                        if (mem) {
                            void* dst = GlobalLock(mem);
                            memcpy(dst, path.c_str(), bytes);
                            GlobalUnlock(mem);
                            SetClipboardData(CF_UNICODETEXT, mem);
                        }
                        CloseClipboard();
                    }
                    break;
                }
                case 4: {
                    std::wstring msg = L"다음을 삭제하시겠습니까?\n\n" + path;
                    if (MessageBoxW(hwnd, msg.c_str(), L"삭제 확인", MB_YESNO | MB_ICONWARNING) ==
                        IDYES) {
                        std::wstring doubleNull = path + L'\0';
                        SHFILEOPSTRUCTW op{};
                        op.hwnd = hwnd;
                        op.wFunc = FO_DELETE;
                        op.pFrom = doubleNull.c_str();
                        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION;
                        SHFileOperationW(&op);
                        g_results.erase(g_results.begin() + selected);
                        ListView_SetItemCountEx(g_resultsView, g_results.size(), LVSICF_NOSCROLL);
                        InvalidateRect(g_resultsView, nullptr, FALSE);
                    }
                    break;
                }
                case 5: {
                    SHELLEXECUTEINFOW sei{};
                    sei.cbSize = sizeof(sei);
                    sei.fMask = SEE_MASK_INVOKEIDLIST;
                    sei.hwnd = hwnd;
                    sei.lpVerb = L"properties";
                    sei.lpFile = path.c_str();
                    sei.nShow = SW_SHOWNORMAL;
                    ShellExecuteExW(&sei);
                    break;
                }
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
                                 CW_USEDEFAULT, CW_USEDEFAULT, 700, 560, nullptr, nullptr,
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
