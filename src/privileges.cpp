#include "privileges.h"

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

bool EnablePrivilege(const wchar_t* privilegeName) {
    HANDLE token;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &token)) {
        return false;
    }

    LUID luid;
    if (!LookupPrivilegeValueW(nullptr, privilegeName, &luid)) {
        CloseHandle(token);
        return false;
    }

    TOKEN_PRIVILEGES tp{};
    tp.PrivilegeCount = 1;
    tp.Privileges[0].Luid = luid;
    tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;

    BOOL adjusted = AdjustTokenPrivileges(token, FALSE, &tp, sizeof(tp), nullptr, nullptr);
    DWORD err = GetLastError();
    CloseHandle(token);
    return adjusted && err == ERROR_SUCCESS;
}
