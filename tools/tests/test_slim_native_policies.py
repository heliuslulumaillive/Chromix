"""Native defaults and the shared geometry helper used by Blink in Windows Lite."""
import unittest

from tools.tests import test_canvas_input_mask as host
from tools.build_google_services_patch import sections

ROOT = host.ROOT


def created_source(patch_name, path):
    patch = ROOT / 'patches/lite-windows158' / patch_name
    for name, created, body in sections(patch.read_text('utf-8')):
        if name == path and created:
            return ''.join(line[1:] for line in body.splitlines(keepends=True)
                           if line.startswith('+') and not line.startswith('+++'))
    raise AssertionError(path)


class SlimNativePolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        host.CanvasInputMaskTests.setUpClass.__func__(cls)

    build_and_run = host.CanvasInputMaskTests.build_and_run

    def test_native_disabled_and_empty_rectangles(self):
        name = 'third_party/blink/renderer/core/dom/uxr_rect_policy.h'
        dest = self.root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(created_source('21-web-platform-surfaces.patch', name), encoding='utf-8')
        rect = self.root / 'ui/gfx/geometry/rect_f.h'
        rect.parent.mkdir(parents=True, exist_ok=True)
        rect.write_text("""#pragma once
namespace gfx { struct RectF {
  float w, h;
  float width() const { return w; }
  float height() const { return h; }
  void set_width(float v) { w=v; }
  void set_height(float v) { h=v; }
}; }
""", encoding='utf-8')
        config = created_source('19-shared-policy.patch', 'base/uxr_config.cc')
        start = config.index('UxrSurfacePolicy UxrConfig::RectPolicy() const {')
        method = config[start:config.index('\n}', start)+2]
        source = """
#include <cassert>
#include "third_party/blink/renderer/core/dom/uxr_rect_policy.h"
namespace base {
struct AutoLock {
  explicit AutoLock(unsigned& reads) { ++reads; }
};
struct UxrConfig {
  mutable unsigned lock_=0;
  UxrSurfaceModes surface_modes_;
  UxrSurfacePolicy RectPolicy() const;
};
""" + method + """
}
int main() {
  using base::UxrSurfaceMode;
  base::UxrConfig config;
  assert(config.RectPolicy().IsLegacy());
  config.surface_modes_.rects = {UxrSurfaceMode::kShardX, 3, 1};
  for (int disabled=0; disabled<3; ++disabled) {
    config.surface_modes_.noise_disabled = disabled==1;
    config.surface_modes_.raw = disabled==2;
    const unsigned before = config.lock_;
    const auto policy = config.RectPolicy();
    assert(policy.IsNative()==bool(disabled));
    for (int i=0; i<512; ++i) {
      gfx::RectF empty{0,0}, rect{120,80};
      blink::ApplyUxrRectSize(empty, policy);
      blink::ApplyUxrRectSize(rect, policy);
      assert(empty.width()==0 && empty.height()==0);
      assert(disabled ? rect.width()==120 && rect.height()==80
                      : rect.width()!=120 || rect.height()!=80);
    }
    assert(config.lock_ == before+1);
  }
}
"""
        self.build_and_run('slim-rect-policy', source)

    def test_windows_speech_uses_the_pinned_upstream_backend(self):
        from tools.lite_patch_stack import load
        data = load(ROOT)
        files = {row['path']:row for row in data['source_files']}
        tts = files['content/browser/speech/tts_win.cc']
        self.assertIsNotNone(tts['before_sha256'])
        self.assertEqual(tts['before_sha256'], tts['after_sha256'])
        self.assertFalse(any(row['path'].endswith('17-speech.patch') for row in data['patches']))
        for name in ('base/uxr_voice_compat.h', 'base/uxr_voice_compat_tests.cc'):
            self.assertIsNone(files[name]['after_sha256'])

    def test_only_an_explicit_site_quota_changes_the_native_quota(self):
        inputs = (ROOT/'patches/lite-windows158/22-process-and-startup.patch').read_text('utf-8')
        self.assertNotIn('append_default("uxr-storage-quota"', inputs)
        self.assertRegex(inputs, r'\{"fingerprint-storage-quota",\s*"uxr-storage-quota"\}')
        changes = (ROOT/'patches/lite-windows158/31-storage-and-cookies.patch').read_text('utf-8')
        self.assertIn('if (const auto quota = FingerprintStorageQuotaBytes())', changes)
        self.assertIn('return FingerprintStorageQuotaBytes();', changes)
