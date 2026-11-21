"""Compile the actual Windows policy and retain historical surface goldens."""
from pathlib import Path
import unittest

from tools.tests import test_canvas_input_mask as host
from tools.build_google_services_patch import sections
ROOT = host.ROOT


class WindowsFontCohortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the existing verified host C++ setup and actual product-header
        # extraction. No mocked font-policy implementation is tested here.
        host.CanvasInputMaskTests.setUpClass.__func__(cls)

    build_and_run = host.CanvasInputMaskTests.build_and_run

    def test_blink_uses_the_upstream_font_collection(self):
        from tools.lite_patch_stack import load
        manifest = load(ROOT)
        rows = {row['path']: row for row in manifest['source_files']}
        for filename in ('third_party/blink/renderer/modules/font_access/font_access.cc',
                         'third_party/blink/renderer/platform/fonts/font_cache.cc',
                         'third_party/blink/renderer/platform/fonts/font_cache.h'):
            self.assertIsNotNone(rows[filename]['before_sha256'])
            self.assertEqual(rows[filename]['before_sha256'], rows[filename]['after_sha256'])
        for name in ('base/uxr_font_allowlist.h', 'base/uxr_surface_reference_data.h'):
            self.assertIsNone(rows[name]['after_sha256'])

    def test_existing_surface_expectations_remain_frozen(self):
        patch = ROOT / 'patches/lite-windows157/19-shared-policy.patch'
        section = next(data for name, created, data in sections(patch.read_text(encoding='utf-8'))
                       if name == 'base/uxr_surface_golden_tests.cc' and created)
        source = ''.join(line[1:] for line in section.splitlines(keepends=True)
                         if line.startswith('+') and not line.startswith('+++'))
        self.build_and_run('surface-goldens-with-windows-cohorts', source + '\nint main(){return RunSurfaceGoldenTests();}\n')

    def test_native_fallback_and_signed_sources_have_distinct_entry_points(self):
        patch = (ROOT / 'patches/lite-windows157/13-fonts.patch').read_text(encoding='utf-8')
        for fragment in ('GetPrivateFontCollection', 'LoadCountryCatalog(catalog, group, &selection)',
                         'VerifyCatalogAssets(', 'CreateControlledCollection(', 'selection->face_index.get()'):
            self.assertIn(fragment, patch)
        self.assertNotIn('uxr-fonts-experimental-packages', patch)
        self.assertNotIn('Verify(root, &resources)', patch)


if __name__ == '__main__':
    unittest.main()
