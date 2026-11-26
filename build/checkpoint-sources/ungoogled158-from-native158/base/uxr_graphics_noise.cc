// Copyright 2026. Independent implementation from byte-verified models.
#include "base/uxr_graphics_noise.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <limits>
#include <string>

#include "base/uxr_pixel_noise.h"

namespace base {
namespace {

uint32_t RotateLeft(uint32_t value, unsigned count) {
  return (value << count) | (value >> (32 - count));
}

uint32_t Fmix32(uint32_t value) {
  value ^= value >> 16;
  value *= 0x85ebca6bu;
  value ^= value >> 13;
  value *= 0xc2b2ae35u;
  return value ^ (value >> 16);
}

class Xoshiro128 {
 public:
  explicit Xoshiro128(uint32_t seed) {
    for (uint32_t i = 0; i < 4; ++i)
      state_[i] = Fmix32(seed + (i + 1) * 0x9e3779b9u);
  }
  uint32_t Next() {
    const uint32_t result = RotateLeft(state_[1] * 5u, 7) * 9u;
    const uint32_t temporary = state_[1] << 9;
    state_[2] ^= state_[0];
    state_[3] ^= state_[1];
    state_[1] ^= state_[2];
    state_[0] ^= state_[3];
    state_[2] ^= temporary;
    state_[3] = RotateLeft(state_[3], 11);
    return result;
  }

 private:
  std::array<uint32_t, 4> state_;
};

bool ValidLayout(const UxrPixelRegion& region) {
  if (region.width == 0 || region.height == 0)
    return false;
  const size_t row_bytes = static_cast<size_t>(region.width) * 4;
  if (row_bytes / 4 != region.width || region.row_stride < row_bytes ||
      region.bytes.size() < row_bytes)
    return false;
  if (!region.preserved_inputs.empty() &&
      region.preserved_inputs.size() <
          (uint64_t{region.width} * region.height + 7) / 8) {
    return false;
  }
  return region.height - 1 <=
         (region.bytes.size() - row_bytes) / region.row_stride;
}

span<uint8_t> Pixel(const UxrPixelRegion& region, uint32_t x, uint32_t y) {
  return region.bytes.subspan(
      static_cast<size_t>(y) * region.row_stride + static_cast<size_t>(x) * 4,
      size_t{4});
}

uint32_t Channel(const UxrPixelRegion& region, uint32_t logical_rgb) {
  return region.bgra ? 2 - logical_rgb : logical_rgb;
}

bool AssignShardBit(span<uint8_t> pixel, uint32_t channel, uint32_t bit) {
  const uint32_t alpha = pixel[3];
  if (alpha == 0)
    return false;
  const uint32_t candidate = (pixel[channel] & 0xfeu) | bit;
  if (alpha != 255) {
    const uint32_t product = candidate * alpha;
    const uint32_t premultiplied =
        (product + ((product + 128u) >> 8) + 128u) >> 8;
    const uint32_t scale = 0xff000000u / alpha;
    const uint32_t back = (premultiplied * scale + 0x800000u) >> 24;
    if (candidate != back)
      return false;
  }
  const bool changed = pixel[channel] != candidate;
  pixel[channel] = static_cast<uint8_t>(candidate);
  return changed;
}

UxrPixelResult ApplyChromix(const UxrSurfacePolicy& policy,
                            UxrPixelSurface surface,
                            const UxrPixelRegion& region) {
  const uint32_t seed = UxrPixelNoiseSeed(policy.seed);
  bool changed = false;
  for (uint32_t y = 0; y < region.height; ++y) {
    const uint32_t absolute_y =
        surface == UxrPixelSurface::kWebGLReadback
            ? region.canvas_height - 1 -
                  (static_cast<uint32_t>(region.origin_y) + y)
            : static_cast<uint32_t>(region.origin_y) + y;
    for (uint32_t x = 0; x < region.width; ++x) {
      auto pixel = Pixel(region, x, y);
      if (UxrPreservesInputPixel(region.preserved_inputs, region.width, x, y))
        continue;
      if (pixel[3] == 0)
        continue;
      for (uint32_t channel = 0; channel < 3; ++channel) {
        const uint32_t index = Channel(region, channel);
        const uint8_t value =
            UxrNoiseChannel(seed, static_cast<uint32_t>(region.origin_x) + x,
                            absolute_y, channel, pixel[index]);
        changed |= value != pixel[index];
        pixel[index] = value;
      }
    }
  }
  return changed ? UxrPixelResult::kApplied : UxrPixelResult::kUnchanged;
}

bool ValidNeighborhood(const UxrPixelNeighborhood& view, size_t pixel_bytes) {
  if (view.bytes.empty())
    return view.width == 0 && view.height == 0;
  const size_t row_bytes = static_cast<size_t>(view.width) * pixel_bytes;
  return view.width && view.height && row_bytes / pixel_bytes == view.width &&
         view.row_stride >= row_bytes && view.bytes.size() >= row_bytes &&
         view.height - 1 <= (view.bytes.size() - row_bytes) / view.row_stride;
}

// Absolute 2x2 cells are independent of the requested crop. Clamp only when
// the halo's completed read reaches a real surface edge. RGB bit zero is not
// part of the decision; otherwise a previous assignment could move the guard.
bool ShardFlatCell(const UxrPixelRegion& region, UxrPixelSurface surface,
                   uint32_t x, uint32_t y, bool floating = false) {
  const auto& view = region.neighborhood;
  if (view.bytes.empty())
    return false;  // historical/reference helper calls retain their old path
  const int64_t ax = int64_t{region.origin_x} + x;
  const int64_t ay = int64_t{region.origin_y} + y;
  const int64_t last_x = int64_t{view.origin_x} + view.width - 1;
  const int64_t last_y = int64_t{view.origin_y} + view.height - 1;
  if (ax < view.origin_x || ay < view.origin_y || ax > last_x || ay > last_y)
    return true;  // native clipped/unknown pixels are never invented
  const int64_t px = std::clamp(ax ^ 1, int64_t{view.origin_x}, last_x);
  // WebGL's mask is anchored at the framebuffer bottom. Export rows are
  // top-down, including odd-height surfaces whose 2x2 parity would otherwise
  // disagree with readPixels.
  const int64_t paired_y = surface == UxrPixelSurface::kWebGLExport
      ? int64_t{region.canvas_height} - 1 -
            ((int64_t{region.canvas_height} - 1 - ay) ^ 1)
      : ay ^ 1;
  const int64_t py = std::clamp(paired_y, int64_t{view.origin_y}, last_y);
  const size_t bytes_per_pixel = floating ? 16 : 4;
  auto canonical = [&](int64_t cx, int64_t cy) {
    const size_t offset = static_cast<size_t>(cy - view.origin_y) * view.row_stride +
                          static_cast<size_t>(cx - view.origin_x) * bytes_per_pixel;
    auto pixel = view.bytes.subspan(offset, bytes_per_pixel);
    std::array<uint32_t, 4> value;
    if (floating) {
      for (size_t channel = 0; channel < 4; ++channel) {
        std::memcpy(&value[channel], pixel.subspan(channel * 4, size_t{4}).data(), 4);
        if (channel != 3)
          value[channel] &= ~1u;
      }
    } else {
      value[3] = pixel[3];
      for (uint32_t channel = 0; channel < 3; ++channel) {
        uint32_t component = pixel[Channel(region, channel)];
        if (region.premultiplied && surface == UxrPixelSurface::kWebGLExport &&
            value[3] != 255) {
          const uint32_t product = component * value[3];
          component = (product + ((product + 128u) >> 8) + 128u) >> 8;
        }
        value[channel] = component & 0xfeu;
      }
    }
    return value;
  };
  const auto center = canonical(ax, ay);
  return center == canonical(px, ay) && center == canonical(ax, py) &&
         center == canonical(px, py);
}

std::array<uint32_t, 2> ShardWebGLMask(uint32_t seed, uint32_t x, uint32_t y);

UxrPixelResult ApplyShardCanvasSemantic(const UxrSurfacePolicy& policy,
                                        const UxrPixelRegion& region) {
  if (uint64_t{region.canvas_width} * region.canvas_height < 1024)
    return UxrPixelResult::kUnchanged;
  bool changed = false;
  for (uint32_t y = 0; y < region.height; ++y) {
    for (uint32_t x = 0; x < region.width; ++x) {
      auto pixel = Pixel(region, x, y);
      if (UxrPreservesInputPixel(region.preserved_inputs, region.width, x, y))
        continue;
      if (!pixel[3])
        continue;
      // Separate Canvas's coordinate stream from WebGL. One candidate per
      // 256 non-flat pixels, one selected RGB low bit, no read counter or
      // content-dependent early stop. Large blank margins do not dilute it.
      const auto mask = ShardWebGLMask(static_cast<uint32_t>(policy.seed) ^ 0x43415633u,
          static_cast<uint32_t>(int64_t{region.origin_x} + x),
          static_cast<uint32_t>(int64_t{region.origin_y} + y));
      // The coordinate mask is independent of pixel contents. Only selected
      // candidates can change; flatness need not be read for the other pixels.
      if ((mask[0] & 255u) != 0 ||
          ShardFlatCell(region, UxrPixelSurface::kCanvas2D, x, y))
        continue;
      changed |= AssignShardBit(pixel, Channel(region, (mask[0] >> 8) % 3),
                                mask[1] & 1u);
    }
  }
  return changed ? UxrPixelResult::kApplied : UxrPixelResult::kUnchanged;
}

UxrPixelResult ApplyShardCanvas(const UxrSurfacePolicy& policy,
                                const UxrPixelRegion& region) {
  const uint64_t pixels = uint64_t{region.canvas_width} * region.canvas_height;
  if (pixels < 1024)
    return UxrPixelResult::kUnchanged;
  Xoshiro128 random(static_cast<uint32_t>(policy.seed));
  bool changed = false;
  for (uint32_t iteration = 0; iteration < 32; ++iteration) {
    const uint32_t value = random.Next();
    const uint64_t position = value % pixels;
    const int64_t x =
        static_cast<int64_t>(position % region.canvas_width) - region.origin_x;
    const int64_t y =
        static_cast<int64_t>(position / region.canvas_width) - region.origin_y;
    if (x < 0 || y < 0 || x >= region.width || y >= region.height)
      continue;
    if (UxrPreservesInputPixel(region.preserved_inputs, region.width,
                               static_cast<uint32_t>(x),
                               static_cast<uint32_t>(y)))
      continue;
    changed |= AssignShardBit(
        Pixel(region, static_cast<uint32_t>(x), static_cast<uint32_t>(y)),
        Channel(region, (value >> 16) % 3), (value >> 24) & 1);
  }
  return changed ? UxrPixelResult::kApplied : UxrPixelResult::kUnchanged;
}

// A ShardX-derived sparse mask addressed by absolute pixel coordinates.
// There is no process counter and no restart at a crop/tile boundary.
// Assignment (rather than XOR) prevents repeated application accumulating.
std::array<uint32_t, 2> ShardWebGLMask(uint32_t seed, uint32_t x, uint32_t y) {
  Xoshiro128 random(seed ^ Fmix32(x + 0x9e3779b9u) ^
                    RotateLeft(Fmix32(y + 0x85ebca6bu), 16));
  return {random.Next(), random.Next()};
}

bool AssignShardWebGLBit(span<uint8_t> pixel, uint32_t channel,
                        uint32_t bit, bool premultiplied, bool exported) {
  const uint32_t alpha = pixel[3];
  if (!alpha)
    return false;
  uint32_t value = pixel[channel];
  if (premultiplied && exported && alpha != 255) {
    const uint32_t product = value * alpha;
    value = (product + ((product + 128u) >> 8) + 128u) >> 8;
  }
  // Exact color endpoints carry no fractional rendering detail. Preserve
  // them just as the floating-point path preserves 0 and 1. For premultiplied
  // storage, alpha is the maximum representable color, including exports
  // mapped back to that same storage lattice.
  const uint32_t maximum = premultiplied ? alpha : 255;
  if (value == 0 || value == maximum)
    return false;
  uint32_t candidate = (value & 0xfeu) | bit;
  // A one-step change in premultiplied storage is representable even at very
  // low alpha. Perturbing a straight LSB instead could make every sample at
  // alpha 64 a no-op. Keep invalid premultiplied colors on their native path.
  if (premultiplied && (value > alpha || candidate > alpha))
    return false;
  if (premultiplied && exported && alpha != 255)
    candidate = (candidate * (0xff000000u / alpha) + 0x800000u) >> 24;
  const bool changed = pixel[channel] != candidate;
  pixel[channel] = static_cast<uint8_t>(candidate);
  return changed;
}

UxrPixelResult ApplyShardWebGLV2(const UxrSurfacePolicy& policy,
                                UxrPixelSurface surface,
                                const UxrPixelRegion& region) {
  bool changed = false;
  for (uint32_t y = 0; y < region.height; ++y) {
    const uint32_t absolute_y = surface == UxrPixelSurface::kWebGLReadback
        ? static_cast<uint32_t>(region.origin_y) + y
        : region.canvas_height - 1 -
              (static_cast<uint32_t>(region.origin_y) + y);
    for (uint32_t x = 0; x < region.width; ++x) {
      auto pixel = Pixel(region, x, y);
      if (!pixel[3])
        continue;
      const auto mask = ShardWebGLMask(static_cast<uint32_t>(policy.seed),
          static_cast<uint32_t>(region.origin_x) + x, absolute_y);
      if (((mask[0] & 255u) != 0 && ((mask[0] >> 8) & 255u) != 0 &&
           ((mask[0] >> 16) & 255u) != 0) ||
          ShardFlatCell(region, surface, x, y))
        continue;
      for (uint32_t channel = 0; channel < 3; ++channel) {
        if (((mask[0] >> (channel * 8)) & 255u) == 0)
          changed |= AssignShardWebGLBit(pixel, Channel(region, channel),
              (mask[1] >> channel) & 1u, region.premultiplied,
              surface == UxrPixelSurface::kWebGLExport);
      }
    }
  }
  return changed ? UxrPixelResult::kApplied : UxrPixelResult::kUnchanged;
}

UxrPixelResult ApplyShardWebGL(const UxrSurfacePolicy& policy,
                               const UxrPixelRegion& region,
                               uint32_t sequence) {
  Xoshiro128 random(static_cast<uint32_t>(policy.seed) ^ sequence);
  bool changed = false;
  for (uint32_t y = 0; y < region.height; ++y) {
    for (uint32_t x = 0; x < region.width; ++x) {
      auto pixel = Pixel(region, x, y);
      if (pixel[3] == 0)
        continue;
      const uint32_t value = random.Next();
      for (uint32_t channel = 0; channel < 3; ++channel) {
        if (((value >> (channel * 8)) & 255u) == 0) {
          pixel[Channel(region, channel)] ^= 1;
          changed = true;
        }
      }
    }
  }
  return changed ? UxrPixelResult::kApplied : UxrPixelResult::kUnchanged;
}

UxrPixelResult ApplyCloak(const UxrSurfacePolicy& policy,
                          UxrPixelSurface surface,
                          const UxrPixelRegion& region,
                          UxrPixelFlavor flavor) {
  const bool compatible = flavor == UxrPixelFlavor::kCompatibleV1;
  if (!compatible && surface == UxrPixelSurface::kCanvas2D &&
      (region.origin_x != 0 || region.origin_y != 0 ||
       region.width != region.canvas_width ||
       region.height != region.canvas_height)) {
    return UxrPixelResult::kUnsupportedRegion;
  }
  const uint32_t whole_width = compatible ? region.canvas_width : region.width;
  const uint32_t whole_height =
      compatible ? region.canvas_height : region.height;
  if (whole_width < 8 || whole_height < 8)
    return UxrPixelResult::kUnchanged;
  // Half-transparent arithmetic was not byte-verified for this target.
  // Check before modifying anything, including when a later pixel is partial.
  if (!compatible) {
    for (uint32_t y = 0; y < region.height; ++y) {
      for (uint32_t x = 0; x < region.width; ++x) {
        const uint8_t alpha = Pixel(region, x, y)[3];
        if (alpha != 0 && alpha != 255)
          return UxrPixelResult::kUnsupportedAlpha;
      }
    }
  }
  const uint64_t pixels = uint64_t{whole_width} * whole_height;
  const std::string seed = std::to_string(policy.seed);
  const uint32_t position_hash = UxrCloakHashV1(seed + "_pos");
  bool changed = false;
  for (uint32_t i = 0; i < 10; ++i) {
    const uint32_t mixed = (position_hash ^ i) * 0x17385ca9u + 0x47502932u;
    const uint64_t position = mixed % pixels;
    const uint32_t x = static_cast<uint32_t>(position % whole_width);
    const uint32_t y = static_cast<uint32_t>(position / whole_width);
    const int64_t local_x = compatible ? int64_t{x} - region.origin_x : x;
    const int64_t local_y =
        !compatible ? y
        : surface == UxrPixelSurface::kWebGLReadback
            ? int64_t{whole_height} - 1 - y - region.origin_y
            : int64_t{y} - region.origin_y;
    if (local_x < 0 || local_y < 0 || local_x >= region.width ||
        local_y >= region.height) {
      continue;
    }
    auto pixel = Pixel(region, static_cast<uint32_t>(local_x),
                       static_cast<uint32_t>(local_y));
    if (UxrPreservesInputPixel(region.preserved_inputs, region.width,
                               static_cast<uint32_t>(local_x),
                               static_cast<uint32_t>(local_y)))
      continue;
    if (pixel[3] == 0)
      continue;
    // Compatible v1 leaves partial-alpha pixels native. Unlike a whole-image
    // rejection this remains consistent when an opaque region is cropped.
    if (compatible && pixel[3] != 255)
      continue;
    const uint32_t bits = UxrCloakHashV1(seed + "_" + std::to_string(x) + "_" +
                                         std::to_string(y));
    // Reference goldens retain the original physical-slot discrepancy.
    // The explicit compatible-v1 production flavor uses logical RGB.
    for (uint32_t channel = 0; channel < 3; ++channel) {
      const uint32_t index = compatible ? Channel(region, channel) : channel;
      const uint8_t value = (pixel[index] & 0xfeu) | ((bits >> channel) & 1u);
      changed |= value != pixel[index];
      pixel[index] = value;
    }
  }
  return changed ? UxrPixelResult::kApplied : UxrPixelResult::kUnchanged;
}

}  // namespace

uint32_t UxrCloakHashV1(std::string_view bytes) {
  if (bytes.empty() || bytes.size() >= 0x80000000u)
    return 0;
  uint32_t hash = static_cast<uint32_t>(bytes.size());
  size_t offset = 0;
  const auto read16 = [&bytes](size_t at) {
    return static_cast<uint32_t>(static_cast<unsigned char>(bytes[at])) |
           (static_cast<uint32_t>(static_cast<unsigned char>(bytes[at + 1]))
            << 8);
  };
  for (; bytes.size() - offset >= 4; offset += 4) {
    hash += read16(offset);
    const uint32_t temporary = (read16(offset + 2) << 11) ^ hash;
    hash = (hash << 16) ^ temporary;
    hash += hash >> 11;
  }
  const auto signed_byte = [&bytes](size_t at) {
    const uint32_t value = static_cast<unsigned char>(bytes[at]);
    return value < 128 ? value : value - 256u;
  };
  switch (bytes.size() - offset) {
    case 3:
      hash += read16(offset);
      hash ^= hash << 16;
      hash ^= signed_byte(offset + 2) << 18;
      hash += hash >> 11;
      break;
    case 2:
      hash += read16(offset);
      hash ^= hash << 11;
      hash += hash >> 17;
      break;
    case 1:
      hash += signed_byte(offset);
      hash ^= hash << 10;
      hash += hash >> 1;
      break;
    default:
      break;
  }
  hash ^= hash << 3;
  hash += hash >> 5;
  hash ^= hash << 4;
  hash += hash >> 17;
  hash ^= hash << 25;
  return hash + (hash >> 6);
}

UxrPixelResult UxrApplyGraphicsNoiseV1(const UxrSurfacePolicy& policy,
                                       UxrPixelSurface surface,
                                       const UxrPixelRegion& region,
                                       uint32_t shard_webgl_sequence,
                                       UxrPixelFlavor flavor) {
  if (policy.IsLegacy())
    return UxrPixelResult::kLegacy;
  if (policy.IsNative())
    return UxrPixelResult::kNative;
  if (policy.seed == 0 ||
      (policy.mode == UxrSurfaceMode::kShardX &&
       policy.seed > std::numeric_limits<uint32_t>::max())) {
    return UxrPixelResult::kUnsupportedSeed;
  }
  if (region.width == 0 || region.height == 0)
    return UxrPixelResult::kUnchanged;
  if (!ValidLayout(region))
    return UxrPixelResult::kInvalidLayout;
  if (policy.mode == UxrSurfaceMode::kShardX &&
      !ValidNeighborhood(region.neighborhood, 4))
    return UxrPixelResult::kInvalidLayout;
  switch (policy.mode) {
    case UxrSurfaceMode::kChromix:
      return ApplyChromix(policy, surface, region);
    case UxrSurfaceMode::kShardX:
      if (surface == UxrPixelSurface::kCanvas2D)
        return flavor == UxrPixelFlavor::kCompatibleV1 && !region.neighborhood.bytes.empty()
                   ? ApplyShardCanvasSemantic(policy, region)
                   : ApplyShardCanvas(policy, region);
      if (flavor == UxrPixelFlavor::kCompatibleV1)
        return ApplyShardWebGLV2(policy, surface, region);
      if (surface == UxrPixelSurface::kWebGLExport)
        return UxrPixelResult::kUnchanged;
      return ApplyShardWebGL(policy, region, shard_webgl_sequence);
    case UxrSurfaceMode::kCloak:
      return ApplyCloak(policy, surface, region, flavor);
    case UxrSurfaceMode::kLegacy:
      return UxrPixelResult::kLegacy;
    case UxrSurfaceMode::kNative:
      return UxrPixelResult::kNative;
  }
  return UxrPixelResult::kInvalidLayout;
}

UxrPixelResult UxrApplyShardWebGLFloatV2(
    const UxrSurfacePolicy& policy, const UxrPixelRegion& region) {
  const size_t row_bytes = static_cast<size_t>(region.width) * 16;
  if (!region.width || !region.height || row_bytes / 16 != region.width ||
      region.row_stride < row_bytes || region.bytes.size() < row_bytes ||
      region.height - 1 > (region.bytes.size() - row_bytes) / region.row_stride)
    return UxrPixelResult::kInvalidLayout;
  if (!ValidNeighborhood(region.neighborhood, 16))
    return UxrPixelResult::kInvalidLayout;
  if (policy.mode != UxrSurfaceMode::kShardX || !policy.seed ||
      policy.seed > std::numeric_limits<uint32_t>::max())
    return UxrPixelResult::kUnsupportedSeed;
  bool changed = false;
  for (uint32_t y = 0; y < region.height; ++y) {
    for (uint32_t x = 0; x < region.width; ++x) {
      auto pixel = region.bytes.subspan(y * region.row_stride + x * size_t{16},
                                        size_t{16});
      float alpha;
      std::memcpy(&alpha, pixel.subspan(size_t{12}, size_t{4}).data(), sizeof(alpha));
      if (!std::isfinite(alpha) || alpha == 0)
        continue;
      const auto mask = ShardWebGLMask(static_cast<uint32_t>(policy.seed),
          static_cast<uint32_t>(region.origin_x) + x,
          static_cast<uint32_t>(region.origin_y) + y);
      if (((mask[0] & 255u) != 0 && ((mask[0] >> 8) & 255u) != 0 &&
           ((mask[0] >> 16) & 255u) != 0) ||
          ShardFlatCell(region, UxrPixelSurface::kWebGLReadback, x, y, true))
        continue;
      for (uint32_t channel = 0; channel < 3; ++channel) {
        if (((mask[0] >> (channel * 8)) & 255u) != 0)
          continue;
        float value;
        uint32_t bits;
        auto bytes = pixel.subspan(channel * size_t{4}, size_t{4});
        std::memcpy(&value, bytes.data(), sizeof(value));
        if (!std::isfinite(value) || value == 0 || value == 1)
          continue;
        std::memcpy(&bits, &value, sizeof(bits));
        const uint32_t replacement = (bits & ~1u) | ((mask[1] >> channel) & 1u);
        changed |= bits != replacement;
        std::memcpy(bytes.data(), &replacement, sizeof(replacement));
      }
    }
  }
  return changed ? UxrPixelResult::kApplied : UxrPixelResult::kUnchanged;
}

const char* UxrPixelResultName(UxrPixelResult result) {
  switch (result) {
    case UxrPixelResult::kApplied:
      return "applied";
    case UxrPixelResult::kUnchanged:
      return "unchanged";
    case UxrPixelResult::kNative:
      return "native";
    case UxrPixelResult::kLegacy:
      return "legacy";
    case UxrPixelResult::kInvalidLayout:
      return "invalid-layout";
    case UxrPixelResult::kUnsupportedSeed:
      return "unsupported-seed";
    case UxrPixelResult::kUnsupportedAlpha:
      return "unsupported-alpha";
    case UxrPixelResult::kUnsupportedRegion:
      return "unsupported-region";
  }
  return "invalid-result";
}

}  // namespace base
