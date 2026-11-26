// Chromium-155 voice metadata compatibility backed by real Windows voices.
// Google names are compatibility aliases, not Google's network speech service.
#ifndef BASE_UXR_VOICE_COMPAT_H_
#define BASE_UXR_VOICE_COMPAT_H_

#include <algorithm>
#include <array>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace base::uxr_voice_compat {

struct Alias {
  const char* name;
  const char* lang;
};

inline constexpr std::array<Alias, 19> kAliases = {{
    {"Google Deutsch", "de-DE"},
    {"Google US English", "en-US"},
    {"Google UK English Female", "en-GB"},
    {"Google UK English Male", "en-GB"},
    {"Google español", "es-ES"},
    {"Google español de Estados Unidos", "es-US"},
    {"Google français", "fr-FR"},
    {"Google हिन्दी", "hi-IN"},
    {"Google Bahasa Indonesia", "id-ID"},
    {"Google italiano", "it-IT"},
    {"Google 日本語", "ja-JP"},
    {"Google 한국의", "ko-KR"},
    {"Google Nederlands", "nl-NL"},
    {"Google polski", "pl-PL"},
    {"Google português do Brasil", "pt-BR"},
    {"Google русский", "ru-RU"},
    {"Google 普通话（中国大陆）", "zh-CN"},
    {"Google 粤語（香港）", "zh-HK"},
    {"Google 國語（臺灣）", "zh-TW"},
}};

inline const Alias* FindAlias(std::string_view name) {
  for (const auto& alias : kAliases) {
    if (name == alias.name)
      return &alias;
  }
  return nullptr;
}

inline bool EqualLanguage(std::string_view left, std::string_view right) {
  if (left.size() != right.size())
    return false;
  const auto lower = [](char c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; };
  for (size_t i = 0; i < left.size(); ++i) {
    if (lower(left[i]) != lower(right[i]))
      return false;
  }
  return true;
}

inline std::string_view PrimaryLanguage(std::string_view language) {
  return language.substr(0, language.find('-'));
}

template <typename Voice>
const Voice* SelectNativeVoice(const std::vector<Voice>& voices,
                              std::string_view language) {
  const Voice* fallback = nullptr;
  const Voice* same_language = nullptr;
  for (const auto& voice : voices) {
    if (!voice.native || voice.remote || voice.name.empty())
      continue;
    if (!fallback)
      fallback = &voice;
    if (EqualLanguage(voice.lang, language))
      return &voice;
    if (!same_language && !voice.lang.empty() &&
        EqualLanguage(PrimaryLanguage(voice.lang), PrimaryLanguage(language))) {
      same_language = &voice;
    }
  }
  return same_language ? same_language : fallback;
}

// Append after the platform inventory, retaining its ordering and default.
// An unavailable platform backend cannot produce usable aliases.
template <typename Voice>
void AppendWindowsCompatibilityVoices(std::vector<Voice>* voices) {
  const auto native_voices = *voices;
  for (const auto& alias : kAliases) {
    if (std::any_of(voices->begin(), voices->end(), [&](const Voice& voice) {
          return voice.name == alias.name;
        })) {
      continue;
    }
    const auto* backend = SelectNativeVoice(native_voices, alias.lang);
    if (!backend)
      continue;
    Voice voice = *backend;
    voice.name = alias.name;
    voice.lang = alias.lang;
    // Keep the real backend identity through the controller/worker boundary.
    voice.native_voice_identifier = backend->name;
    voice.engine_id.clear();
    voice.native = true;
    // Preserve the agreed browser-visible compatibility declaration. The
    // implementation itself remains local and has no network transport.
    voice.remote = true;
    voices->push_back(std::move(voice));
  }
}

template <typename Voice>
const std::string& NativeVoiceName(const Voice& voice) {
  if (voice.native && voice.remote && FindAlias(voice.name) &&
      !voice.native_voice_identifier.empty()) {
    return voice.native_voice_identifier;
  }
  return voice.name;
}

}  // namespace base::uxr_voice_compat

#endif  // BASE_UXR_VOICE_COMPAT_H_
