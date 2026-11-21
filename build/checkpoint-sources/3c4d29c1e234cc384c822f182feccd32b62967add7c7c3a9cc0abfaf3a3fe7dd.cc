// Copyright 2026. Native checks use the same production helpers as the browser.
#include <cstdio>
#include <initializer_list>
#include <string>
#include <utility>

#include "base/at_exit.h"
#include "base/command_line.h"
#include "base/containers/flat_map.h"
#include "base/uxr_config.h"

int RunGraphicsGoldenTests(const std::string& fixture_root);
int RunSurfaceGoldenTests();
int RunGpuModeGoldenTests();
int RunHardwareGoldenTests();
int RunAudioDefaultsTests();
int RunVoiceCompatibilityTests();

namespace {
using Config = base::flat_map<std::string, std::string>;
using Mode = base::UxrSurfaceMode;
unsigned checks = 0;
unsigned failures = 0;
void Check(bool result, const char* description) {
  ++checks;
  if (!result) {
    ++failures;
    std::fprintf(stderr, "Configuration failure: %s\n", description);
  }
}
Config Input(std::initializer_list<std::pair<const char*, const char*>> fields) {
  Config result;
  result["uxr-fingerprint-enabled"] = "true";
  result["uxr-fingerprint-seed"] = "12345";
  result["uxr-canvas-seed"] = "12345";
  result["uxr-webgl-fingerprint"] = "true";
  for (const auto& field : fields)
    result[field.first] = field.second;
  return result;
}
void LegacyChecks() {
  auto& config = base::UxrConfig::GetInstance();
  const auto input = Input({
      {"uxr-pixel-noise", "seeded"}});
  Check(config.SetAll(input), "legacy input");
  const auto modes = config.SurfaceModes();
  Check(modes.canvas.IsLegacy() && modes.webgl.IsLegacy() &&
            modes.audio.IsNative() &&
            modes.rects.IsLegacy() && modes.text.IsLegacy() &&
            modes.sensors.IsLegacy() &&
            modes.media.IsNative(),
        "absent modes retain every legacy branch");
  const auto gpu = config.GpuBackendPolicy();
  Check(gpu.seeded_pixel_noise && gpu.pixel_noise_seed == 12345 &&
            gpu.canvas_pixel_noise && !gpu.canvas_text_noise,
        "legacy pixel/backend policy is unchanged");
  Check(config.Snapshot() == input, "legacy snapshot bytes/fields retained");
  Check(config.SetAll(input), "legacy repeated snapshot is idempotent");
  Check(!modes.audio_default_sample_rate && !modes.audio_channel_limit &&
            !modes.audio_default_channel_count,
        "absent audio rendering controls preserve native defaults");
}
void NewPolicyChecks() {
  auto& config = base::UxrConfig::GetInstance();
  const Config invalid[] = {
      Input({{"uxr-surface-modes-version", "2"}}),
      Input({{"uxr-canvas-mode-seed", "garbage"}}),
      Input({{"uxr-text-amount", "nan"}}),
      Input({{"uxr-canvas-mode", "invented"}}),
      Input({{"uxr-canvas-mode", "shardx"}, {"uxr-canvas-mode-seed", "4294967296"}}),
      Input({{"uxr-canvas-mode", "shardx"}, {"uxr-canvas-mode-seed", ""}}),
      Input({{"uxr-rects-mode", "shardx"}, {"uxr-rects-amount", "-1"}}),
      Input({{"uxr-fonts-mode", "shardx"}}),
      Input({{"uxr-fonts-allowlist", "Arial"}}),
      Input({{"uxr-fonts-local-allowlist", ""}}),
      Input({{"uxr-fonts-mode", "chromix"}}),
      Input({{"uxr-fonts-mode", "cloak"}}),
      Input({{"uxr-audio-mode", "chromix"}}),
      Input({{"uxr-audio-mode", "cloak"}}),
      Input({{"uxr-media-mode", "chromix"}}),
      Input({{"uxr-media-mode", "cloak"}}),
      Input({{"uxr-audio-render", "isolated"}}),
      Input({{"uxr-font-policy", "restricted"}}),
      Input({{"uxr-font-whitelist", "Arial"}}),
      Input({{"uxr-windows-font-metrics", "true"}}),
      Input({{"uxr-media-mode", "shardx"}, {"uxr-media-audio-inputs", "-1"}}),
      Input({{"uxr-media-mode", "native"}, {"uxr-media-audio-inputs", "0"}}),
      Input({{"uxr-media-mode", "shardx"}, {"uxr-media-video-inputs", "33"}}),
      Input({{"uxr-memory-mode", "custom"}, {"uxr-device-memory", "0"}}),
      Input({{"uxr-memory-mode", "custom"}, {"uxr-device-memory", "33"}}),
      Input({{"uxr-cpu-mode", "custom"}, {"uxr-hw-concurrency", "129"}}),
      Input({{"uxr-cpu-mode", "custom"}, {"uxr-hw-concurrency", "banana"}}),
      Input({{"uxr-gpu-mode", "cloak"}}),
      Input({{"uxr-gpu-mode", "custom"}}),
      Input({{"uxr-webgl-capabilities-mode", "custom"},
             {"uxr-webgl-max-texture-size", "0"}}),
      Input({{"uxr-webgl-capabilities-mode", "custom"},
             {"uxr-webgl-max-vertex-attribs", "8"}}),
      Input({{"uxr-screen-mode", "invented"}}),
      Input({{"uxr-sensors-mode", "shardx"}, {"uxr-sensors-amount", "nan"}}),
      Input({{"uxr-audio-default-sample-rate", "2999"}}),
      Input({{"uxr-audio-default-sample-rate", "768001"}}),
      Input({{"uxr-audio-default-sample-rate", "nan"}}),
      Input({{"uxr-audio-channel-limit", "0"}}),
      Input({{"uxr-audio-channel-limit", "1.5"}}),
      Input({{"uxr-audio-default-channel-count", "33"}}),
      Input({{"uxr-audio-channel-limit", "1"},
             {"uxr-audio-default-channel-count", "2"}}),
      Input({{"uxr-fonts-allowlist", "Arial,,Verdana"}}),
      Input({{"uxr-fonts-local-allowlist", "Arial,"}}),
      Input({{"uxr-fonts-allowlist", "   "}}),
      Input({{"uxr-fonts-allowlist", "Ar\ti\ral"}}),
      Input({{"uxr-fonts-allowlist", "\xC0\xAF"}}),
      Input({{"uxr-webgl-masked-vendor", "example"}}),
      Input({{"uxr-webgl-identity-mode", "custom"},
             {"uxr-webgl-vendor", "vendor"}, {"uxr-webgl-renderer", "renderer"},
             {"uxr-webgl-masked-vendor", "example"}}),
  };
  for (const auto& value : invalid) {
    Check(!config.SetAll(value), "invalid mode combination rejected");
    Check(!config.IsInitialized(), "rejected input published no partial snapshot");
  }
  const auto input = Input({
      {"uxr-canvas-mode", "shardx"}, {"uxr-canvas-mode-seed", "4294967295"},
      {"uxr-webgl-mode", "native"},
      {"uxr-audio-mode", "shardx"}, {"uxr-audio-mode-seed", "42"},
      {"uxr-fonts-mode", "native"}, {"uxr-rects-mode", "shardx"},
      {"uxr-text-mode", "shardx"},
      {"uxr-sensors-mode", "shardx"},
      {"uxr-media-mode", "shardx"}, {"uxr-media-audio-inputs", "0"},
      {"uxr-memory-mode", "custom"}, {"uxr-device-memory", "4"},
      {"uxr-cpu-mode", "custom"}, {"uxr-hw-concurrency", "12"},
      {"uxr-gpu-mode", "native"},
      {"uxr-webgl-capabilities-mode", "custom"},
      {"uxr-webgl-max-texture-size", "64"},
      {"uxr-audio-default-sample-rate", "32000"},
      {"uxr-audio-channel-limit", "2"},
      {"uxr-audio-default-channel-count", "1"},
      {"uxr-webgl-identity-mode", "custom"},
      {"uxr-webgl-vendor", "vendor"},
      {"uxr-webgl-renderer", "renderer"},
      {"uxr-webgl-masked-vendor", "Test Masked Vendor"},
      {"uxr-webgl-masked-renderer", "Test Masked Renderer"},
  });
  Check(config.SetAll(input), "valid independent modes");
  const auto modes = config.SurfaceModes();
  Check(modes.canvas.mode == Mode::kShardX &&
            modes.canvas.seed == 4294967295ULL && modes.webgl.IsNative(),
        "Canvas and WebGL modes/seeds are independent");
  Check(modes.audio.mode == Mode::kShardX && modes.audio.seed == 42,
        "ShardX audio keeps its independent seed without a graph rewrite");
  Check(modes.rects.amount == 1 && modes.sensors.amount == 0.1,
        "compatible defaults are explicit");
  Check(modes.audio_default_sample_rate == 32000.0f &&
            modes.audio_channel_limit == 2u &&
            modes.audio_default_channel_count == 1u,
        "real audio controls are independent of readback noise");
  Check(modes.text.mode == Mode::kShardX,
        "independent text choice");
  Check(config.Snapshot() == input, "new snapshot retains selected values");
  Check(config.SetAll(input), "new repeated snapshot is idempotent");
  auto changed = input;
  changed["uxr-canvas-mode"] = "native";
  Check(!config.SetAll(changed) && config.Snapshot() == input,
        "an initialized process cannot silently change identity");
}
void RetiredSurfaceChecks() {
  auto& config = base::UxrConfig::GetInstance();
  for (const char* surface : {"canvas", "webgl", "fonts", "audio", "rects",
                              "text", "offset", "sensors", "media", "voices"}) {
    for (const char* mode : {"chromix", "cloak"}) {
      auto input = Input({});
      input[std::string("uxr-") + surface + "-mode"] = mode;
      Check(!config.SetAll(input), "retired surface was rejected");
      Check(!config.IsInitialized(), "retired surface published no snapshot");
    }
  }
}
}  // namespace

int main(int argc, char** argv) {
  base::AtExitManager at_exit;
  base::CommandLine::Init(argc, argv);
  if (argc == 2 && std::string(argv[1]) == "--config-legacy") {
    LegacyChecks();
  } else if (argc == 2 && std::string(argv[1]) == "--config-retired-surfaces") {
    RetiredSurfaceChecks();
  } else {
    if (argc != 2) {
      std::fprintf(stderr, "Supply the fixture directory or --config-legacy\n");
      return 2;
    }
    const int graphics = RunGraphicsGoldenTests(argv[1]);
    const int surfaces = RunSurfaceGoldenTests();
    const int gpu = RunGpuModeGoldenTests();
    const int hardware = RunHardwareGoldenTests();
    const int audio_defaults = RunAudioDefaultsTests();
    const int voices = RunVoiceCompatibilityTests();
    Check(graphics == 0, "graphics production helper goldens");
    Check(surfaces == 0, "surface production helper goldens");
    Check(gpu == 0, "GPU production policy goldens");
    Check(hardware == 0, "hardware production selector goldens");
    Check(audio_defaults == 0, "audio render defaults production policy checks");
    Check(voices == 0, "Windows voice compatibility routing checks");
    NewPolicyChecks();
  }
  std::printf("{\"status\":\"%s\",\"configuration_checks\":%u,\"failures\":%u}\n",
              failures ? "failed" : "passed", checks, failures);
  return failures ? 1 : 0;
}
