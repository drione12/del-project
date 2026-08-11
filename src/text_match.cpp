#include "text_match.h"

#include <algorithm>
#include <cwctype>

std::wstring ToLower(const std::wstring& s) {
    std::wstring result = s;
    std::transform(result.begin(), result.end(), result.begin(),
                    [](wchar_t c) { return static_cast<wchar_t>(::towlower(c)); });
    return result;
}

bool WildcardMatch(const std::wstring& text, const std::wstring& pattern) {
    size_t t = 0, p = 0;
    size_t starIdx = std::wstring::npos, matchIdx = 0;

    while (t < text.size()) {
        if (p < pattern.size() && (pattern[p] == L'?' || pattern[p] == text[t])) {
            t++;
            p++;
        } else if (p < pattern.size() && pattern[p] == L'*') {
            starIdx = p;
            matchIdx = t;
            p++;
        } else if (starIdx != std::wstring::npos) {
            p = starIdx + 1;
            matchIdx++;
            t = matchIdx;
        } else {
            return false;
        }
    }
    while (p < pattern.size() && pattern[p] == L'*') p++;
    return p == pattern.size();
}
