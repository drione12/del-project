#pragma once

// Enables a named privilege (e.g. SE_BACKUP_NAME) on the current process token.
// Needed to open a raw volume handle ("\\.\C:") without full Administrator rights.
bool EnablePrivilege(const wchar_t* privilegeName);
