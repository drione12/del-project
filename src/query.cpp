#include "query.h"

#include <cstdlib>
#include <cwchar>
#include <cwctype>
#include <regex>

#include "text_match.h"

namespace {

constexpr uint64_t kTicksPerDay = 24ull * 60 * 60 * 10'000'000ull;

// Local Y/M/D (midnight) -> UTC FILETIME as a single uint64, matching how
// size/date values are stored on FileEntry/SearchResult everywhere else.
uint64_t LocalDateToUtcFileTime(int year, int month, int day) {
    SYSTEMTIME local{};
    local.wYear = static_cast<WORD>(year);
    local.wMonth = static_cast<WORD>(month);
    local.wDay = static_cast<WORD>(day);
    FILETIME localFt{};
    if (!SystemTimeToFileTime(&local, &localFt)) return 0;
    FILETIME utcFt{};
    if (!LocalFileTimeToFileTime(&localFt, &utcFt)) return 0;
    return (static_cast<uint64_t>(utcFt.dwHighDateTime) << 32) | utcFt.dwLowDateTime;
}

// Adds `days` (may be negative) to local midnight "today" and returns the
// UTC FILETIME for that day's start. Simple tick arithmetic, not full
// DST-aware calendar math - good enough for a search filter, can be off by
// up to an hour right around a DST transition.
uint64_t StartOfLocalDayPlus(int64_t days) {
    SYSTEMTIME nowLocal;
    GetLocalTime(&nowLocal);
    uint64_t todayStart = LocalDateToUtcFileTime(nowLocal.wYear, nowLocal.wMonth, nowLocal.wDay);
    return static_cast<uint64_t>(static_cast<int64_t>(todayStart) +
                                  days * static_cast<int64_t>(kTicksPerDay));
}

void AddMonths(int year, int month, int deltaMonths, int& outYear, int& outMonth) {
    int total = year * 12 + (month - 1) + deltaMonths;
    outYear = total / 12;
    outMonth = total % 12 + 1;
}

uint64_t StartOfMonth(int year, int month) { return LocalDateToUtcFileTime(year, month, 1); }

// today/yesterday/tomorrow/this|last|next-week|month|year -> a [lo, hi) range.
// Not the full <last|past|prev|...><N><years|months|...> generality Everything
// supports, nor month/weekday names - just the commonly typed constants.
bool ResolveDateConstant(const std::wstring& lower, uint64_t& lo, uint64_t& hi) {
    SYSTEMTIME nowLocal;
    GetLocalTime(&nowLocal);

    if (lower == L"today") {
        lo = StartOfLocalDayPlus(0);
        hi = StartOfLocalDayPlus(1);
        return true;
    }
    if (lower == L"yesterday") {
        lo = StartOfLocalDayPlus(-1);
        hi = StartOfLocalDayPlus(0);
        return true;
    }
    if (lower == L"tomorrow") {
        lo = StartOfLocalDayPlus(1);
        hi = StartOfLocalDayPlus(2);
        return true;
    }
    if (lower == L"thisweek" || lower == L"lastweek" || lower == L"nextweek") {
        int weekOffset = (lower == L"lastweek") ? -1 : (lower == L"nextweek" ? 1 : 0);
        int64_t dayOffset = static_cast<int64_t>(weekOffset) * 7 - nowLocal.wDayOfWeek;
        lo = StartOfLocalDayPlus(dayOffset);
        hi = StartOfLocalDayPlus(dayOffset + 7);
        return true;
    }
    if (lower == L"thismonth" || lower == L"lastmonth" || lower == L"nextmonth") {
        int delta = (lower == L"lastmonth") ? -1 : (lower == L"nextmonth" ? 1 : 0);
        int y1, m1, y2, m2;
        AddMonths(nowLocal.wYear, nowLocal.wMonth, delta, y1, m1);
        AddMonths(nowLocal.wYear, nowLocal.wMonth, delta + 1, y2, m2);
        lo = StartOfMonth(y1, m1);
        hi = StartOfMonth(y2, m2);
        return true;
    }
    if (lower == L"thisyear" || lower == L"lastyear" || lower == L"nextyear") {
        int delta = (lower == L"lastyear") ? -1 : (lower == L"nextyear" ? 1 : 0);
        lo = StartOfMonth(nowLocal.wYear + delta, 1);
        hi = StartOfMonth(nowLocal.wYear + delta + 1, 1);
        return true;
    }
    return false;
}

// YYYY, YYYY-MM or YYYY-MM-DD only - not Everything's full locale-dependent
// day/month/year ordering or the compact YYYYMMDD form.
bool ParseAbsoluteDate(const std::wstring& text, int& year, int& month, int& day) {
    if (text.size() < 4) return false;
    for (int i = 0; i < 4; i++) {
        if (!iswdigit(text[i])) return false;
    }
    year = _wtoi(text.substr(0, 4).c_str());
    month = 1;
    day = 1;
    if (text.size() >= 7 && text[4] == L'-') {
        month = _wtoi(text.substr(5, 2).c_str());
        if (month < 1 || month > 12) return false;
        if (text.size() >= 10 && text[7] == L'-') {
            day = _wtoi(text.substr(8, 2).c_str());
            if (day < 1 || day > 31) return false;
        }
    }
    return true;
}

bool ParseOperatorPrefix(const std::wstring& s, CompareOp& op, std::wstring& rest) {
    if (s.compare(0, 2, L">=") == 0) {
        op = CompareOp::Ge;
        rest = s.substr(2);
        return true;
    }
    if (s.compare(0, 2, L"<=") == 0) {
        op = CompareOp::Le;
        rest = s.substr(2);
        return true;
    }
    if (!s.empty() && s[0] == L'>') {
        op = CompareOp::Gt;
        rest = s.substr(1);
        return true;
    }
    if (!s.empty() && s[0] == L'<') {
        op = CompareOp::Lt;
        rest = s.substr(1);
        return true;
    }
    if (!s.empty() && s[0] == L'=') {
        op = CompareOp::Eq;
        rest = s.substr(1);
        return true;
    }
    return false;
}

// Splits "a..b" or "a-b" into a/b. Range separators only, so a leading '-'
// (not meaningful for sizes/dates anyway) is left alone.
bool SplitRange(const std::wstring& s, std::wstring& lo, std::wstring& hi) {
    size_t pos = s.find(L"..");
    if (pos != std::wstring::npos) {
        lo = s.substr(0, pos);
        hi = s.substr(pos + 2);
        return true;
    }
    pos = s.find(L'-', 1);
    if (pos != std::wstring::npos) {
        lo = s.substr(0, pos);
        hi = s.substr(pos + 1);
        return true;
    }
    return false;
}

bool ParseSizeConstantRange(const std::wstring& lower, uint64_t& lo, uint64_t& hi) {
    constexpr uint64_t kKb = 1024ull;
    constexpr uint64_t kMb = 1024ull * 1024;
    if (lower == L"empty") {
        lo = 0;
        hi = 0;
        return true;
    }
    if (lower == L"tiny") {
        lo = 1;
        hi = 10 * kKb;
        return true;
    }
    if (lower == L"small") {
        lo = 10 * kKb + 1;
        hi = 100 * kKb;
        return true;
    }
    if (lower == L"medium") {
        lo = 100 * kKb + 1;
        hi = kMb;
        return true;
    }
    if (lower == L"large") {
        lo = kMb + 1;
        hi = 16 * kMb;
        return true;
    }
    if (lower == L"huge") {
        lo = 16 * kMb + 1;
        hi = 128 * kMb;
        return true;
    }
    if (lower == L"gigantic") {
        lo = 128 * kMb + 1;
        hi = UINT64_MAX;
        return true;
    }
    return false;
}

bool ParseSizeNumber(const std::wstring& text, uint64_t& value) {
    size_t i = 0;
    while (i < text.size() && iswdigit(text[i])) i++;
    if (i == 0) return false;

    wchar_t* end = nullptr;
    unsigned long long num = std::wcstoull(text.c_str(), &end, 10);

    std::wstring unit = ToLower(text.substr(i));
    uint64_t mult = 1;
    if (unit.empty()) {
        mult = 1;
    } else if (unit == L"kb") {
        mult = 1024ull;
    } else if (unit == L"mb") {
        mult = 1024ull * 1024;
    } else if (unit == L"gb") {
        mult = 1024ull * 1024 * 1024;
    } else if (unit == L"tb") {
        mult = 1024ull * 1024 * 1024 * 1024;
    } else {
        return false;
    }
    value = num * mult;
    return true;
}

void ParseSizeValue(const std::wstring& valueText, QueryTerm& term) {
    CompareOp op;
    std::wstring rest;
    if (ParseOperatorPrefix(valueText, op, rest)) {
        uint64_t v;
        if (ParseSizeNumber(rest, v)) {
            term.op = op;
            term.valueLow = v;
        }
        return;
    }

    std::wstring loText, hiText;
    if (SplitRange(valueText, loText, hiText)) {
        uint64_t lo, hi;
        if (ParseSizeNumber(loText, lo) && ParseSizeNumber(hiText, hi)) {
            term.op = CompareOp::Range;
            term.valueLow = lo;
            term.valueHigh = hi;
        }
        return;
    }

    uint64_t lo, hi;
    if (ParseSizeConstantRange(ToLower(valueText), lo, hi)) {
        term.op = CompareOp::Range;
        term.valueLow = lo;
        term.valueHigh = hi;
        return;
    }

    uint64_t v;
    if (ParseSizeNumber(valueText, v)) {
        term.op = CompareOp::Eq;
        term.valueLow = v;
    }
}

void ParseDateValue(const std::wstring& valueText, QueryTerm& term) {
    CompareOp op;
    std::wstring rest;
    if (ParseOperatorPrefix(valueText, op, rest)) {
        uint64_t lo, hi;
        int y, m, d;
        if (ResolveDateConstant(ToLower(rest), lo, hi)) {
            term.op = op;
            term.valueLow = lo;
        } else if (ParseAbsoluteDate(rest, y, m, d)) {
            term.op = op;
            term.valueLow = LocalDateToUtcFileTime(y, m, d);
        }
        return;
    }

    std::wstring loText, hiText;
    if (SplitRange(valueText, loText, hiText)) {
        int y1, m1, d1, y2, m2, d2;
        if (ParseAbsoluteDate(loText, y1, m1, d1) && ParseAbsoluteDate(hiText, y2, m2, d2)) {
            term.op = CompareOp::Range;
            term.valueLow = LocalDateToUtcFileTime(y1, m1, d1);
            term.valueHigh = LocalDateToUtcFileTime(y2, m2, d2);
        }
        return;
    }

    uint64_t lo, hi;
    if (ResolveDateConstant(ToLower(valueText), lo, hi)) {
        term.op = CompareOp::Range;
        term.valueLow = lo;
        term.valueHigh = hi;
        return;
    }

    int y, m, d;
    if (ParseAbsoluteDate(valueText, y, m, d)) {
        term.op = CompareOp::Range;
        term.valueLow = LocalDateToUtcFileTime(y, m, d);
        term.valueHigh = term.valueLow + kTicksPerDay;
    }
}

bool CompareValue(CompareOp op, uint64_t value, uint64_t lo, uint64_t hi) {
    switch (op) {
        case CompareOp::Eq:
            return value == lo;
        case CompareOp::Lt:
            return value < lo;
        case CompareOp::Le:
            return value <= lo;
        case CompareOp::Gt:
            return value > lo;
        case CompareOp::Ge:
            return value >= lo;
        case CompareOp::Range:
            return value >= lo && value < hi;
    }
    return false;
}

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
    } else if (StartsWith(lower, L"size:")) {
        term.kind = QueryTerm::Kind::Size;
        ParseSizeValue(tok.substr(5), term);
    } else if (StartsWith(lower, L"len:")) {
        term.kind = QueryTerm::Kind::Size;
        ParseSizeValue(tok.substr(4), term);
    } else if (StartsWith(lower, L"datemodified:")) {
        term.kind = QueryTerm::Kind::DateModified;
        ParseDateValue(tok.substr(13), term);
    } else if (StartsWith(lower, L"dm:")) {
        term.kind = QueryTerm::Kind::DateModified;
        ParseDateValue(tok.substr(3), term);
    } else if (StartsWith(lower, L"datecreated:")) {
        term.kind = QueryTerm::Kind::DateCreated;
        ParseDateValue(tok.substr(12), term);
    } else if (StartsWith(lower, L"dc:")) {
        term.kind = QueryTerm::Kind::DateCreated;
        ParseDateValue(tok.substr(3), term);
    } else if (StartsWith(lower, L"dateaccessed:")) {
        term.kind = QueryTerm::Kind::DateAccessed;
        ParseDateValue(tok.substr(13), term);
    } else if (StartsWith(lower, L"da:")) {
        term.kind = QueryTerm::Kind::DateAccessed;
        ParseDateValue(tok.substr(3), term);
    } else if (lower == L"dupe:") {
        term.kind = QueryTerm::Kind::Dupe;
    } else if (lower == L"sizedupe:") {
        term.kind = QueryTerm::Kind::SizeDupe;
    } else if (lower == L"namepartdupe:") {
        term.kind = QueryTerm::Kind::NamePartDupe;
    } else if (lower == L"attribdupe:") {
        term.kind = QueryTerm::Kind::AttribDupe;
    } else if (lower == L"dadupe:") {
        term.kind = QueryTerm::Kind::DateAccessedDupe;
    } else if (lower == L"dcdupe:") {
        term.kind = QueryTerm::Kind::DateCreatedDupe;
    } else if (lower == L"dmdupe:") {
        term.kind = QueryTerm::Kind::DateModifiedDupe;
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

bool InDupeSet(const std::unordered_set<uint64_t>* set, uint64_t frn) {
    return set && set->count(frn) != 0;
}

bool MatchesTerm(const QueryTerm& term, const MatchOptions& opts, const std::wstring& name,
                  const std::wstring& path, DWORD attributes, uint64_t size, uint64_t createdTime,
                  uint64_t modifiedTime, uint64_t accessedTime, uint64_t frn,
                  const DupeMembership* dupes) {
    switch (term.kind) {
        case QueryTerm::Kind::Ext:
            return MatchExt(name, term.text);
        case QueryTerm::Kind::FolderOnly:
            return (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
        case QueryTerm::Kind::FileOnly:
            return (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0;
        case QueryTerm::Kind::Attrib:
            return MatchAttrib(attributes, term.text);
        case QueryTerm::Kind::Size:
            return CompareValue(term.op, size, term.valueLow, term.valueHigh);
        case QueryTerm::Kind::DateModified:
            return modifiedTime != 0 &&
                   CompareValue(term.op, modifiedTime, term.valueLow, term.valueHigh);
        case QueryTerm::Kind::DateCreated:
            return createdTime != 0 &&
                   CompareValue(term.op, createdTime, term.valueLow, term.valueHigh);
        case QueryTerm::Kind::DateAccessed:
            return accessedTime != 0 &&
                   CompareValue(term.op, accessedTime, term.valueLow, term.valueHigh);
        case QueryTerm::Kind::Dupe:
            return dupes && InDupeSet(dupes->dupe, frn);
        case QueryTerm::Kind::SizeDupe:
            return dupes && InDupeSet(dupes->sizeDupe, frn);
        case QueryTerm::Kind::NamePartDupe:
            return dupes && InDupeSet(dupes->namePartDupe, frn);
        case QueryTerm::Kind::AttribDupe:
            return dupes && InDupeSet(dupes->attribDupe, frn);
        case QueryTerm::Kind::DateAccessedDupe:
            return dupes && InDupeSet(dupes->dateAccessedDupe, frn);
        case QueryTerm::Kind::DateCreatedDupe:
            return dupes && InDupeSet(dupes->dateCreatedDupe, frn);
        case QueryTerm::Kind::DateModifiedDupe:
            return dupes && InDupeSet(dupes->dateModifiedDupe, frn);
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
                   DWORD attributes, uint64_t size, uint64_t createdTime, uint64_t modifiedTime,
                   uint64_t accessedTime, uint64_t frn, const DupeMembership* dupes) {
    for (const auto& group : query.groups) {
        bool allMatch = true;
        for (const auto& term : group.terms) {
            bool m = MatchesTerm(term, query.options, name, path, attributes, size, createdTime,
                                  modifiedTime, accessedTime, frn, dupes);
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
