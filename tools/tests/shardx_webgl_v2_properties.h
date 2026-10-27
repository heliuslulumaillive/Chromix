// Observable invariants for the production helper, not an algorithm copy.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <limits>
#include <utility>
#include <vector>

inline std::pair<size_t, size_t> RunShardWebGLV2Properties() {
  size_t checks = 0, failures = 0;
  auto check = [&](bool condition) { ++checks; failures += !condition; };
  for (uint32_t seed : {1u, 42u, 314159u, 32452843u, 49979687u, 0xffffffffu}) {
    base::UxrSurfacePolicy policy;
    policy.mode = base::UxrSurfaceMode::kShardX;
    policy.seed = seed;
    for (const auto [width, height] : {std::pair{128u, 96u}, {257u, 25u}, {64u, 64u}}) {
      for (bool partial : {false, true}) {
        std::vector<uint8_t> original(width * height * 4);
        for (uint32_t y = 0; y < height; ++y) for (uint32_t x = 0; x < width; ++x) {
          const size_t at = (y * width + x) * 4;
          original[at] = (x * 7 + y * 13) % 256;
          original[at + 1] = (x * 3 + y * 5) % 256;
          original[at + 2] = (x * 11 + y * 17) % 256;
          original[at + 3] = partial ? uint8_t((x + y) % 5 * 63) : 255;
        }
        auto apply = [&](std::vector<uint8_t>& pixels, uint32_t w, uint32_t h,
                         int32_t x, int32_t y, size_t stride,
                         base::UxrPixelSurface surface = base::UxrPixelSurface::kWebGLReadback,
                         bool bgra = false) {
          base::UxrPixelRegion region{pixels, stride, w, h, width, height, x, y, bgra};
          return base::UxrApplyGraphicsNoiseV1(policy, surface, region);
        };
        auto full = original;
        apply(full, width, height, 0, 0, width * 4);
        check(full != original);
        auto twice = full;
        apply(twice, width, height, 0, 0, width * 4);
        check(twice == full);
        auto repeat = original;
        apply(repeat, width, height, 0, 0, width * 4);
        check(repeat == full);
        for (size_t i = 0; i < full.size(); ++i) {
          check(i % 4 == 3 ? full[i] == original[i] :
                std::abs(int(full[i]) - int(original[i])) <= 1);
          if (original[(i / 4) * 4 + 3] == 0) check(full[i] == original[i]);
          if (i % 4 != 3 && (original[i] == 0 || original[i] == 255))
            check(full[i] == original[i]);
        }
        // Arbitrary tiles with sentinel stride padding must compose exactly.
        auto tiles = original;
        for (uint32_t y = 0; y < height; y += 19) for (uint32_t x = 0; x < width; x += 23) {
          const uint32_t w = std::min(23u, width - x), h = std::min(19u, height - y);
          const size_t stride = w * 4 + 13;
          std::vector<uint8_t> tile(stride * h, 173);
          for (uint32_t yy = 0; yy < h; ++yy)
            std::copy_n(original.begin() + ((y + yy) * width + x) * 4, w * 4,
                        tile.begin() + yy * stride);
          apply(tile, w, h, x, y, stride);
          for (uint32_t yy = 0; yy < h; ++yy) {
            std::copy_n(tile.begin() + yy * stride, w * 4,
                        tiles.begin() + ((y + yy) * width + x) * 4);
            check(std::all_of(tile.begin() + yy * stride + w * 4,
                              tile.begin() + (yy + 1) * stride,
                              [](uint8_t v) { return v == 173; }));
          }
        }
        check(tiles == full);
        // Exports use top-left order, optionally BGRA storage.
        for (bool bgra : {false, true}) {
          std::vector<uint8_t> exported(original.size());
          for (uint32_t y = 0; y < height; ++y) for (uint32_t x = 0; x < width; ++x)
            for (uint32_t c = 0; c < 4; ++c)
              exported[(y * width + x) * 4 + (bgra && c < 3 ? 2 - c : c)] =
                  original[((height - 1 - y) * width + x) * 4 + c];
          apply(exported, width, height, 0, 0, width * 4,
                base::UxrPixelSurface::kWebGLExport, bgra);
          bool same = true;
          for (uint32_t y = 0; y < height; ++y) for (uint32_t x = 0; x < width; ++x)
            for (uint32_t c = 0; c < 4; ++c)
              same &= exported[(y * width + x) * 4 + (bgra && c < 3 ? 2 - c : c)] ==
                  full[((height - 1 - y) * width + x) * 4 + c];
          check(same);
        }
        auto other = original;
        policy.seed = seed == 0xffffffffu ? 2u : seed + 1;
        apply(other, width, height, 0, 0, width * 4);
        check(other != full);
        policy.seed = seed;
      }
    }
  }
  base::UxrSurfacePolicy policy;
  policy.mode = base::UxrSurfaceMode::kShardX;
  policy.seed = 32452843;
  // Boundary-only colors must remain exact across sizes, alpha conventions
  // and read/export orientation. This is a color invariant, not a canvas-size
  // exemption; the preceding interior-valued scenes still require changes.
  for (const auto [w, h] : {std::pair{17u, 19u}, {128u, 96u}, {301u, 151u}}) {
    for (bool premultiplied : {false, true}) {
      for (uint32_t alpha : {0u, 1u, 2u, 64u, 128u, 255u}) {
        std::vector<uint8_t> raw(w * h * 4), exported(raw.size());
        const uint32_t maximum = premultiplied ? alpha : 255;
        for (uint32_t y = 0; y < h; ++y) for (uint32_t x = 0; x < w; ++x) {
          for (uint32_t channel = 0; channel < 3; ++channel) {
            const bool high = ((x + y + channel) & 1u) != 0;
            raw[(y * w + x) * 4 + channel] = high ? maximum : 0;
            exported[((h - 1 - y) * w + x) * 4 + channel] =
                high && (!premultiplied || alpha) ? 255 : 0;
          }
          raw[(y * w + x) * 4 + 3] = alpha;
          exported[((h - 1 - y) * w + x) * 4 + 3] = alpha;
        }
        const auto original_raw = raw, original_export = exported;
        base::UxrPixelRegion raw_region{raw, w * 4, w, h, w, h,
                                         0, 0, false, premultiplied};
        base::UxrPixelRegion export_region{exported, w * 4, w, h, w, h,
                                            0, 0, false, premultiplied};
        check(base::UxrApplyGraphicsNoiseV1(policy,
            base::UxrPixelSurface::kWebGLReadback, raw_region) ==
            base::UxrPixelResult::kUnchanged);
        check(base::UxrApplyGraphicsNoiseV1(policy,
            base::UxrPixelSurface::kWebGLExport, export_region) ==
            base::UxrPixelResult::kUnchanged);
        check(raw == original_raw && exported == original_export);
      }
    }
  }
  const uint32_t width = 128, height = 96;
  // Valid premultiplied storage and its straight-alpha export must round trip
  // to the same changed raw bytes, including low-alpha quantization boundaries.
  auto premultiply = [](uint32_t c, uint32_t a) {
    const uint32_t product = c * a;
    return (product + ((product + 128u) >> 8) + 128u) >> 8;
  };
  auto unpremultiply = [](uint32_t c, uint32_t a) {
    return a ? (c * (0xff000000u / a) + 0x800000u) >> 24 : 0u;
  };
  for (uint32_t alpha : {1u, 2u, 7u, 63u, 127u, 128u, 192u, 254u, 255u}) {
    std::vector<uint8_t> raw(width * height * 4), exported(raw.size());
    for (uint32_t y = 0; y < height; ++y) for (uint32_t x = 0; x < width; ++x) {
      for (uint32_t c = 0; c < 3; ++c) {
        const uint8_t value = (x * 7 + y * 11 + c * 17) % (alpha + 1);
        raw[(y * width + x) * 4 + c] = value;
        exported[((height - 1 - y) * width + x) * 4 + c] =
            static_cast<uint8_t>(unpremultiply(value, alpha));
      }
      raw[(y * width + x) * 4 + 3] = alpha;
      exported[((height - 1 - y) * width + x) * 4 + 3] = alpha;
    }
    base::UxrPixelRegion raw_region{raw, width * 4, width, height, width, height,
                                     0, 0, false, true};
    base::UxrPixelRegion export_region{exported, width * 4, width, height,
                                        width, height, 0, 0, false, true};
    const auto native_raw = raw;
    base::UxrApplyGraphicsNoiseV1(policy, base::UxrPixelSurface::kWebGLReadback,
                                   raw_region);
    base::UxrApplyGraphicsNoiseV1(policy, base::UxrPixelSurface::kWebGLExport,
                                   export_region);
    // Alpha 1 has only the two representable endpoints, with no interior
    // color on which a positive perturbation can be validly demonstrated.
    check(alpha == 1 ? raw == native_raw : raw != native_raw);
    for (size_t i = 0; i < raw.size(); ++i)
      if (i % 4 != 3 && (native_raw[i] == 0 || native_raw[i] == alpha))
        check(raw[i] == native_raw[i]);
    bool match = true;
    for (uint32_t y = 0; y < height; ++y) for (uint32_t x = 0; x < width; ++x)
      for (uint32_t c = 0; c < 3; ++c)
        match &= premultiply(exported[((height - 1 - y) * width + x) * 4 + c], alpha) ==
                     raw[(y * width + x) * 4 + c];
    check(match);
    const auto once = raw;
    base::UxrApplyGraphicsNoiseV1(policy, base::UxrPixelSurface::kWebGLReadback,
                                   raw_region);
    check(raw == once);
  }
  std::vector<uint8_t> floating(width * height * 16);
  for (size_t i = 0; i < floating.size(); i += 4) {
    const float v = std::array<float, 4>{.25f, .5f, .75f, 1.f}[(i / 4) % 4];
    std::memcpy(floating.data() + i, &v, 4);
  }
  auto changed = floating;
  base::UxrPixelRegion region{changed, width * 16, width, height, width, height};
  check(base::UxrApplyShardWebGLFloatV2(policy, region) == base::UxrPixelResult::kApplied);
  const auto once = changed;
  base::UxrApplyShardWebGLFloatV2(policy, region);
  check(changed == once);
  for (size_t i = 0; i < changed.size(); i += 4) {
    uint32_t a, b;
    std::memcpy(&a, changed.data() + i, 4);
    std::memcpy(&b, floating.data() + i, 4);
    check((i / 4) % 4 == 3 ? a == b : (a & ~1u) == (b & ~1u));
  }
  std::vector<uint8_t> crop(71 * 53 * 16);
  for (uint32_t y = 0; y < 53; ++y)
    std::copy_n(floating.begin() + ((11 + y) * width + 13) * 16, 71 * 16,
                crop.begin() + y * 71 * 16);
  base::UxrPixelRegion cropped{crop, 71 * 16, 71, 53, width, height, 13, 11};
  base::UxrApplyShardWebGLFloatV2(policy, cropped);
  for (uint32_t y = 0; y < 53; ++y)
    check(std::equal(crop.begin() + y * 71 * 16, crop.begin() + (y + 1) * 71 * 16,
                      changed.begin() + ((11 + y) * width + 13) * 16));
  return {checks, failures};
}
