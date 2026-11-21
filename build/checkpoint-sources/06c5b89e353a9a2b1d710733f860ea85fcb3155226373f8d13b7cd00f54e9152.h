// Copyright 2026 The ShardX Authors.
#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_DOM_UXR_RECT_POLICY_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_DOM_UXR_RECT_POLICY_H_

#include "base/uxr_surface_math.h"
#include "base/uxr_surface_modes.h"
#include "ui/gfx/geometry/rect_f.h"

namespace blink {

inline void ApplyUxrRectSize(gfx::RectF& rect,
                             const base::UxrSurfacePolicy& policy) {
  if (policy.mode != base::UxrSurfaceMode::kShardX ||
      (rect.width() == 0 && rect.height() == 0))
    return;
  const auto size = base::uxr_surface_v1::RectSize(
      rect.width(), rect.height(), static_cast<uint32_t>(policy.seed),
      policy.amount);
  rect.set_width(size.first);
  rect.set_height(size.second);
}

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_DOM_UXR_RECT_POLICY_H_
