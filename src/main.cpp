#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commctrl.h>
#include <shellapi.h>
#include <winioctl.h>

#include <atomic>
#include <cstdio>
#include <cwchar>
#include <functional>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "ntfs_index.h"
#include "privileges.h"
#include "usn_watcher.h"
#include "volume_utils.h"

namespace {

constexpr int kSearchBoxId = 101;
constexpr int kStatusId = 102;
constexpr int kResultsId = 103;
constexpr UINT kMsgIndexReady = WM_APP + 1;
constexpr size_t kMaxResults = 2000;

std::vector<std::unique_ptr<NtfsIndex>> g_volumes;
std::vector<std::wstring> g_results;
std::atomic<bool> g_running{true};
HWND g_searchBox = nullptr;
HWND g_status = nullptr;
HWND g_resultsView = nullptr;

std::vector<std::wstring> SearchAll(const std::wstring& query) {
    std::vector<std::wstring> results;
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

            LVCOLUMNW col{};
            col.mask = LVCF_TEXT | LVCF_WIDTH;
            col.cx = 580;
            col.pszText = const_cast<LPWSTR>(L"경로 (더블클릭하면 열림)");
            ListView_InsertColumn(g_resultsView, 0, &col);

            std::thread(IndexingThread, hwnd).detach();
            return 0;
        }
        case WM_SIZE: {
            int w = LOWORD(lParam), h = HIWORD(lParam);
            MoveWindow(g_status, 8, 8, w - 16, 20, TRUE);
            MoveWindow(g_searchBox, 8, 32, w - 16, 26, TRUE);
            MoveWindow(g_resultsView, 8, 64, w - 16, h - 72, TRUE);
            ListView_SetColumnWidth(g_resultsView, 0, w - 16 - 20);
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
                    wcsncpy_s(di->item.pszText, di->item.cchTextMax, g_results[i].c_str(),
                              _TRUNCATE);
                }
            } else if (hdr->code == NM_DBLCLK) {
                auto* nm = reinterpret_cast<NMITEMACTIVATE*>(lParam);
                if (nm->iItem >= 0 && static_cast<size_t>(nm->iItem) < g_results.size()) {
                    ShellExecuteW(nullptr, L"open", g_results[nm->iItem].c_str(), nullptr,
                                  nullptr, SW_SHOWNORMAL);
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
