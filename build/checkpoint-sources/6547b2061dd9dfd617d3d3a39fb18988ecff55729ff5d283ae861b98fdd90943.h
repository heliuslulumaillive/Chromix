// Copyright 2026. Export routing owns immutable values, never DOM/GC pointers.
#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_GRAPHICS_CANVAS_FINGERPRINT_CONTEXT_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_GRAPHICS_CANVAS_FINGERPRINT_CONTEXT_H_

#include <memory>
#include "base/uxr_surface_modes.h"

namespace blink {

class UxrCanvasInputPixels;

enum class UxrCanvasExportSource { kOther, kCanvas2D, kWebGL };

struct UxrCanvasExportContext {
  UxrCanvasExportSource source = UxrCanvasExportSource::kOther;
  base::UxrSurfacePolicy policy;
  bool noise_disabled = false;
  bool offscreen = false;
  bool webgl_premultiplied = false;
  std::shared_ptr<const UxrCanvasInputPixels> inputs;

  static UxrCanvasExportContext FromModes(const base::UxrSurfaceModes& modes,
                                          UxrCanvasExportSource source,
                                          bool offscreen,
                                          bool webgl_premultiplied = false) {
    UxrCanvasExportContext result;
    result.source = source;
    result.offscreen = offscreen;
    result.webgl_premultiplied = webgl_premultiplied;
    result.noise_disabled = modes.noise_disabled || modes.raw;
    if (source == UxrCanvasExportSource::kCanvas2D)
      result.policy = modes.canvas;
    else if (source == UxrCanvasExportSource::kWebGL)
      result.policy = modes.webgl;
    return result;
  }
};

}  // namespace blink

#endif
