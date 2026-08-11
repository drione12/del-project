#pragma once

#include <string>

std::wstring ToLower(const std::wstring& s);

// Supports '*' (any run of characters) and '?' (single character).
bool WildcardMatch(const std::wstring& text, const std::wstring& pattern);
