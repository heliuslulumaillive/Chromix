/*
 * Copyright (c) 2008, Google Inc. All rights reserved.
 * Copyright (C) 2009 Dirk Schulze <krit@webkit.org>
 * Copyright (C) 2010 Torch Mobile (Beijing) Co. Ltd. All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met:
 *
 *     * Redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer.
 *     * Redistributions in binary form must reproduce the above
 * copyright notice, this list of conditions and the following disclaimer
 * in the documentation and/or other materials provided with the
 * distribution.
 *     * Neither the name of Google Inc. nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#include "third_party/blink/renderer/platform/graphics/image_data_buffer.h"

#include <algorithm>
#include <array>
#include <unordered_map>

#include "base/compiler_specific.h"
#include "base/logging.h"
#include "base/memory/ptr_util.h"
#include "base/strings/string_number_conversions.h"
#include "base/uxr_config.h"
#include "base/uxr_graphics_noise.h"
#include "base/uxr_pixel_noise.h"
#include "third_party/blink/renderer/platform/image-encoders/image_encoder_utils.h"
#include "third_party/blink/renderer/platform/runtime_enabled_features.h"
#include "third_party/blink/renderer/platform/wtf/text/base64.h"
#include "third_party/blink/renderer/platform/wtf/text/strcat.h"
#include "third_party/skia/include/core/SkImage.h"
#include "third_party/skia/include/core/SkBitmap.h"
#include "third_party/skia/include/core/SkSurface.h"
#include "ui/gfx/hdr_metadata.h"
#include "ui/gfx/skia_span_util.h"

namespace blink {

struct UxrCanvasInputPixels::Storage {
  static constexpr int kTileSize = 64;
  struct Tile {
    SkBitmap pixels;
    std::array<uint64_t, kTileSize> written{};
  };
  SkImageInfo backing_info;
  std::unordered_map<uint64_t, std::shared_ptr<Tile>> tiles;
};

UxrCanvasInputPixels::UxrCanvasInputPixels(const SkImageInfo& backing_info)
    : storage_(std::make_unique<Storage>()) {
  storage_->backing_info = backing_info;
}

UxrCanvasInputPixels::UxrCanvasInputPixels(const UxrCanvasInputPixels& other)
    : storage_(std::make_unique<Storage>(*other.storage_)) {}

UxrCanvasInputPixels::~UxrCanvasInputPixels() = default;

bool UxrCanvasInputPixels::HasSize(int width, int height) const {
  return storage_->backing_info.width() == width &&
         storage_->backing_info.height() == height;
}

bool UxrCanvasInputPixels::Write(const SkPixmap& source, int x, int y) {
  if (!source.addr() || x < 0 || y < 0 || source.width() <= 0 ||
      source.height() <= 0 ||
      int64_t{x} + source.width() > storage_->backing_info.width() ||
      int64_t{y} + source.height() > storage_->backing_info.height()) {
    return false;
  }
  constexpr int size = Storage::kTileSize;
  const int right = x + source.width(), bottom = y + source.height();
  for (int ty = y / size; ty <= (bottom - 1) / size; ++ty) {
    for (int tx = x / size; tx <= (right - 1) / size; ++tx) {
      const int left = tx * size, top = ty * size;
      const uint64_t key = (uint64_t{static_cast<uint32_t>(ty)} << 32) |
                           static_cast<uint32_t>(tx);
      auto& slot = storage_->tiles[key];
      if (!slot || slot.use_count() > 1) {
        auto tile = std::make_shared<Storage::Tile>();
        const SkImageInfo info = storage_->backing_info.makeWH(
            std::min(size, storage_->backing_info.width() - left),
            std::min(size, storage_->backing_info.height() - top));
        if (!tile->pixels.tryAllocPixels(info))
          return false;
        tile->pixels.eraseColor(SK_ColorTRANSPARENT);
        if (slot) {
          if (!slot->pixels.readPixels(tile->pixels.pixmap(), 0, 0))
            return false;
          tile->written = slot->written;
        }
        slot = std::move(tile);
      }
      if (!slot->pixels.writePixels(source, x - left, y - top))
        return false;
      for (int py = std::max(y, top); py < std::min(bottom, top + size); ++py) {
        for (int px = std::max(x, left); px < std::min(right, left + size); ++px)
          slot->written[py - top] |= uint64_t{1} << (px - left);
      }
    }
  }
  return true;
}

std::vector<uint8_t> UxrCanvasInputPixels::PreserveMask(
    const SkPixmap& native_readback, int x, int y) const {
  std::vector<uint8_t> mask;
  if (!native_readback.addr() || native_readback.width() <= 0 ||
      native_readback.height() <= 0 ||
      (native_readback.colorType() != kRGBA_8888_SkColorType &&
       native_readback.colorType() != kBGRA_8888_SkColorType)) {
    return mask;
  }
  const int64_t right = int64_t{x} + native_readback.width();
  const int64_t bottom = int64_t{y} + native_readback.height();
  constexpr int size = Storage::kTileSize;
  for (const auto& [key, tile] : storage_->tiles) {
    const int left = static_cast<int>(static_cast<uint32_t>(key)) * size;
    const int top = static_cast<int>(static_cast<uint32_t>(key >> 32)) * size;
    const int64_t l = std::max<int64_t>(x, left);
    const int64_t t = std::max<int64_t>(y, top);
    const int64_t r = std::min<int64_t>(right, left + tile->pixels.width());
    const int64_t b = std::min<int64_t>(bottom, top + tile->pixels.height());
    if (l >= r || t >= b)
      continue;
    SkBitmap expected;
    if (!expected.tryAllocPixels(native_readback.info().makeWH(
            tile->pixels.width(), tile->pixels.height())) ||
        !tile->pixels.readPixels(expected.pixmap(), 0, 0)) {
      continue;
    }
    for (int64_t py = t; py < b; ++py) {
      for (int64_t px = l; px < r; ++px) {
        const int tx = static_cast<int>(px - left);
        const int ty = static_cast<int>(py - top);
        if (!(tile->written[ty] & (uint64_t{1} << tx)) ||
            *expected.getAddr32(tx, ty) !=
                *native_readback.addr32(static_cast<int>(px - x),
                                        static_cast<int>(py - y))) {
          continue;
        }
        if (mask.empty()) {
          mask.resize((static_cast<size_t>(native_readback.width()) *
                           native_readback.height() + 7) / 8);
        }
        const size_t index = static_cast<size_t>(py - y) *
                                 native_readback.width() + (px - x);
        mask[index / 8] |= 1u << (index % 8);
      }
    }
  }
  return mask;
}

namespace {
bool UxrCopyAndNoiseEncodeBuffer(
    SkPixmap& pm,
    sk_sp<SkImage>& retained_image,
    const UxrCanvasExportContext& export_context) {
  if (!pm.addr() || pm.info().isEmpty() ||
      !pm.info().validRowBytes(pm.rowBytes()) ||
      SkImageInfo::ByteSizeOverflowed(pm.computeByteSize())) {
    return false;
  }
  const auto& config = base::UxrConfig::GetInstance();
  if (export_context.policy.IsConfigured()) {
    const SkColorType color_type = pm.colorType();
    if (export_context.policy.IsNative() || export_context.noise_disabled ||
        (color_type != kRGBA_8888_SkColorType &&
         color_type != kBGRA_8888_SkColorType)) {
      return true;
    }
    auto data = gfx::MakeSkDataFromSpanWithCopy(gfx::SkPixmapToSpan(pm));
    if (!data)
      return false;
    const SkImageInfo info = pm.info().makeAlphaType(kUnpremul_SkAlphaType);
    SkPixmap copy(info, data->writable_data(), pm.rowBytes());
    if (pm.info().alphaType() != kUnpremul_SkAlphaType &&
        !pm.readPixels(info, copy.writable_addr(), copy.rowBytes())) {
      return false;
    }
    base::UxrPixelRegion region{
        gfx::SkPixmapToWritableSpan(copy), copy.rowBytes(),
        static_cast<uint32_t>(copy.width()),
        static_cast<uint32_t>(copy.height()),
        static_cast<uint32_t>(copy.width()),
        static_cast<uint32_t>(copy.height()), 0, 0,
        color_type == kBGRA_8888_SkColorType,
        export_context.webgl_premultiplied};
    const auto preserved = export_context.inputs
        ? export_context.inputs->PreserveMask(copy, 0, 0)
        : std::vector<uint8_t>();
    region.preserved_inputs = preserved;
    if (export_context.policy.mode == base::UxrSurfaceMode::kShardX)
      region.neighborhood = {region.bytes, region.row_stride, region.width,
                             region.height, 0, 0};
    const auto result = base::UxrApplyGraphicsNoiseV1(
        export_context.policy,
        export_context.source == UxrCanvasExportSource::kWebGL
            ? base::UxrPixelSurface::kWebGLExport
            : base::UxrPixelSurface::kCanvas2D,
        region);
    // Unchanged and unsupported paths preserve the original encoder input.
    // Coverage/fallbacks are published by the v1 capability manifest.
    if (result != base::UxrPixelResult::kApplied)
      return true;
    auto image = SkImages::RasterFromData(copy.info(), std::move(data),
                                          copy.rowBytes());
    if (!image)
      return false;
    retained_image = std::move(image);
    pm = copy;
    return true;
  }
  const auto seed_text = config.Get("uxr-canvas-seed");
  uint64_t seed64 = 0u;
  const SkColorType ct = pm.colorType();
  const bool apply_noise =
      config.GpuBackendPolicy().canvas_pixel_noise && !seed_text.empty() &&
      seed_text.find_first_not_of("0123456789") == std::string::npos &&
      base::StringToUint64(seed_text, &seed64) && seed64 != 0u &&
      (ct == kRGBA_8888_SkColorType || ct == kBGRA_8888_SkColorType);
  const uint32_t seed = base::UxrPixelNoiseSeed(seed64);
  const bool normalize_alpha = pm.info().alphaType() != kUnpremul_SkAlphaType;
  // Disabled noise must preserve the native pixmap/encoder alpha path.
  if (!apply_noise)
    return true;
  auto data = gfx::MakeSkDataFromSpanWithCopy(gfx::SkPixmapToSpan(pm));
  if (!data)
    return false;
  const SkImageInfo info =
      normalize_alpha ? pm.info().makeAlphaType(kUnpremul_SkAlphaType) : pm.info();
  SkPixmap copy(info, data->writable_data(), pm.rowBytes());
  if (normalize_alpha &&
      !pm.readPixels(info, copy.writable_addr(), copy.rowBytes())) {
    return false;
  }
  auto pixels = gfx::SkPixmapToWritableSpan(copy);
  const auto preserved = export_context.inputs
      ? export_context.inputs->PreserveMask(copy, 0, 0)
      : std::vector<uint8_t>();
  for (int y = 0; apply_noise && y < copy.height(); ++y) {
    auto row = pixels.subspan(static_cast<size_t>(y) * copy.rowBytes(),
                              copy.info().minRowBytes());
    for (int x = 0; x < copy.width(); ++x) {
      if (base::UxrPreservesInputPixel(preserved, copy.width(), x, y))
        continue;
      auto pixel = row.subspan(static_cast<size_t>(x) * 4, size_t{4});
      if (pixel[3] == 0)
        continue;
      for (uint32_t ch = 0; ch < 3; ++ch) {
        const size_t index = ct == kBGRA_8888_SkColorType ? 2 - ch : ch;
        pixel[index] = base::UxrNoiseChannel(
            seed, static_cast<uint32_t>(x), static_cast<uint32_t>(y), ch,
            pixel[index]);
      }
    }
  }
  auto image = SkImages::RasterFromData(copy.info(), std::move(data),
                                       copy.rowBytes());
  if (!image)
    return false;
  // RasterFromData retains the private pixel storage backing the pixmap.
  retained_image = std::move(image);
  pm = copy;
  return true;
}
}  // namespace

ImageDataBuffer::ImageDataBuffer(
    scoped_refptr<StaticBitmapImage> image,
    const UxrCanvasExportContext& export_context) {
  if (!image)
    return;
  PaintImage paint_image = image->PaintImageForCurrentFrame();
  if (!paint_image || paint_image.IsPaintWorklet())
    return;

  SkImageInfo paint_image_info = paint_image.GetSkImageInfo();
  if (paint_image_info.isEmpty())
    return;

#if defined(MEMORY_SANITIZER)
  // Test if software SKImage has an initialized pixmap.
  SkPixmap pixmap;
  if (!paint_image.IsTextureBacked() &&
      paint_image.GetSwSkImage()->peekPixels(&pixmap)) {
    MSAN_CHECK_MEM_IS_INITIALIZED(pixmap.addr(), pixmap.computeByteSize());
  }
#endif

  const SkColorInfo target_color_info =
      ImageEncoderUtils::GetColorInfoForEncoder(paint_image_info.colorInfo(),
                                                image->GetHdrMetadata());

  if (target_color_info != paint_image_info.colorInfo() ||
      paint_image.IsTextureBacked() || paint_image.IsLazyGenerated()) {
    // Unpremul is handled upfront, using readPixels, which will correctly clamp
    // premul color values that would otherwise cause overflows in the skia
    // encoder unpremul logic.
    const SkImageInfo info =
        SkImageInfo::Make(paint_image_info.dimensions(), target_color_info);
    const size_t row_bytes = info.minRowBytes();
    size_t size = info.computeByteSize(row_bytes);
    if (SkImageInfo::ByteSizeOverflowed(size))
      return;

    sk_sp<SkData> data = SkData::MakeUninitialized(size);
    if (!data)
      return;
    pixmap_ = {info, data->writable_data(), info.minRowBytes()};
    if (!paint_image.readPixels(info, pixmap_.writable_addr(), row_bytes, 0,
                                0)) {
      pixmap_.reset();
      return;
    }
    MSAN_CHECK_MEM_IS_INITIALIZED(pixmap_.addr(), pixmap_.computeByteSize());
    retained_image_ =
        SkImages::RasterFromData(info, std::move(data), row_bytes);
  } else {
    retained_image_ = paint_image.GetSwSkImage();
    if (!retained_image_->peekPixels(&pixmap_))
      return;
    MSAN_CHECK_MEM_IS_INITIALIZED(pixmap_.addr(), pixmap_.computeByteSize());
  }
  if (!retained_image_ ||
      !UxrCopyAndNoiseEncodeBuffer(pixmap_, retained_image_, export_context)) {
    pixmap_.reset();
    return;
  }
  is_valid_ = true;
}

ImageDataBuffer::ImageDataBuffer(
    const SkPixmap& pixmap,
    const UxrCanvasExportContext& export_context)
    : pixmap_(pixmap),
      is_valid_(pixmap_.addr() &&
                !gfx::Size(pixmap.width(), pixmap.height()).IsEmpty()) {
  if (is_valid_ &&
      !UxrCopyAndNoiseEncodeBuffer(pixmap_, retained_image_, export_context)) {
    pixmap_.reset();
    is_valid_ = false;
    return;
  }
  // Any color space or alpha conversion should have been performed by the
  // caller higher up in the stack (e.g. in CanvasAsyncBlobCreator).
  if (ImageEncoderUtils::GetColorInfoForEncoder(pixmap_.info().colorInfo(),
                                                gfx::HDRMetadata()) !=
      pixmap_.info().colorInfo()) {
    DLOG(WARNING) << "Encoding image without prior color space or alpha "
                     "conversion; precision or colors may be lost.";
  }
}

std::unique_ptr<ImageDataBuffer> ImageDataBuffer::Create(
    scoped_refptr<StaticBitmapImage> image,
    UxrCanvasExportContext export_context) {
  std::unique_ptr<ImageDataBuffer> buffer =
      base::WrapUnique(new ImageDataBuffer(image, export_context));
  if (!buffer->IsValid())
    return nullptr;
  return buffer;
}

std::unique_ptr<ImageDataBuffer> ImageDataBuffer::Create(
    const SkPixmap& pixmap,
    UxrCanvasExportContext export_context) {
  std::unique_ptr<ImageDataBuffer> buffer =
      base::WrapUnique(new ImageDataBuffer(pixmap, export_context));
  if (!buffer->IsValid())
    return nullptr;
  return buffer;
}

base::span<const uint8_t> ImageDataBuffer::PixelData() const {
  DCHECK(is_valid_);
  return gfx::SkPixmapToSpan(pixmap_);
}

bool ImageDataBuffer::EncodeImage(const ImageEncodingMimeType mime_type,
                                  const double& quality,
                                  Vector<unsigned char>* encoded_image) const {
  return ImageEncoder::Encode(encoded_image, pixmap_, mime_type, quality);
}

String ImageDataBuffer::ToDataURL(const ImageEncodingMimeType mime_type,
                                  const double& quality) const {
  DCHECK(is_valid_);
  Vector<unsigned char> result;
  if (!ImageEncoder::Encode(&result, pixmap_, mime_type, quality)) {
    return "data:,";
  }
  return StrCat({"data:", ImageEncoderUtils::MimeTypeName(mime_type),
                 ";base64,", Base64Encode(result)});
}

}  // namespace blink
