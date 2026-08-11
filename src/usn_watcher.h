#pragma once

#include <atomic>

#include "ntfs_index.h"

// Tails the NTFS USN change journal for one drive and applies each record
// to its NtfsIndex, so the in-memory index stays live without ever
// re-scanning the volume. Runs until `running` is cleared.
namespace UsnWatcher {
void WatchLoop(wchar_t driveLetter, NtfsIndex& index, std::atomic<bool>& running);
}
