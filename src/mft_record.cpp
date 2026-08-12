#include "mft_record.h"

#include <cstddef>
#include <cstring>
#include <vector>
#include <winioctl.h>

namespace {

#pragma pack(push, 1)

struct MftRecordHeader {
    char magic[4];  // "FILE"
    uint16_t usaOffset;
    uint16_t usaCount;
    uint64_t lsn;
    uint16_t sequenceNumber;
    uint16_t hardLinkCount;
    uint16_t firstAttributeOffset;
    uint16_t flags;
    uint32_t usedSize;
    uint32_t allocatedSize;
    uint64_t baseFileRecord;
    uint16_t nextAttributeId;
};

struct AttributeHeader {
    uint32_t type;
    uint32_t length;
    uint8_t nonResident;
    uint8_t nameLength;
    uint16_t nameOffset;
    uint16_t flags;
    uint16_t attributeId;
};

struct ResidentAttributeHeader : AttributeHeader {
    uint32_t contentLength;
    uint16_t contentOffset;
    uint8_t indexedFlag;
    uint8_t reserved;
};

struct NonResidentAttributeHeader : AttributeHeader {
    uint64_t startingVcn;
    uint64_t endingVcn;
    uint16_t dataRunsOffset;
    uint16_t compressionUnit;
    uint32_t reserved;
    uint64_t allocatedSize;
    uint64_t realSize;
    uint64_t initializedSize;
};

struct StandardInformation {
    uint64_t creationTime;
    uint64_t modificationTime;
    uint64_t mftChangeTime;
    uint64_t accessTime;
    // more fields follow (dos attributes, quota, usn, ...) - not needed here.
};

#pragma pack(pop)

constexpr uint32_t kAttrStandardInformation = 0x10;
constexpr uint32_t kAttrData = 0x80;
constexpr uint32_t kAttrEnd = 0xFFFFFFFF;
constexpr size_t kSectorSize = 512;

// NTFS stores the last 2 bytes of every 512-byte sector in a record
// redirected into an "update sequence array" for corruption detection; they
// have to be restored before the record's bytes mean anything.
void ApplyFixup(BYTE* record, DWORD recordLength) {
    auto* header = reinterpret_cast<MftRecordHeader*>(record);

    size_t usaBytes = static_cast<size_t>(header->usaCount) * sizeof(uint16_t);
    if (static_cast<size_t>(header->usaOffset) + usaBytes > recordLength) return;

    const uint8_t* usa = record + header->usaOffset;
    size_t sectorCount = recordLength / kSectorSize;

    for (size_t i = 0; i < sectorCount && (i + 1) < header->usaCount; i++) {
        BYTE* sectorEnd = record + (i + 1) * kSectorSize - sizeof(uint16_t);
        std::memcpy(sectorEnd, usa + (i + 1) * sizeof(uint16_t), sizeof(uint16_t));
    }
}

MftRecordInfo ParseMftRecord(BYTE* record, DWORD recordLength) {
    MftRecordInfo info;
    if (recordLength < sizeof(MftRecordHeader)) return info;

    auto* header = reinterpret_cast<MftRecordHeader*>(record);
    if (std::memcmp(header->magic, "FILE", 4) != 0) return info;

    ApplyFixup(record, recordLength);

    BYTE* cursor = record + header->firstAttributeOffset;
    BYTE* end = record + recordLength;

    while (cursor + sizeof(AttributeHeader) <= end) {
        auto* attr = reinterpret_cast<AttributeHeader*>(cursor);
        if (attr->type == kAttrEnd || attr->length == 0) break;
        if (cursor + attr->length > end) break;

        if (attr->type == kAttrStandardInformation && !attr->nonResident &&
            cursor + sizeof(ResidentAttributeHeader) <= end) {
            auto* resident = reinterpret_cast<ResidentAttributeHeader*>(cursor);
            BYTE* content = cursor + resident->contentOffset;
            if (content + sizeof(StandardInformation) <= end) {
                auto* si = reinterpret_cast<StandardInformation*>(content);
                info.createdTime = si->creationTime;
                info.modifiedTime = si->modificationTime;
                info.accessedTime = si->accessTime;
            }
        } else if (attr->type == kAttrData && attr->nameLength == 0) {
            if (attr->nonResident && cursor + sizeof(NonResidentAttributeHeader) <= end) {
                auto* nonResident = reinterpret_cast<NonResidentAttributeHeader*>(cursor);
                info.size = nonResident->realSize;
            } else if (!attr->nonResident && cursor + sizeof(ResidentAttributeHeader) <= end) {
                auto* resident = reinterpret_cast<ResidentAttributeHeader*>(cursor);
                info.size = resident->contentLength;
            }
        }

        cursor += attr->length;
    }

    info.valid = true;
    return info;
}

}  // namespace

MftRecordInfo ReadMftRecordInfo(HANDLE hVolume, uint64_t frn) {
    NTFS_FILE_RECORD_INPUT_BUFFER input{};
    input.FileReferenceNumber.QuadPart = static_cast<LONGLONG>(frn);

    // Generously sized: real MFT records are almost always 1024 bytes, this
    // leaves headroom for the (rarer) 4096-byte case plus the output header.
    constexpr DWORD kBufferSize = 8192;
    std::vector<BYTE> buffer(kBufferSize);

    DWORD bytesReturned = 0;
    BOOL ok = DeviceIoControl(hVolume, FSCTL_GET_NTFS_FILE_RECORD, &input, sizeof(input),
                               buffer.data(), kBufferSize, &bytesReturned, nullptr);
    if (!ok) return MftRecordInfo{};

    auto* output = reinterpret_cast<NTFS_FILE_RECORD_OUTPUT_BUFFER*>(buffer.data());

    // Clamp to the buffer we actually allocated rather than trusting
    // FileRecordLength outright - defense in depth against a corrupt volume.
    auto headerSize = static_cast<DWORD>(offsetof(NTFS_FILE_RECORD_OUTPUT_BUFFER, FileRecordBuffer));
    DWORD maxRecordLength = (kBufferSize > headerSize) ? (kBufferSize - headerSize) : 0;
    DWORD recordLength = output->FileRecordLength;
    if (recordLength > maxRecordLength) recordLength = maxRecordLength;

    return ParseMftRecord(output->FileRecordBuffer, recordLength);
}
