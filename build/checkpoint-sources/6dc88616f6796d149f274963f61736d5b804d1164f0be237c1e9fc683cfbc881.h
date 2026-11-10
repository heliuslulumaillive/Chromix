// Copyright 2026. Independently reconstructed, versioned pixel algorithms.
#ifndef BASE_UXR_GRAPHICS_NOISE_H_
#define BASE_UXR_GRAPHICS_NOISE_H_

#include <cstddef>
#include <cstdint>
#include <string_view>

#include "base/base_export.h"
#include "base/containers/span.h"
#include "base/uxr_surface_modes.h"

namespace base {

inline constexpr uint32_t kUxrGraphicsAlgorithmVersion = 1;
inline constexpr bool kUxrCloakCanvasReferenceBankSupported = false;

enum class UxrPixelSurface { kCanvas2D, kWebGLReadback, kWebGLExport };
// Reference flavor exists for historical byte goldens, not production routing.
enum class UxrPixelFlavor { kCompatibleV1, kReferenceV1 };
enum class UxrPixelResult {
  kApplied,
  kUnchanged,
  kNative,
  kLegacy,
  kInvalidLayout,
  kUnsupportedSeed,
  kUnsupportedAlpha,
  kUnsupportedRegion,
};

// A successful read of the same surface, including the one-pixel halo needed
// at crop boundaries. It uses the output's component format and orientation.
// Comparisons ignore only bits assigned by noise, so aliasing the output is
// safe: earlier assignments cannot change which subsequent pixels are flat.
struct UxrPixelNeighborhood {
  span<const uint8_t> bytes;
  size_t row_stride = 0;
  uint32_t width = 0;
  uint32_t height = 0;
  int32_t origin_x = 0;
  int32_t origin_y = 0;
};

// An owned, completed RGBA8/BGRA8 readback. Padding is never a pixel.
// Canvas origin is top-left. WebGL readback origin is bottom-left.
struct UxrPixelRegion {
  span<uint8_t> bytes;
  size_t row_stride = 0;
  uint32_t width = 0;
  uint32_t height = 0;
  uint32_t canvas_width = 0;
  uint32_t canvas_height = 0;
  int32_t origin_x = 0;
  int32_t origin_y = 0;
  bool bgra = false;
  // WebGL default framebuffer storage convention. Exports arrive as straight
  // RGBA and use this to reconstruct the same representable storage lattice.
  // FBO data has no compositor alpha convention.
  bool premultiplied = false;
  UxrPixelNeighborhood neighborhood;
  // One bit per local pixel, captured from the completed native read before
  // noise. Set bits identify unchanged application-supplied pixels.
  span<const uint8_t> preserved_inputs;
};

inline bool UxrPreservesInputPixel(span<const uint8_t> mask,
                                   uint32_t width,
                                   uint32_t x,
                                   uint32_t y) {
  const uint64_t index = uint64_t{y} * width + x;
  return index / 8 < mask.size() &&
         (mask[static_cast<size_t>(index / 8)] & (1u << (index % 8)));
}

// The recovered byte hash includes signed 8-bit tail loads and uint32 wrap.
BASE_EXPORT uint32_t UxrCloakHashV1(std::string_view bytes);

// This function performs no allocation of image storage and no GL operations.
// Shard Canvas projects full-canvas positions into the requested crop.
// Compatible Cloak projects full-canvas points and preserves partial alpha;
// its reference flavor is restricted to the original verified full buffers.
BASE_EXPORT UxrPixelResult
UxrApplyGraphicsNoiseV1(const UxrSurfacePolicy& policy,
                        UxrPixelSurface surface,
                        const UxrPixelRegion& region,
                        uint32_t shard_webgl_sequence = 0,
                        UxrPixelFlavor flavor = UxrPixelFlavor::kCompatibleV1);

// RGBA32F readback uses the same coordinate mask with idempotent mantissa
// assignments; this is not an 8-bit quantizer and preserves nonfinite/zero data.
BASE_EXPORT UxrPixelResult UxrApplyShardWebGLFloatV2(
    const UxrSurfacePolicy& policy, const UxrPixelRegion& region);

BASE_EXPORT const char* UxrPixelResultName(UxrPixelResult result);

}  // namespace base

#endif  // BASE_UXR_GRAPHICS_NOISE_H_
