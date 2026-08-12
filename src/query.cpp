#include "query.h"

#include <cwchar>
#include <cwctype>
#include <regex>

#include "text_match.h"

namespace {

std::vector<std::wstring> Tokenize(const std::wstring& raw) {
    std::vector<std::wstring> tokens;
    std::wstring current;
    bool inQuotes = false;

    for (wchar_t c : raw) {
        if (c == L'"') {
            inQuotes = !inQuotes;
            continue;
        }
        if (!inQuotes && (c == L' ' || c == L'\t')) {
            if (!current.empty()) {
                tokens.push_back(current);
                current.clear();
            }
            continue;
        }
        if (!inQuotes && c == L'|') {
            if (!current.empty()) {
                tokens.push_back(current);
                current.clear();
            }
            tokens.push_back(L"|");
            continue;
        }
        current.push_back(c);
    }
    if (!current.empty()) tokens.push_back(current);
    return tokens;
}

bool StartsWith(const std::wstring& lower, const wchar_t* prefix) {
    size_t len = wcslen(prefix);
    return lower.size() >= len && lower.compare(0, len, prefix) == 0;
}

QueryTerm ParseTerm(std::wstring tok) {
    QueryTerm term;
    if (!tok.empty() && tok[0] == L'!') {
        term.negate = true;
        tok = tok.substr(1);
    }
    std::wstring lower = ToLower(tok);

    if (StartsWith(lower, L"ext:")) {
        term.kind = QueryTerm::Kind::Ext;
        term.text = tok.substr(4);
    } else if (lower == L"folder:" || lower == L"dir:") {
        term.kind = QueryTerm::Kind::FolderOnly;
    } else if (lower == L"file:") {
        term.kind = QueryTerm::Kind::FileOnly;
    } else if (StartsWith(lower, L"attrib:")) {
        term.kind = QueryTerm::Kind::Attrib;
        term.text = tok.substr(7);
    } else if (StartsWith(lower, L"attributes:")) {
        term.kind = QueryTerm::Kind::Attrib;
        term.text = tok.substr(11);
    } else {
        term.kind = QueryTerm::Kind::Text;
        term.text = tok;
    }
    return term;
}

bool MatchText(const std::wstring& haystack, const std::wstring& needle,
               const MatchOptions& opts) {
    if (opts.useRegex) {
        try {
            auto flags = std::regex_constants::ECMAScript;
            if (!opts.caseSensitive) flags |= std::regex_constants::icase;
            std::wregex re(needle, flags);
            return std::regex_search(haystack, re);
        } catch (const std::regex_error&) {
            return false;
        }
    }

    std::wstring h = opts.caseSensitive ? haystack : ToLower(haystack);
    std::wstring n = opts.caseSensitive ? needle : ToLower(needle);

    bool hasWildcard = n.find(L'*') != std::wstring::npos || n.find(L'?') != std::wstring::npos;
    if (hasWildcard) {
        return WildcardMatch(h, n);
    }

    if (!opts.wholeWord) {
        return h.find(n) != std::wstring::npos;
    }

    size_t pos = 0;
    while ((pos = h.find(n, pos)) != std::wstring::npos) {
        bool leftOk = (pos == 0) || !iswalnum(h[pos - 1]);
        size_t endPos = pos + n.size();
        bool rightOk = (endPos == h.size()) || !iswalnum(h[endPos]);
        if (leftOk && rightOk) return true;
        pos++;
    }
    return false;
}

bool MatchExt(const std::wstring& name, const std::wstring& wantedRaw) {
    size_t dot = name.find_last_of(L'.');
    std::wstring actualExt = (dot == std::wstring::npos) ? L"" : ToLower(name.substr(dot + 1));
    std::wstring wanted = ToLower(wantedRaw);

    size_t start = 0;
    while (start <= wanted.size()) {
        size_t sep = wanted.find(L';', start);
        std::wstring one =
            wanted.substr(start, sep == std::wstring::npos ? std::wstring::npos : sep - start);
        if (one == actualExt) return true;
        if (sep == std::wstring::npos) break;
        start = sep + 1;
    }
    return false;
}

bool MatchAttrib(DWORD attributes, const std::wstring& letters) {
    for (wchar_t c : letters) {
        DWORD bit = 0;
        switch (towupper(c)) {
            case L'R': bit = FILE_ATTRIBUTE_READONLY; break;
            case L'H': bit = FILE_ATTRIBUTE_HIDDEN; break;
            case L'S': bit = FILE_ATTRIBUTE_SYSTEM; break;
            case L'D': bit = FILE_ATTRIBUTE_DIRECTORY; break;
            case L'A': bit = FILE_ATTRIBUTE_ARCHIVE; break;
            case L'C': bit = FILE_ATTRIBUTE_COMPRESSED; break;
            case L'E': bit = FILE_ATTRIBUTE_ENCRYPTED; break;
            case L'T': bit = FILE_ATTRIBUTE_TEMPORARY; break;
            case L'O': bit = FILE_ATTRIBUTE_OFFLINE; break;
            case L'L': bit = FILE_ATTRIBUTE_REPARSE_POINT; break;
            default: continue;
        }
        if ((attributes & bit) == 0) return false;
    }
    return true;
}

bool MatchesTerm(const QueryTerm& term, const MatchOptions& opts, const std::wstring& name,
                  const std::wstring& path, DWORD attributes) {
    switch (term.kind) {
        case QueryTerm::Kind::Ext:
            return MatchExt(name, term.text);
        case QueryTerm::Kind::FolderOnly:
            return (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
        case QueryTerm::Kind::FileOnly:
            return (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0;
        case QueryTerm::Kind::Attrib:
            return MatchAttrib(attributes, term.text);
        case QueryTerm::Kind::Text:
        default:
            return MatchText(opts.matchPath ? path : name, term.text, opts);
    }
}

}  // namespace

Query ParseQuery(const std::wstring& raw) {
    Query query;
    auto tokens = Tokenize(raw);

    std::vector<std::wstring> remaining;
    bool regexMode = false;
    for (auto& tok : tokens) {
        std::wstring lower = ToLower(tok);
        if (lower == L"case:") {
            query.options.caseSensitive = true;
        } else if (lower == L"nocase:") {
            query.options.caseSensitive = false;
        } else if (lower == L"wholeword:" || lower == L"ww:") {
            query.options.wholeWord = true;
        } else if (lower == L"nowholeword:") {
            query.options.wholeWord = false;
        } else if (lower == L"path:") {
            query.options.matchPath = true;
        } else if (lower == L"nopath:") {
            query.options.matchPath = false;
        } else if (lower == L"regex:") {
            regexMode = true;
        } else if (lower == L"noregex:") {
            regexMode = false;
        } else {
            remaining.push_back(tok);
        }
    }

    if (regexMode) {
        query.options.useRegex = true;
        std::wstring pattern;
        for (auto& t : remaining) {
            if (!pattern.empty()) pattern += L' ';
            pattern += t;
        }
        QueryTerm term;
        term.kind = QueryTerm::Kind::Text;
        term.text = pattern;
        QueryGroup group;
        group.terms.push_back(term);
        query.groups.push_back(group);
        return query;
    }

    QueryGroup currentGroup;
    for (auto& tok : remaining) {
        if (tok == L"|") {
            if (!currentGroup.terms.empty()) {
                query.groups.push_back(currentGroup);
                currentGroup = QueryGroup{};
            }
            continue;
        }
        currentGroup.terms.push_back(ParseTerm(tok));
    }
    if (!currentGroup.terms.empty()) query.groups.push_back(currentGroup);

    return query;
}

bool MatchesQuery(const Query& query, const std::wstring& name, const std::wstring& path,
                   DWORD attributes) {
    for (const auto& group : query.groups) {
        bool allMatch = true;
        for (const auto& term : group.terms) {
            bool m = MatchesTerm(term, query.options, name, path, attributes);
            if (term.negate) m = !m;
            if (!m) {
                allMatch = false;
                break;
            }
        }
        if (allMatch) return true;
    }
    return false;
}
