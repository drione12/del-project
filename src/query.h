#pragma once

#include <string>
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

struct QueryTerm {
    enum class Kind { Text, Ext, FolderOnly, FileOnly, Attrib };
    Kind kind = Kind::Text;
    std::wstring text;
    bool negate = false;
};

struct QueryGroup {
    std::vector<QueryTerm> terms;  // AND'd together
};

struct Query {
    std::vector<QueryGroup> groups;  // OR'd together
    MatchOptions options;
};

Query ParseQuery(const std::wstring& raw);
bool MatchesQuery(const Query& query, const std::wstring& name, const std::wstring& path,
                   DWORD attributes);
