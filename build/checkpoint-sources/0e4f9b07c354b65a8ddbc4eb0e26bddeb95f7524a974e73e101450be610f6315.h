// Copyright 2026. Process-global persona config delivered to the renderer over
// IPC (NOT the command line) so persona values + seeds never appear in the
// renderer's /proc/cmdline. Populated once at renderer init; read-only after.
#ifndef BASE_UXR_CONFIG_H_
#define BASE_UXR_CONFIG_H_

#include <atomic>
#include <cstdint>
#include <map>
#include <string>

#include "base/base_export.h"
#include "base/containers/flat_map.h"
#include "base/synchronization/lock.h"
#include "base/uxr_surface_modes.h"

namespace base {

template <typename T>
class NoDestructor;

// Validated DIP geometry. Zero sizes/scales retain the native backend value.
struct BASE_EXPORT UxrDisplayConfig {
  int width = 0;
  int height = 0;
  int available_width = 0;
  int available_height = 0;
  int viewport_width = 0;
  int viewport_height = 0;
  int outer_width = 0;
  int outer_height = 0;
  int window_x = 0;
  int window_y = 0;
  bool has_position = false;
  double device_scale_factor = 0;
  std::string orientation;
  int orientation_angle = 0;
  bool enabled() const {
    return width > 0 || viewport_width > 0 || device_scale_factor > 0 ||
           !orientation.empty();
  }
};

// One immutable rendering policy for Canvas, WebGL and WebGPU. This is a
// backend contract, not an adapter identity: native APIs may select different
// GPUs, software paths, formats and color-management implementations.
struct BASE_EXPORT UxrGpuBackendPolicy {
  bool native = true;
  bool seeded_pixel_noise = false;
  uint64_t pixel_noise_seed = 0;
  bool canvas_pixel_noise = false;
  bool canvas_text_noise = false;
  bool capability_overrides = false;
};

class BASE_EXPORT UxrConfig {
 public:
  static constexpr uint32_t kSchemaVersion = 1;
  static UxrConfig& GetInstance();

  UxrConfig(const UxrConfig&) = delete;
  UxrConfig& operator=(const UxrConfig&) = delete;

  // One immutable snapshot per browser launch, inherited before renderer init.
  // Repeating the identical snapshot is idempotent; replacing it is rejected.
  // A rejected input never publishes partial state.
  bool SetAll(const flat_map<std::string, std::string>& cfg,
              uint32_t schema_version = kSchemaVersion);
  bool IsInitialized() const;
  flat_map<std::string, std::string> Snapshot() const;
  UxrDisplayConfig Display() const;
  UxrGpuBackendPolicy GpuBackendPolicy() const;
  UxrSurfaceModes SurfaceModes() const;
  UxrSurfacePolicy RectPolicy() const;
  // Optional shared, whole-millisecond grid for V8 and Blink clocks. Zero
  // retains Chromium's native privacy resolution. Internal scheduling is not
  // quantized; only web-exposed clock values use these helpers.
  int64_t TimerResolutionMicroseconds() const;
  int64_t QuantizeClockMicroseconds(int64_t value) const;
  int64_t QuantizeClockMilliseconds(int64_t value) const;
  // Zero means native audio. The real-time graph reads this without locks.
  std::string ValidationError() const;

  bool Has(const std::string& key) const;
  // Returns the value or "" if absent.
  std::string Get(const std::string& key) const;
  // Parses the value as an int; returns false if absent or unparseable.
  bool GetInt(const std::string& key, int* out) const;
  bool GetDouble(const std::string& key, double* out) const;
  bool GetUint64(const std::string& key, uint64_t* out) const;

  // Zero/false selects the caller's native fallback when no valid seed exists.

 private:
  friend class NoDestructor<UxrConfig>;
  UxrConfig();
  ~UxrConfig();

  mutable Lock lock_;
  flat_map<std::string, std::string> map_;
  UxrDisplayConfig display_;
  UxrGpuBackendPolicy gpu_backend_policy_;
  UxrSurfaceModes surface_modes_;
  std::atomic<int> timer_resolution_ms_{0};
  bool initialized_ = false;
  std::string validation_error_;
};

}  // namespace base

#endif  // BASE_UXR_CONFIG_H_
