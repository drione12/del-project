#pragma once

#include <atomic>

#include "ntfs_index.h"

// Tails the NTFS USN change journal for one drive and applies each record
// to its NtfsIndex, so the in-memory index stays live without ever
// re-scanning the volume. Runs until `running` is cleared.
namespace UsnWatcher {
void WatchLoop(wchar_t driveLetter, NtfsIndex& index, std::atomic<bool>& running);

// Applies every journal record from savedUsn up to the live edge (as of
// when this is called), then returns - unlike WatchLoop, this doesn't run
// forever. Used to reconcile a saved index snapshot (NtfsIndex::LoadFromFile)
// with whatever changed on disk while the app wasn't running. Returns false
// if the journal was reset since the snapshot was taken (journal ID
// mismatch) or the volume can't be opened - the snapshot can't be trusted
// in that case and the caller should fall back to a full BuildFromVolume.
bool CatchUp(wchar_t driveLetter, NtfsIndex& index, DWORDLONG savedJournalId, USN savedUsn);
}
