// Copyright 2026. Independent, versioned fingerprint surface strategies.
#ifndef BASE_UXR_SURFACE_MODES_H_
#define BASE_UXR_SURFACE_MODES_H_

#include <cstdint>
#include <optional>
#include <string>

namespace base {

// kLegacy means no new switch was supplied. It must keep the pre-v1 path.
enum class UxrSurfaceMode : uint8_t {
  kLegacy,
  kNative,
  kChromix,
  kShardX,
  kCloak,
};

struct UxrSurfacePolicy {
  UxrSurfaceMode mode = UxrSurfaceMode::kLegacy;
  uint64_t seed = 0;
  // Audio: intensity (0 means the observed default 8 mantissa bits).
  // Rects/text/sensors: amplitude. Interpretation belongs to that surface.
  double amount = 0;
  bool IsLegacy() const { return mode == UxrSurfaceMode::kLegacy; }
  bool IsNative() const { return mode == UxrSurfaceMode::kNative; }
  bool IsConfigured() const { return !IsLegacy(); }
};

struct UxrSurfaceModes {
  static constexpr uint32_t kVersion = 1;
  UxrSurfacePolicy canvas;
  UxrSurfacePolicy webgl;
  UxrSurfacePolicy audio{UxrSurfaceMode::kNative};
  UxrSurfacePolicy fonts{UxrSurfaceMode::kNative};
  UxrSurfacePolicy rects;
  UxrSurfacePolicy text;
  UxrSurfacePolicy offset;
  UxrSurfacePolicy sensors;
  UxrSurfacePolicy media{UxrSurfaceMode::kNative};
  UxrSurfacePolicy voices;
  // Independent rendering controls. Omission retains the native defaults;
  // explicit WebAudio construction options keep precedence.
  std::optional<float> audio_default_sample_rate;
  std::optional<uint32_t> audio_channel_limit;
  std::optional<uint32_t> audio_default_channel_count;
  // Do not conflate these: observed Cloak audio replacement survives both,
  // whereas its TextMetrics/Canvas reference paths bypass only raw.
  bool noise_disabled = false;
  bool raw = false;
  std::string platform;
};

}  // namespace base

#endif  // BASE_UXR_SURFACE_MODES_H_
