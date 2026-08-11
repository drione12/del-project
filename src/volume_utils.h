#pragma once

#include <vector>

// Returns drive letters of all fixed (local, non-removable) NTFS volumes.
std::vector<wchar_t> DetectNtfsFixedDrives();
