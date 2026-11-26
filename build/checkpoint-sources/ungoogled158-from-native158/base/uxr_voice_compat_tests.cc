// Exercises the same header instantiated by the Windows TTS backend.
#include "base/uxr_voice_compat.h"

#include <cstdio>
#include <set>
#include <string>
#include <vector>

namespace {
struct TestVoice {
  std::string name;
  std::string lang;
  std::string engine_id;
  std::set<int> events;
  bool remote = false;
  bool native = true;
  std::string native_voice_identifier;
  bool operator==(const TestVoice&) const = default;
};
}  // namespace

int RunVoiceCompatibilityTests() {
  using namespace base::uxr_voice_compat;
  int checks = 0;
  int failures = 0;
  const auto check = [&](bool passed, const char* description) {
    ++checks;
    if (!passed) {
      ++failures;
      std::fprintf(stderr, "Voice compatibility failure: %s\n", description);
    }
  };
  const std::vector<TestVoice> installed = {
      {"Native Chinese", "zh-CN", "", {1, 2, 3}},
      {"Native English US", "en-US", "", {1, 2}},
      {"Native English UK", "en-GB", "", {1, 2}},
  };
  auto voices = installed;
  AppendWindowsCompatibilityVoices(&voices);
  check(voices.size() == installed.size() + 19, "complete catalog appended");
  for (size_t i = 0; i < installed.size(); ++i)
    check(voices[i] == installed[i], "native voice and default order preserved");
  for (size_t i = 0; i < kAliases.size(); ++i) {
    const auto& voice = voices[installed.size() + i];
    check(voice.name == kAliases[i].name && voice.lang == kAliases[i].lang,
          "catalog names languages and order");
    check(voice.native && voice.remote && voice.engine_id.empty(),
          "native routing with compatibility metadata");
    const auto* selected = SelectNativeVoice(installed, voice.lang);
    check(selected && NativeVoiceName(voice) == selected->name,
          "every advertised alias resolves to installed backend");
    check(selected && voice.events == selected->events,
          "only real backend events advertised");
  }
  check(SelectNativeVoice(installed, "en-GB")->name == "Native English UK",
        "exact region before primary language");
  check(SelectNativeVoice(installed, "EN-us")->name == "Native English US",
        "language tags compare case insensitively");
  check(SelectNativeVoice(installed, "en-AU")->name == "Native English US",
        "primary language fallback");
  check(SelectNativeVoice(installed, "ja-JP")->name == "Native Chinese",
        "unavailable language uses original platform default");
  const auto once = voices;
  AppendWindowsCompatibilityVoices(&voices);
  check(voices == once, "repeat enumeration has no duplicates or identity drift");
  check(NativeVoiceName(installed[0]) == installed[0].name,
        "original native routing unchanged");
  std::vector<TestVoice> empty;
  AppendWindowsCompatibilityVoices(&empty);
  check(empty.empty(), "no inventory without an installed backend");
  auto remote = installed;
  for (auto& voice : remote) { voice.native = false; voice.remote = true; }
  const auto external = remote;
  AppendWindowsCompatibilityVoices(&remote);
  check(remote == external, "external providers are never backend candidates");
  auto collision = installed;
  collision.push_back({"Google US English", "en-US", "external", {9}, true, false});
  AppendWindowsCompatibilityVoices(&collision);
  check(std::count_if(collision.begin(), collision.end(), [](const TestVoice& v) {
    return v.name == "Google US English";
  }) == 1, "existing same-name provider is preserved");
  std::printf("{\"status\":\"%s\",\"voice_compat_checks\":%d,\"failures\":%d}\n",
              failures ? "failed" : "passed", checks, failures);
  return failures ? 1 : 0;
}
