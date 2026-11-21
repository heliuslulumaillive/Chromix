// Copyright 2026. Independent translations of measured, versioned strategies.
#ifndef BASE_UXR_SURFACE_MATH_H_
#define BASE_UXR_SURFACE_MATH_H_

#include <algorithm>
#include <bit>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <string_view>
#include <utility>

namespace base::uxr_surface_v1 {

inline uint32_t FnvWord(uint32_t word, uint32_t seed) {
  uint32_t h = 2166136261u ^ seed;
  for (unsigned shift = 0; shift != 32; shift += 8)
    h = (h ^ ((word >> shift) & 255u)) * 16777619u;
  return h;
}

inline uint32_t Mix32(uint32_t x) {
  x = (x ^ (x >> 16)) * 0x85ebca6bu;
  x = (x ^ (x >> 13)) * 0xc2b2ae35u;
  return x ^ (x >> 16);
}

inline uint32_t FirstRandom(uint32_t seed) {
  return std::rotl(Mix32(seed + 0x3c6ef372u) * 5u, 7) * 9u;
}

inline float RectDelta(float width, float height, uint32_t seed, double amount) {
  const uint32_t h = FnvWord(std::bit_cast<uint32_t>(height),
                            FnvWord(std::bit_cast<uint32_t>(width), seed));
  return static_cast<float>((FirstRandom(h) * 0x1p-32 * 2.0 - 1.0) * amount);
}

inline std::pair<float, float> RectSize(float width, float height,
                                      uint32_t seed, double amount) {
  if (!seed || !std::isfinite(width) || !std::isfinite(height) ||
      !std::isfinite(amount))
    return {width, height};
  const float delta = RectDelta(width, height, seed, amount);
  return {std::max(0.0f, width + delta), std::max(0.0f, height + delta)};
}

inline double TextWidth(double width, uint32_t seed, double amount) {
  if (!seed || !(width > 0x1p-150) || !std::isfinite(width) ||
      !std::isfinite(amount))
    return width;
  const float original = static_cast<float>(width);
  return static_cast<double>(original + RectDelta(original, original, seed, amount));
}

inline double Acceleration(double value, uint32_t seed, char axis, double amount) {
  if (!seed || !std::isfinite(amount))
    return value;
  const double delta = (FirstRandom(seed ^ static_cast<uint32_t>(axis)) *
                            0x1p-32 - 0.5) * 2.0 * amount;
  return value + delta;
}

inline unsigned AudioBits(double intensity) {
  if (!(intensity > 0) || !std::isfinite(intensity))
    return 8;
  const double n = std::round(std::log2(intensity * 0x1p23));
  return static_cast<unsigned>(std::clamp(n, 1.0, 16.0));
}

inline float AudioSample(float sample, uint32_t seed, uint32_t salt, unsigned n) {
  if (!seed)
    return sample;
  const uint32_t bits = std::bit_cast<uint32_t>(sample);
  const uint32_t exponent = bits & 0x7f800000u;
  if (exponent == 0 || exponent == 0x7f800000u)
    return sample;
  const uint32_t low_mask = (uint32_t{1} << n) - 1;
  const uint32_t masked = bits & ~low_mask;
  uint32_t h = FnvWord(masked, seed ^ salt);
  h ^= h >> 15;
  return std::bit_cast<float>(masked | (h & low_mask));
}

template <class Range>
inline void AudioFloats(Range samples, uint32_t seed, uint32_t salt, double intensity) {
  const unsigned n = AudioBits(intensity);
  for (float& value : samples)
    value = AudioSample(value, seed, salt, n);
}

template <class Range>
inline void AudioBytes(Range samples, uint32_t seed, uint32_t salt) {
  if (!seed)
    return;
  const uint32_t basis = FnvWord(seed ^ salt, 0);
  for (auto& value : samples) {
    if (value == 0 || value == 128)
      continue;
    const uint32_t masked = static_cast<uint32_t>(value) & 254u;
    const uint32_t h = (masked ^ basis) * 16777619u;
    value = static_cast<uint8_t>(masked | ((h ^ (h >> 15)) & 1u));
  }
}

// compatible-v1: keep caller-owned elements beyond the native write range intact.
template <class Range>
inline void AudioFloatsWritten(Range samples, size_t written, uint32_t seed,
                               uint32_t salt, double intensity) {
  AudioFloats(samples.first(std::min(samples.size(), written)), seed, salt, intensity);
}
template <class Range>
inline void AudioBytesWritten(Range samples, size_t written, uint32_t seed,
                              uint32_t salt) {
  AudioBytes(samples.first(std::min(samples.size(), written)), seed, salt);
}

inline uint32_t FontHash(std::string_view name, uint32_t seed) {
  uint32_t h = 2166136261u ^ seed;
  for (unsigned char c : name) {
    if (c == 0)
      break;
    h = (h ^ c) * 16777619u;
  }
  return h;
}

}  // namespace base::uxr_surface_v1
#endif  // BASE_UXR_SURFACE_MATH_H_
