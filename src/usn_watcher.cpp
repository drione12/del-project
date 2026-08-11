#include "usn_watcher.h"

#include <cstdio>
#include <vector>

namespace {

struct JournalState {
    DWORDLONG journalId = 0;
    USN nextUsn = 0;
};

bool QueryJournal(HANDLE hVol, JournalState& state) {
    USN_JOURNAL_DATA_V0 data{};
    DWORD bytesReturned = 0;
    if (!DeviceIoControl(hVol, FSCTL_QUERY_USN_JOURNAL, nullptr, 0,
                          &data, sizeof(data), &bytesReturned, nullptr)) {
        return false;
    }
    state.journalId = data.UsnJournalID;
    state.nextUsn = data.NextUsn;
    return true;
}

} // namespace

void UsnWatcher::WatchLoop(wchar_t driveLetter, NtfsIndex& index, std::atomic<bool>& running) {
    wchar_t volumePath[8];
    swprintf_s(volumePath, L"\\\\.\\%c:", driveLetter);

    HANDLE hVol = CreateFileW(volumePath, GENERIC_READ,
                               FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                               OPEN_EXISTING, 0, nullptr);
    if (hVol == INVALID_HANDLE_VALUE) return;

    JournalState journal;
    if (!QueryJournal(hVol, journal)) {
        CloseHandle(hVol);
        return;
    }

    std::vector<BYTE> buffer(64 * 1024);

    while (running.load()) {
        READ_USN_JOURNAL_DATA_V0 read{};
        read.StartUsn = journal.nextUsn;
        read.ReasonMask = 0xFFFFFFFF;
        read.ReturnOnlyOnClose = 0;
        read.Timeout = 1;        // seconds; lets the loop re-check `running` periodically
        read.BytesToWaitFor = 1; // block until at least one new record arrives
        read.UsnJournalID = journal.journalId;

        DWORD bytesReturned = 0;
        if (!DeviceIoControl(hVol, FSCTL_READ_USN_JOURNAL, &read, sizeof(read),
                              buffer.data(), static_cast<DWORD>(buffer.size()),
                              &bytesReturned, nullptr)) {
            // Journal was likely reset/reallocated (e.g. volume format change);
            // re-query its id and keep tailing.
            if (!QueryJournal(hVol, journal)) break;
            continue;
        }

        USN nextUsn = *reinterpret_cast<USN*>(buffer.data());
        BYTE* cursor = buffer.data() + sizeof(USN);
        BYTE* end = buffer.data() + bytesReturned;

        while (cursor < end) {
            auto* record = reinterpret_cast<PUSN_RECORD>(cursor);
            if (record->RecordLength == 0) break;
            index.ApplyUsnRecord(record);
            cursor += record->RecordLength;
        }

        journal.nextUsn = nextUsn;
    }

    CloseHandle(hVol);
}
