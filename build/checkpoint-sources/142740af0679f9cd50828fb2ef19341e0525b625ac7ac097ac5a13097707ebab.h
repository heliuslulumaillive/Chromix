/*
 * Copyright (C) 2006 Nikolas Zimmermann <zimmermann@kde.org>
 * Copyright (C) 2007, 2008, 2009 Apple Inc. All rights reserved.
 * Copyright (C) 2010 Torch Mobile (Beijing) Co. Ltd. All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY APPLE COMPUTER, INC. ``AS IS'' AND ANY
 * EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
 * PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL APPLE COMPUTER, INC. OR
 * CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
 * EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
 * PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
 * PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY
 * OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_GRAPHICS_IMAGE_DATA_BUFFER_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_GRAPHICS_IMAGE_DATA_BUFFER_H_

#include <memory>
#include <vector>

#include "base/containers/span.h"
#include "base/memory/scoped_refptr.h"
#include "third_party/blink/renderer/platform/graphics/canvas_fingerprint_context.h"
#include "third_party/blink/renderer/platform/graphics/static_bitmap_image.h"
#include "third_party/blink/renderer/platform/image-encoders/image_encoder.h"
#include "third_party/blink/renderer/platform/platform_export.h"
#include "third_party/blink/renderer/platform/wtf/allocator/allocator.h"
#include "third_party/blink/renderer/platform/wtf/text/wtf_string.h"
#include "third_party/blink/renderer/platform/wtf/vector.h"
#include "third_party/skia/include/core/SkPixmap.h"
#include "third_party/skia/include/core/SkRefCnt.h"

namespace blink {

// Sparse, copy-on-write tiles of application input in native backing format.
// Comparing the actual native read with these values preserves untouched
// input even when a later glyph or shape overlaps its bounding rectangle.
class PLATFORM_EXPORT UxrCanvasInputPixels {
 public:
  explicit UxrCanvasInputPixels(const SkImageInfo& backing_info);
  UxrCanvasInputPixels(const UxrCanvasInputPixels&);
  ~UxrCanvasInputPixels();
  bool HasSize(int width, int height) const;
  bool Write(const SkPixmap& source, int x, int y);
  std::vector<uint8_t> PreserveMask(const SkPixmap& native_readback,
                                    int x, int y) const;

 private:
  struct Storage;
  std::unique_ptr<Storage> storage_;
};

class PLATFORM_EXPORT ImageDataBuffer {
  USING_FAST_MALLOC(ImageDataBuffer);

 public:
  static std::unique_ptr<ImageDataBuffer> Create(
      scoped_refptr<StaticBitmapImage>,
      UxrCanvasExportContext = {});
  static std::unique_ptr<ImageDataBuffer> Create(
      const SkPixmap&, UxrCanvasExportContext = {});

  String ToDataURL(const ImageEncodingMimeType mime_type,
                   const double& quality) const;
  bool EncodeImage(const ImageEncodingMimeType mime_type,
                   const double& quality,
                   Vector<unsigned char>* encoded_image) const;

  base::span<const uint8_t> PixelData() const;
  const SkPixmap& GetPixmap() const { return pixmap_; }

 private:
  ImageDataBuffer(const SkPixmap&, const UxrCanvasExportContext&);
  ImageDataBuffer(scoped_refptr<StaticBitmapImage>,
                  const UxrCanvasExportContext&);

  bool IsValid() { return is_valid_; }  // Only used by Create()

  sk_sp<SkImage> retained_image_;
  SkPixmap pixmap_;
  bool is_valid_ = false;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_GRAPHICS_IMAGE_DATA_BUFFER_H_
