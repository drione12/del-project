#pragma once

#include <cstdint>
#include <string>
#include <unordered_set>
#include <vector>
#include <windows.h>

// A parsed Everything-style search query: OR'd groups of AND'd terms, plus
// global matching options toggled inline in the query text (case:, path:, ...).
struct MatchOptions {
    bool caseSensitive = false;
    bool wholeWord = false;
    bool matchPath = false;  // match the full path instead of just the name
    bool useRegex = false;
};

// Comparison used by the numeric/date filter kinds (Size, DateModified, ...).
// Range means "valueLow <= x < valueHigh".
enum class CompareOp { Eq, Lt, Le, Gt, Ge, Range };

struct QueryTerm {
    enum class Kind {
        Text,
        Ext,
        FolderOnly,
        FileOnly,
        Attrib,
        Size,
        DateModified,
        DateCreated,
        DateAccessed,
        Dupe,
        SizeDupe,
        NamePartDupe,
        AttribDupe,
        DateAccessedDupe,
        DateCreatedDupe,
        DateModifiedDupe
    };
    Kind kind = Kind::Text;
    std::wstring text;
    bool negate = false;

    // Only used by Size/DateModified/DateCreated/DateAccessed.
    CompareOp op = CompareOp::Eq;
    uint64_t valueLow = 0;
    uint64_t valueHigh = 0;  // upper bound, only meaningful when op == Range
};

struct QueryGroup {
    std::vector<QueryTerm> terms;  // AND'd together
};

struct Query {
    std::vector<QueryGroup> groups;  // OR'd together
    MatchOptions options;
};

// Precomputed FRN membership for the dupe: filter family (dupe:/sizedupe:/
// namepartdupe:/attribdupe:/dadupe:/dcdupe:/dmdupe:) - each pointer is null
// when the query doesn't use that particular filter, so NtfsIndex::Search
// only pays for the whole-index grouping pass(es) it actually needs.
struct DupeMembership {
    const std::unordered_set<uint64_t>* dupe = nullptr;
    const std::unordered_set<uint64_t>* sizeDupe = nullptr;
    const std::unordered_set<uint64_t>* namePartDupe = nullptr;
    const std::unordered_set<uint64_t>* attribDupe = nullptr;
    const std::unordered_set<uint64_t>* dateAccessedDupe = nullptr;
    const std::unordered_set<uint64_t>* dateCreatedDupe = nullptr;
    const std::unordered_set<uint64_t>* dateModifiedDupe = nullptr;
};

// FILETIME values (created/modified/accessed) are each a single 100ns-tick
// uint64, 0 meaning "unknown" - matching FileEntry/SearchResult elsewhere.
// `frn` + `dupes` are only consulted for Kind::*Dupe terms.
Query ParseQuery(const std::wstring& raw);
bool MatchesQuery(const Query& query, const std::wstring& name, const std::wstring& path,
                   DWORD attributes, uint64_t size, uint64_t createdTime, uint64_t modifiedTime,
                   uint64_t accessedTime, uint64_t frn = 0, const DupeMembership* dupes = nullptr);
