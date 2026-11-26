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

inline uint32_t PopulationHash(std::string_view s) {
  if (s.empty())
    return 0;
  uint32_t h = static_cast<uint32_t>(s.size());
  size_t at = 0;
  const auto word = [&](size_t index) {
    return static_cast<uint32_t>(static_cast<unsigned char>(s[index])) |
        (static_cast<uint32_t>(static_cast<unsigned char>(s[index + 1])) << 8);
  };
  for (; at + 4 <= s.size(); at += 4) {
    h += word(at);
    h = (h << 16) ^ ((word(at + 2) << 11) ^ h);
    h += h >> 11;
  }
  switch (s.size() - at) {
    case 3:
      h += word(at);
      h ^= h << 16;
      h ^= static_cast<uint32_t>(static_cast<int8_t>(s[at + 2])) << 18;
      h += h >> 11;
      break;
    case 2:
      h += word(at);
      h ^= h << 11;
      h += h >> 17;
      break;
    case 1:
      h += static_cast<uint32_t>(static_cast<int8_t>(s[at]));
      h ^= h << 10;
      h += h >> 1;
      break;
    default:
      break;
  }
  h ^= h << 3; h += h >> 5; h ^= h << 4;
  h += h >> 17; h ^= h << 25;
  return h + (h >> 6);
}

// Exact unsigned arithmetic read from original VA 0x1887cc880..0x1887ccd1e.
// Deliberately independent of std::hash and its library/version-dependent ABI.
inline uint64_t LoadLE(std::string_view s, size_t offset, unsigned count = 8) {
  uint64_t word = 0;
  for (unsigned i = 0; i != count; ++i)
    word |= uint64_t{static_cast<unsigned char>(s[offset + i])} << (i * 8);
  return word;
}
inline uint64_t ShiftMix(uint64_t v) { return v ^ (v >> 47); }
inline uint64_t Hash16(uint64_t u, uint64_t v) {
  constexpr uint64_t kMul = 0x9ddfea08eb382d69ULL;
  uint64_t a = ShiftMix((u ^ v) * kMul);
  return ShiftMix((v ^ a) * kMul) * kMul;
}
inline std::pair<uint64_t, uint64_t> Weak32(
    std::string_view s, size_t at, uint64_t a, uint64_t b) {
  const uint64_t w = LoadLE(s, at), x = LoadLE(s, at + 8);
  const uint64_t y = LoadLE(s, at + 16), z = LoadLE(s, at + 24);
  a += w;
  b = std::rotr(b + a + z, 21);
  const uint64_t c = a;
  a += x + y;
  b += std::rotr(a, 44);
  return {a + z, b + c};
}
inline uint64_t OffsetHash(std::string_view s) {
  constexpr uint64_t k0 = 0xc3a5c85c97cb3127ULL;
  constexpr uint64_t k1 = 0xb492b66fbe98f273ULL;
  constexpr uint64_t k2 = 0x9ae16a3b2f90404fULL;
  constexpr uint64_t k3 = 0xc949d7c7509e6557ULL;
  const size_t n = s.size();
  if (n <= 16) {
    if (n > 8) {
      const uint64_t b = LoadLE(s, n - 8);
      return Hash16(LoadLE(s, 0), std::rotr(b + n, static_cast<int>(n))) ^ b;
    }
    if (n >= 4)
      return Hash16(n + (LoadLE(s, 0, 4) << 3), LoadLE(s, n - 4, 4));
    if (!n)
      return k2;
    const uint32_t y = static_cast<unsigned char>(s[0]) |
        (static_cast<uint32_t>(static_cast<unsigned char>(s[n / 2])) << 8);
    const uint32_t z = static_cast<uint32_t>(n) |
        (static_cast<uint32_t>(static_cast<unsigned char>(s[n - 1])) << 2);
    return ShiftMix(y * k2 ^ z * k3) * k2;
  }
  if (n <= 32) {
    const uint64_t a = LoadLE(s, 0) * k1, b = LoadLE(s, 8);
    const uint64_t c = LoadLE(s, n - 8) * k2, d = LoadLE(s, n - 16) * k0;
    return Hash16(std::rotr(a - b, 43) + std::rotr(c, 30) + d,
                  a + std::rotr(b ^ k3, 20) - c + n);
  }
  if (n <= 64) {
    uint64_t z = LoadLE(s, 24);
    uint64_t a = LoadLE(s, 0) + (n + LoadLE(s, n - 16)) * k0;
    uint64_t b = std::rotr(a + z, 52);
    uint64_t c = std::rotr(a, 37);
    a += LoadLE(s, 8);
    c += std::rotr(a, 7);
    a += LoadLE(s, 16);
    const uint64_t vf = a + z, vs = b + std::rotr(a, 31) + c;
    a = LoadLE(s, 16) + LoadLE(s, n - 32);
    z += LoadLE(s, n - 8);
    b = std::rotr(a + z, 52);
    c = std::rotr(a, 37);
    a += LoadLE(s, n - 24);
    c += std::rotr(a, 7);
    a += LoadLE(s, n - 16);
    const uint64_t wf = a + z, ws = b + std::rotr(a, 31) + c;
    const uint64_t r = ShiftMix((vf + ws) * k2 + (wf + vs) * k0);
    return ShiftMix(r * k0 + vs) * k2;
  }
  uint64_t x = LoadLE(s, n - 40);
  uint64_t y = LoadLE(s, n - 16) + LoadLE(s, n - 56);
  uint64_t z = Hash16(LoadLE(s, n - 48) + n, LoadLE(s, n - 24));
  auto v = Weak32(s, n - 64, n, z);
  auto w = Weak32(s, n - 32, y + k1, x);
  x = x * k1 + LoadLE(s, 0);
  const size_t count = (n - 1) & ~size_t{63};
  for (size_t at = 0; at < count; at += 64) {
    x = std::rotr(x + y + v.first + LoadLE(s, at + 8), 37) * k1;
    y = std::rotr(y + v.second + LoadLE(s, at + 48), 42) * k1;
    x ^= w.second;
    y += v.first + LoadLE(s, at + 40);
    z = std::rotr(z + w.first, 33) * k1;
    v = Weak32(s, at, v.second * k1, x + w.first);
    w = Weak32(s, at + 32, z + w.second, y + LoadLE(s, at + 16));
    std::swap(z, x);
  }
  return Hash16(Hash16(v.first, w.first) + ShiftMix(y) * k1 + z,
                Hash16(v.second, w.second) + x);
}

}  // namespace base::uxr_surface_v1
#endif  // BASE_UXR_SURFACE_MATH_H_
