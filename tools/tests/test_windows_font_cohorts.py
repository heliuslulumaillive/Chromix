"""Compile the actual Windows policy and retain historical surface goldens."""
import json
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

    def test_documented_base_fonts_and_bounded_atomic_cohorts(self):
        catalog = json.loads((ROOT / 'tools/tests/fixtures/windows-font-cohorts-v1.json').read_text(encoding='utf-8'))
        core = ',\n'.join(json.dumps(s, ensure_ascii=False) for s in catalog['groups'][0]['names'])
        source = r'''
#include "base/uxr_surface_reference_data.h"
#include <cassert>
#include <array>
#include <set>
#include <string>
using namespace base::uxr_surface_v1;
int main() {
 const std::string_view documented_core[] = { CORE_NAMES };
 std::set<std::array<bool,3>> observed;
 for (uint32_t seed=1;seed<=512;++seed) {
  for (const auto name:documented_core)
   assert(!ShardWindowsFontHiddenV1(name,seed));
  assert(!ShardWindowsFontHiddenV1("Segoe UI Light",seed));
  assert(!ShardWindowsFontHiddenV1("segoe ui light",seed));
  const auto optional=ShardWindowsFontHiddenV1("Meiryo",seed);
  assert(optional==ShardWindowsFontHiddenV1("MS Mincho",seed));
  assert(optional==ShardWindowsFontHiddenV1("Batang",seed));
  assert(optional==ShardWindowsFontHiddenV1("Dotum",seed));
  assert(optional==ShardWindowsFontHiddenV1("SimHei",seed));
  assert(optional==ShardWindowsFontHiddenV1("Microsoft Uighur",seed));
  const auto applications=ShardWindowsFontHiddenV1("Agency FB",seed);
  assert(applications==ShardWindowsFontHiddenV1("Lucida Bright",seed));
  assert(applications==ShardWindowsFontHiddenV1("My Installed Custom Font",seed));
  observed.insert({false,optional,applications});
 }
 assert(observed.size()==4);
 for (const auto name:{"Segoe UI Light","Meiryo","Agency FB"})
  assert(!ShardWindowsFontHiddenV1(name,0));
 // The recorded regression seed loses only its illegal core-font deletion.
 assert(!ShardWindowsFontHiddenV1("Segoe UI Light",uint32_t(3329572536739077236ULL)));
 assert(!ShardWindowsFontHiddenV1("",1));
}
'''.replace('CORE_NAMES', core)
        self.build_and_run('windows-font-cohorts', source)

    def test_existing_surface_expectations_remain_frozen(self):
        patch = ROOT / 'patches/lite-windows157/19-shared-policy.patch'
        section = next(data for name, created, data in sections(patch.read_text(encoding='utf-8'))
                       if name == 'base/uxr_surface_golden_tests.cc' and created)
        source = ''.join(line[1:] for line in section.splitlines(keepends=True)
                         if line.startswith('+') and not line.startswith('+++'))
        self.build_and_run('surface-goldens-with-windows-cohorts', source + '\nint main(){return RunSurfaceGoldenTests();}\n')

    def test_windows_path_uses_cohorts_without_changing_other_platforms(self):
        patch = (ROOT / 'patches/lite-windows157/13-fonts.patch').read_text(encoding='utf-8')
        self.assertIn('+#if BUILDFLAG(IS_WIN)\n+    return !base::uxr_surface_v1::ShardWindowsFontHiddenV1(', patch)
        self.assertIn('+#else\n+    return !base::uxr_surface_v1::ShardFontHidden(', patch)
        # The native and noise-disabled branches remain outside the policy.
        self.assertIn('!modes.noise_disabled && !modes.raw', patch)


if __name__ == '__main__':
    unittest.main()
