// Copyright 2026. Explicit installed-font filtering for compatible-v1.
#ifndef BASE_UXR_FONT_ALLOWLIST_H_
#define BASE_UXR_FONT_ALLOWLIST_H_

#include <string_view>

namespace base {

// This predicate compares a resolved native family with the validated CSV
// configuration. It does not make a font available or resolve a declared alias.
// An absent list is handled by the caller; an explicitly empty list matches no
// family. Case folding is ASCII-only and non-ASCII UTF-8 bytes compare exactly.
inline bool UxrFontFamilyInAllowlistV1(std::string_view csv,
                                     std::string_view family) {
  if (family.empty())
    return false;
  const auto space = [](char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' ||
           c == '\v';
  };
  const auto lower = [](unsigned char c) {
    return c >= 'A' && c <= 'Z' ? static_cast<unsigned char>(c + ('a' - 'A'))
                              : c;
  };
  while (!csv.empty()) {
    const auto separator = csv.find(',');
    auto entry = csv.substr(0, separator);
    while (!entry.empty() && space(entry.front()))
      entry.remove_prefix(1);
    while (!entry.empty() && space(entry.back()))
      entry.remove_suffix(1);
    if (entry.size() == family.size()) {
      bool equal = true;
      for (std::string_view::size_type i = 0; i < entry.size(); ++i) {
        if (lower(static_cast<unsigned char>(entry[i])) !=
            lower(static_cast<unsigned char>(family[i]))) {
          equal = false;
          break;
        }
      }
      if (equal)
        return true;
    }
    if (separator == std::string_view::npos)
      break;
    csv.remove_prefix(separator + 1);
  }
  return false;
}

}  // namespace base

#endif  // BASE_UXR_FONT_ALLOWLIST_H_
