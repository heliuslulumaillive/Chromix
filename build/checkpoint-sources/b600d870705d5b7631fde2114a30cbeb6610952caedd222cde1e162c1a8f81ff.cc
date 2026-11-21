// Copyright 2026.
#include "base/uxr_config.h"
#include "base/uxr_gpu_modes.h"
#include "base/uxr_pixel_noise.h"

#include <array>
#include <cmath>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <random>
#include <string_view>

#include "base/no_destructor.h"
#include "base/strings/string_number_conversions.h"
#include "base/strings/string_util.h"

namespace base {

namespace {
bool ReadDimension(const flat_map<std::string, std::string>& cfg,
                   const char* key, int* out, int min = 1, int max = 32768) {
  auto it = cfg.find(key);
  if (it == cfg.end())
    return true;
  int value = 0;
  if (!StringToInt(it->second, &value) || value < min || value > max)
    return false;
  *out = value;
  return true;
}

bool ParseDisplay(const flat_map<std::string, std::string>& cfg,
                  UxrDisplayConfig* d) {
  if (!ReadDimension(cfg, "uxr-screen-width", &d->width) ||
      !ReadDimension(cfg, "uxr-screen-height", &d->height) ||
      (d->width == 0) != (d->height == 0) ||
      !ReadDimension(cfg, "uxr-viewport-width", &d->viewport_width) ||
      !ReadDimension(cfg, "uxr-viewport-height", &d->viewport_height) ||
      (d->viewport_width == 0) != (d->viewport_height == 0) ||
      !ReadDimension(cfg, "uxr-outer-width", &d->outer_width) ||
      !ReadDimension(cfg, "uxr-outer-height", &d->outer_height) ||
      (d->outer_width == 0) != (d->outer_height == 0) ||
      !ReadDimension(cfg, "uxr-window-x", &d->window_x, -1000000, 1000000) ||
      !ReadDimension(cfg, "uxr-window-y", &d->window_y, -1000000, 1000000))
    return false;
  d->has_position = cfg.contains("uxr-window-x") || cfg.contains("uxr-window-y");
  d->available_width = d->width;
  d->available_height = d->height;
  if (!d->width && (cfg.contains("uxr-screen-avail-width") ||
                    cfg.contains("uxr-screen-avail-height")))
    return false;
  if (!ReadDimension(cfg, "uxr-screen-avail-width", &d->available_width, 0) ||
      !ReadDimension(cfg, "uxr-screen-avail-height", &d->available_height, 0) ||
      d->available_width > d->width || d->available_height > d->height)
    return false;
  if (cfg.contains("uxr-taskbar-height")) {
    int taskbar = 0;
    if (!d->height || !ReadDimension(cfg, "uxr-taskbar-height", &taskbar, 0) ||
        taskbar > d->height)
      return false;
    if (cfg.contains("uxr-screen-avail-height") &&
        d->available_height != d->height - taskbar)
      return false;
    d->available_height = d->height - taskbar;
  }
  if (auto it = cfg.find("uxr-device-pixel-ratio"); it != cfg.end()) {
    if (!StringToDouble(it->second, &d->device_scale_factor) ||
        !std::isfinite(d->device_scale_factor) ||
        d->device_scale_factor < 0.25 || d->device_scale_factor > 8)
      return false;
  }
  if (auto it = cfg.find("uxr-screen-orientation"); it != cfg.end()) {
    d->orientation = it->second;
    if (d->orientation != "landscape-primary" &&
        d->orientation != "landscape-secondary" &&
        d->orientation != "portrait-primary" &&
        d->orientation != "portrait-secondary")
      return false;
    d->orientation_angle = d->orientation.ends_with("secondary") ? 180 : 0;
  }
  if (cfg.contains("uxr-screen-orientation-angle")) {
    if (d->orientation.empty() ||
        !ReadDimension(cfg, "uxr-screen-orientation-angle", &d->orientation_angle, 0, 270) ||
        d->orientation_angle % 90 != 0)
      return false;
  }
  // This backend configures one emulated screen, not extra physical monitors.
  if (auto it = cfg.find("uxr-screen-extended"); it != cfg.end()) {
    if (it->second != "false" && it->second != "0")
      return false;
  }
  // Color depth is a backend capability, never a getter-only declaration.
  return !cfg.contains("uxr-screen-color-depth");
}
bool ParseSurfaceModes(const flat_map<std::string, std::string>& cfg,
                       UxrSurfaceModes* modes) {
  if (auto version = cfg.find("uxr-surface-modes-version");
      version != cfg.end() && version->second != "1") {
    return false;
  }
  const auto value = [&](const std::string& key) -> std::string {
    auto found = cfg.find(key);
    return found == cfg.end() ? std::string() : found->second;
  };
  modes->noise_disabled = cfg.contains("uxr-disable-fingerprint-noise");
  const std::string noise = value("uxr-mode-noise");
  modes->raw = noise == "raw";
  if (!noise.empty() && noise != "raw" && noise != "false" && noise != "true")
    return false;
  modes->noise_disabled |= noise == "false";
  if (auto rate = cfg.find("uxr-audio-default-sample-rate"); rate != cfg.end()) {
    double parsed = 0;
    if (!StringToDouble(rate->second, &parsed) || !std::isfinite(parsed) ||
        parsed < 3000 || parsed > 768000)
      return false;
    modes->audio_default_sample_rate = static_cast<float>(parsed);
  }
  const auto read_channels = [&](const char* key,
                                  std::optional<uint32_t>* destination) {
    const auto it = cfg.find(key);
    if (it == cfg.end())
      return true;
    unsigned parsed = 0;
    if (it->second.empty() ||
        it->second.find_first_not_of("0123456789") != std::string::npos ||
        !StringToUint(it->second, &parsed) || parsed < 1 || parsed > 32)
      return false;
    *destination = parsed;
    return true;
  };
  if (!read_channels("uxr-audio-channel-limit", &modes->audio_channel_limit) ||
      !read_channels("uxr-audio-default-channel-count",
                     &modes->audio_default_channel_count) ||
      (modes->audio_channel_limit && modes->audio_default_channel_count &&
       *modes->audio_default_channel_count > *modes->audio_channel_limit))
    return false;
  const auto read = [&](const char* surface, UxrSurfacePolicy* policy,
                        double default_amount, double maximum_amount,
                        std::initializer_list<UxrSurfaceMode> supported) {
    const std::string prefix = std::string("uxr-") + surface;
    auto mode = cfg.find(prefix + "-mode");
    if (mode == cfg.end()) {
      // These v1-only companions must not be silently ignored. Old
      // uxr-canvas-seed / uxr-audio-seed remain independent legacy inputs.
      return !cfg.contains(prefix + "-mode-seed") &&
             !cfg.contains(prefix + "-amount");
    }
    if (mode->second == "native")
      policy->mode = UxrSurfaceMode::kNative;
    else if (mode->second == "shardx")
      policy->mode = UxrSurfaceMode::kShardX;
    else
      return false;
    bool allowed = false;
    for (const auto candidate : supported)
      allowed |= policy->mode == candidate;
    if (!allowed)
      return false;
    const bool explicit_seed = cfg.contains(prefix + "-mode-seed");
    std::string seed = value(prefix + "-mode-seed");
    if (explicit_seed && seed.empty() && !policy->IsNative())
      return false;
    if (seed.empty())
      seed = value("uxr-fingerprint-seed");
    if (seed.empty())
      seed = value("uxr-canvas-seed");
    if (!policy->IsNative() &&
        (seed.empty() || seed.find_first_not_of("0123456789") != std::string::npos ||
         !StringToUint64(seed, &policy->seed) || policy->seed == 0)) {
      return false;
    }
    if (policy->mode == UxrSurfaceMode::kShardX) {
      if (explicit_seed && policy->seed > std::numeric_limits<uint32_t>::max())
        return false;
      if (!explicit_seed) {
        policy->seed = UxrPixelNoiseSeed(policy->seed);
        if (policy->seed == 0)
          policy->seed = 1;
      }
    }
    policy->amount = default_amount;
    auto amount = cfg.find(prefix + "-amount");
    if (amount != cfg.end() &&
        (!StringToDouble(amount->second, &policy->amount) ||
         !std::isfinite(policy->amount) || policy->amount < 0 ||
         policy->amount > maximum_amount)) {
      return false;
    }
    return true;
  };
  using M = UxrSurfaceMode;
  const auto supported = {M::kNative, M::kShardX};
  return read("canvas", &modes->canvas, 0, 0, supported) &&
         read("webgl", &modes->webgl, 0, 0, supported) &&
         read("audio", &modes->audio, 0, 1, supported) &&
         read("fonts", &modes->fonts, 0, 0, supported) &&
         read("rects", &modes->rects, 1, 16, supported) &&
         read("text", &modes->text, 1, 16, supported) &&
         read("sensors", &modes->sensors, 0.1, 100, supported) &&
         read("media", &modes->media, 0, 0, supported);
}

bool ValidateModeCapabilities(const flat_map<std::string, std::string>& cfg,
                              const UxrSurfaceModes& modes) {
  const auto value = [&](const char* key) -> std::string_view {
    const auto it = cfg.find(key);
    return it == cfg.end() ? std::string_view() : it->second;
  };
  const auto allowed = [&](const char* key,
                           std::initializer_list<std::string_view> values) {
    if (!cfg.contains(key))
      return true;
    for (auto expected : values)
      if (value(key) == expected)
        return true;
    return false;
  };
  for (const char* key : {"uxr-fonts-allowlist", "uxr-fonts-local-allowlist"}) {
    if (!cfg.contains(key))
      continue;
    const auto list = value(key);
    if (list.size() > 16384 || !IsStringUTF8(list))
      return false;
    if (list.empty())
      continue;  // Explicit empty list denies every local family in its scope.
    size_t offset = 0;
    unsigned count = 0;
    for (;;) {
      const auto comma = list.find(',', offset);
      const auto item = TrimWhitespaceASCII(list.substr(
          offset, comma == std::string_view::npos ? comma : comma - offset),
          TRIM_ALL);
      if (++count > 256 || item.empty() || item.size() > 256 ||
          item.find_first_of("\r\n\t") != std::string_view::npos ||
          item.find('\0') != std::string_view::npos)
        return false;
      if (comma == std::string_view::npos)
        break;
      offset = comma + 1;
    }
  }
  for (const char* key : {"uxr-gpu-mode", "uxr-webgl-identity-mode",
                          "uxr-webgpu-identity-mode",
                          "uxr-webgpu-features-mode"}) {
    if (!allowed(key, {"native", "custom", "cloak"}))
      return false;
  }
  if (!allowed("uxr-webgl-capabilities-mode", {"native", "custom"}) ||
      !allowed("uxr-webgpu-limits-mode", {"native", "cloak"})) {
    return false;
  }
  bool needs_profile = false;
  for (const char* key : {"uxr-gpu-mode", "uxr-webgl-identity-mode",
                          "uxr-webgpu-identity-mode",
                          "uxr-webgpu-features-mode",
                          "uxr-webgpu-limits-mode"}) {
    needs_profile |= value(key) == "cloak";
  }
  if (needs_profile) {
    int index = -1;
    if (!StringToInt(value("uxr-hardware-resolved-index"), &index) ||
        !UxrFindGpuProfileV1(value("uxr-hardware-resolved-family"), index)) {
      return false;
    }
  }
  if (UxrResolveGpuModeV1(value("uxr-webgl-identity-mode"),
                          value("uxr-gpu-mode")) == UxrGpuMode::kCustom &&
      (value("uxr-webgl-vendor").empty() ||
       value("uxr-webgl-renderer").empty())) {
    return false;
  }
  if (UxrResolveGpuModeV1(value("uxr-webgpu-identity-mode"),
                          value("uxr-gpu-mode")) == UxrGpuMode::kCustom &&
      (value("uxr-webgpu-vendor").empty() ||
       value("uxr-webgpu-architecture").empty())) {
    return false;
  }
  if (cfg.contains("uxr-webgl-masked-vendor") ||
      cfg.contains("uxr-webgl-masked-renderer")) {
    if (UxrResolveGpuModeV1(value("uxr-webgl-identity-mode"),
                            value("uxr-gpu-mode")) != UxrGpuMode::kCustom)
      return false;
    for (const char* key : {"uxr-webgl-masked-vendor",
                            "uxr-webgl-masked-renderer"}) {
      const auto text = value(key);
      if (text.empty() || text.size() > 512 || !IsStringUTF8(text) ||
          text.find_first_of("\r\n\t") != std::string_view::npos ||
          text.find('\0') != std::string_view::npos)
        return false;
    }
  }
  if (value("uxr-webgl-capabilities-mode") == "custom") {
    constexpr std::string_view limits[] = {
        "uxr-webgl-max-texture-size", "uxr-webgl-max-cube-map-texture-size",
        "uxr-webgl-max-renderbuffer-size", "uxr-webgl-max-3d-texture-size",
        "uxr-webgl-max-array-texture-layers"};
    for (const auto& [key, text] : cfg) {
      if (!std::string_view(key).starts_with("uxr-webgl-"))
        continue;
      bool numeric = false;
      for (const auto name : limits)
        numeric |= key == name;
      if (numeric) {
        int limit = 0;
        if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos ||
            !StringToInt(text, &limit) || limit < 1)
          return false;
      } else if (key != "uxr-webgl-vendor" && key != "uxr-webgl-renderer" &&
                 key != "uxr-webgl-extensions" && key != "uxr-webgl-real" &&
                 key != "uxr-webgl-fingerprint" &&
                 key != "uxr-webgl-masked-vendor" &&
                 key != "uxr-webgl-masked-renderer" &&
                 key != "uxr-webgl-mode" && key != "uxr-webgl-mode-seed" &&
                 key != "uxr-webgl-amount" && key != "uxr-webgl-identity-mode" &&
                 key != "uxr-webgl-capabilities-mode") {
        return false;
      }
    }
  }
  for (const char* key : {"uxr-media-audio-inputs", "uxr-media-audio-outputs",
                          "uxr-media-video-inputs"}) {
    if (!cfg.contains(key))
      continue;
    int count = -1;
    if (modes.media.mode != UxrSurfaceMode::kShardX ||
        value(key).empty() ||
        value(key).find_first_not_of("0123456789") != std::string_view::npos ||
        !StringToInt(value(key), &count) || count < 0 || count > 32) {
      return false;
    }
  }
  return true;
}

bool ParseGpuBackend(const flat_map<std::string, std::string>& cfg,
                     UxrGpuBackendPolicy* policy) {
  // Public launches retain one native rendering/capability contract by default.
  // Legacy presentation and noise remain explicit compatibility/test choices.
  policy->native = true;
  if (auto it = cfg.find("uxr-gpu-backend"); it != cfg.end()) {
    if (it->second != "native" && it->second != "compatibility")
      return false;
    policy->native = it->second == "native";
  }
  const bool noise_disabled = cfg.contains("uxr-disable-fingerprint-noise");
  if (auto it = cfg.find("uxr-pixel-noise"); it != cfg.end()) {
    if (it->second != "native" && it->second != "seeded")
      return false;
    policy->seeded_pixel_noise = it->second == "seeded" && !noise_disabled;
  }
  // Global disable wins even when the explicit mode has no usable seed.
  if (policy->seeded_pixel_noise && !noise_disabled) {
    const auto seed = cfg.find("uxr-canvas-seed");
    if (seed == cfg.end() || seed->second.empty() ||
        seed->second.find_first_not_of("0123456789") != std::string::npos ||
        !StringToUint64(seed->second, &policy->pixel_noise_seed) ||
        policy->pixel_noise_seed == 0) {
      return false;
    }
  }
  policy->canvas_pixel_noise =
      policy->seeded_pixel_noise && !noise_disabled;
  policy->canvas_text_noise = !policy->native && !noise_disabled;
  policy->capability_overrides = !policy->native;
  return true;
}

bool ValidateWebPreferences(const flat_map<std::string, std::string>& cfg) {
  const auto allowed = [&](const char* key,
                           std::initializer_list<std::string_view> values) {
    auto it = cfg.find(key);
    if (it == cfg.end())
      return true;
    for (std::string_view value : values)
      if (it->second == value)
        return true;
    return false;
  };
  for (const char* key : {"uxr-identity-mode", "uxr-cpu-mode",
                          "uxr-memory-mode", "uxr-screen-mode"}) {
    if (!allowed(key, {"native", "custom"}))
      return false;
  }
  if (auto mode = cfg.find("uxr-cpu-mode");
      mode != cfg.end() && mode->second == "custom") {
    auto cpu = cfg.find("uxr-hw-concurrency");
    int count = 0;
    if (cpu == cfg.end() || cpu->second.empty() ||
        cpu->second.find_first_not_of("0123456789") != std::string::npos ||
        !StringToInt(cpu->second, &count) || count < 1 || count > 128) {
      return false;
    }
  }
  if (auto mode = cfg.find("uxr-memory-mode");
      mode != cfg.end() && mode->second == "custom") {
    auto memory = cfg.find("uxr-device-memory");
    double amount = 0;
    if (memory == cfg.end() || !StringToDouble(memory->second, &amount) ||
        !std::isfinite(amount) || amount < 0.25 || amount > 32) {
      return false;
    }
  }
  for (const char* key : {"uxr-reduced-motion", "uxr-reduced-transparency",
                          "uxr-inverted-colors"}) {
    if (!allowed(key, {"true", "false", "1", "0"}))
      return false;
  }
  if (!allowed("uxr-color-scheme", {"dark", "light"}) ||
      !allowed("uxr-preferred-contrast", {"more", "less", "no-preference"}) ||
      !allowed("uxr-forced-colors", {"active", "none", "true", "false", "1", "0"}) ||
      !allowed("uxr-pointer", {"fine", "coarse", "none"}) ||
      !allowed("uxr-hover", {"hover", "none"})) {
    return false;
  }
  int touch = -1;
  // These new launch switches use the same decimal syntax as both SDKs.
  for (const char* key : {"uxr-max-touch-points", "uxr-timer-resolution"}) {
    if (auto it = cfg.find(key); it != cfg.end() &&
        (it->second.empty() ||
         it->second.find_first_not_of("0123456789") != std::string::npos))
      return false;
  }
  if (!ReadDimension(cfg, "uxr-max-touch-points", &touch, 0, 16))
    return false;
  const auto pointer = cfg.find("uxr-pointer");
  if (pointer != cfg.end()) {
    if ((pointer->second == "none" && touch > 0) ||
        (pointer->second == "coarse" && touch == 0))
      return false;
    if (auto hover = cfg.find("uxr-hover"); hover != cfg.end() &&
        pointer->second == "none" && hover->second == "hover")
      return false;
  }
  // Changing a layout map alone does not change the platform key mapping.
  return true;
}

bool ValidateCodecPolicy(const flat_map<std::string, std::string>& cfg) {
  for (const char* key : {"uxr-codec-h264", "uxr-codec-vp8", "uxr-codec-vp9",
                          "uxr-codec-av1", "uxr-codec-hevc"}) {
    const auto it = cfg.find(key);
    if (it == cfg.end() || it->second.empty() || it->second == "disabled" ||
        it->second == "native")
      continue;
    std::string_view remaining = it->second;
    bool supported = false;
    while (!remaining.empty()) {
      const auto comma = remaining.find(',');
      const auto token = remaining.substr(0, comma);
      if (token == "supported")
        supported = true;
      else if (token != "smooth" && token != "power-efficient")
        return false;
      if (comma == std::string_view::npos)
        break;
      remaining.remove_prefix(comma + 1);
      if (remaining.empty())
        return false;
    }
    if (!supported)
      return false;
  }
  return true;
}



int64_t QuantizeClock(int64_t value, int64_t quantum) {
  if (quantum <= 1)
    return value;
  // Preserve the native infinite/sentinel values and saturate at int64 bounds.
  if (value == std::numeric_limits<int64_t>::min() ||
      value == std::numeric_limits<int64_t>::max())
    return value;
  int64_t remainder = value % quantum;
  if (remainder < 0)
    remainder += quantum;
  if (value < std::numeric_limits<int64_t>::min() + remainder)
    return std::numeric_limits<int64_t>::min();
  return value - remainder;
}
}  // namespace

UxrConfig::UxrConfig() = default;
UxrConfig::~UxrConfig() = default;

// static
UxrConfig& UxrConfig::GetInstance() {
  static NoDestructor<UxrConfig> instance;
  return *instance;
}

bool UxrConfig::SetAll(const flat_map<std::string, std::string>& cfg,
                       uint32_t schema_version) {
  AutoLock locker(lock_);
  if (schema_version != kSchemaVersion) {
    validation_error_ = "unsupported persona schema";
    return false;
  }
  if (initialized_) {
    if (map_ == cfg)
      return true;
    validation_error_ = "persona snapshot is immutable";
    return false;
  }
  size_t bytes = 0;
  for (const auto& [key, value] : cfg) {
    if (key == "uxr-hdr" || key == "uxr-widevine-cdm" ||
        key.starts_with("uxr-offset-mode") || key.starts_with("uxr-voices-mode")) {
      validation_error_ = "unsupported surface mode";
      return false;
    }
    bytes += key.size() + value.size();
    if (key.empty() || key.size() > 128 || value.size() > 16384 ||
        key.find_first_not_of("abcdefghijklmnopqrstuvwxyz0123456789-") !=
            std::string::npos || value.find('\0') != std::string::npos) {
      validation_error_ = "invalid persona field";
      return false;
    }
  }
  if (cfg.size() > 256 || bytes > 65536) {
    validation_error_ = "persona snapshot exceeds bounds";
    return false;
  }
  for (const char* retired : {"uxr-font-policy", "uxr-font-whitelist",
                               "uxr-audio-render", "uxr-windows-font-metrics"}) {
    if (cfg.contains(retired)) {
      validation_error_ = "retired Lite font/audio control";
      return false;
    }
  }
  int timer_resolution_ms = 0;
  UxrSurfaceModes surface_modes;
  if (!ParseSurfaceModes(cfg, &surface_modes) ||
      !ValidateModeCapabilities(cfg, surface_modes)) {
    validation_error_ = "invalid fingerprint surface mode, version, seed or amount";
    return false;
  }
  if (!ValidateWebPreferences(cfg) ||
      !ValidateCodecPolicy(cfg) ||
      !ReadDimension(cfg, "uxr-timer-resolution", &timer_resolution_ms, 0, 1000)) {
    validation_error_ = "invalid input, font, clock, audio or codec policy";
    return false;
  }
  UxrDisplayConfig display;
  if (!ParseDisplay(cfg, &display)) {
    validation_error_ = "invalid or inconsistent display geometry";
    return false;
  }
  UxrGpuBackendPolicy gpu_backend_policy;
  if (!ParseGpuBackend(cfg, &gpu_backend_policy)) {
    validation_error_ =
        "invalid GPU backend or pixel noise policy: expected native|seeded pixel "
        "noise; seeded requires a nonzero decimal uint64 uxr-canvas-seed";
    return false;
  }
  map_ = cfg;
  display_ = display;
  gpu_backend_policy_ = gpu_backend_policy;
  surface_modes_ = surface_modes;
  timer_resolution_ms_.store(timer_resolution_ms, std::memory_order_relaxed);
  initialized_ = true;
  validation_error_.clear();
  return true;
}

bool UxrConfig::IsInitialized() const {
  AutoLock locker(lock_);
  return initialized_;
}

flat_map<std::string, std::string> UxrConfig::Snapshot() const {
  AutoLock locker(lock_);
  return map_;
}

UxrDisplayConfig UxrConfig::Display() const {
  AutoLock locker(lock_);
  return display_;
}

UxrGpuBackendPolicy UxrConfig::GpuBackendPolicy() const {
  AutoLock locker(lock_);
  return gpu_backend_policy_;
}

UxrSurfaceModes UxrConfig::SurfaceModes() const {
  AutoLock locker(lock_);
  return surface_modes_;
}

int64_t UxrConfig::TimerResolutionMicroseconds() const {
  return int64_t{timer_resolution_ms_.load(std::memory_order_relaxed)} * 1000;
}

int64_t UxrConfig::QuantizeClockMicroseconds(int64_t value) const {
  return QuantizeClock(value, TimerResolutionMicroseconds());
}

int64_t UxrConfig::QuantizeClockMilliseconds(int64_t value) const {
  return QuantizeClock(value, timer_resolution_ms_.load(std::memory_order_relaxed));
}


std::string UxrConfig::ValidationError() const {
  AutoLock locker(lock_);
  return validation_error_;
}

bool UxrConfig::Has(const std::string& key) const {
  AutoLock locker(lock_);
  return map_.contains(key);
}

std::string UxrConfig::Get(const std::string& key) const {
  AutoLock locker(lock_);
  auto it = map_.find(key);
  return it == map_.end() ? std::string() : it->second;
}

bool UxrConfig::GetInt(const std::string& key, int* out) const {
  AutoLock locker(lock_);
  auto it = map_.find(key);
  if (it == map_.end()) {
    return false;
  }
  int value = 0;
  if (!out || !StringToInt(it->second, &value))
    return false;
  *out = value;
  return true;
}

bool UxrConfig::GetDouble(const std::string& key, double* out) const {
  AutoLock locker(lock_);
  auto it = map_.find(key);
  double value = 0;
  if (!out || it == map_.end() || !StringToDouble(it->second, &value) ||
      !std::isfinite(value))
    return false;
  *out = value;
  return true;
}

bool UxrConfig::GetUint64(const std::string& key, uint64_t* out) const {
  AutoLock locker(lock_);
  auto it = map_.find(key);
  uint64_t value = 0;
  if (!out || it == map_.end() || it->second.empty() ||
      it->second.find_first_not_of("0123456789") != std::string::npos ||
      !StringToUint64(it->second, &value))
    return false;
  *out = value;
  return true;
}

}  // namespace base
