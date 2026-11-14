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

    def test_package_policy_preserves_complete_system_and_atomic_apps(self):
        catalog = json.loads((ROOT / 'tools/tests/fixtures/windows-font-packages-v2.json').read_text(encoding='utf-8'))
        core = ',\n'.join(json.dumps(s, ensure_ascii=False) for s in catalog['core_names'])
        source = r'''
#include "base/uxr_surface_reference_data.h"
#include <cassert>
#include <set>
using namespace base::uxr_surface_v1;
int main() {
 const std::string_view system[] = { SYSTEM_NAMES };
 const uint32_t masks[] = {0,1,3,5,7,9,11,13,15,2,4,6};
 std::set<uint32_t> profiles;
 for (uint32_t p=0;p<12;++p) {
  const uint32_t seed=120+p;profiles.insert(WindowsFontPackageProfileV2(seed));
  for (const auto name:system) assert(!ShardWindowsFontHiddenV2(name,seed));
  for (const auto name:{"Century","Century Gothic","Agency FB","MS Outlook","MT Extra","Lucida Bright","Haettenschweiler","Lucida Sans","MS Reference Specialty","Monotype Corsiva","Pristina"})
   assert(ShardWindowsFontHiddenV2(name,seed)==((masks[p]&1u)==0));
  for (const auto name:{"Noto Sans SC","Noto Sans SC Black","NotoSansSC-ExtraLight","Noto Sans CJK JP Bold"})
   assert(ShardWindowsFontHiddenV2(name,seed)==((masks[p]&2u)==0));
  for (const auto name:{"Noto Serif SC","Noto Serif SC Bold","NotoSerifSC-Black","Noto Serif CJK KR Medium"})
   assert(ShardWindowsFontHiddenV2(name,seed)==((masks[p]&4u)==0));
  for (const auto name:{"Liberation Sans","Liberation Serif Bold","LiberationMono-Regular"})
   assert(ShardWindowsFontHiddenV2(name,seed)==((masks[p]&8u)==0));
  assert(!ShardWindowsFontHiddenV2("Unknown Newly Installed Font",seed));
 }
 assert(profiles.size()==12);
 for (const auto name:{"Yu Gothic UI","Yu Gothic UI Bold","YuGothicUI-Regular","Segoe UI Variable Display Semib"}) {
  assert(WindowsFontListedV2(name,kWindowsSystemFontNamesV2));
  for (uint32_t seed:{1u,9918u,14666u,0xffffffffu}) assert(!ShardWindowsFontHiddenV2(name,seed));
 }
 for (const auto name:{"Meiryo","Batang","SimHei","Microsoft Uighur","Leelawadee"})
  for (uint32_t seed=1;seed<=128;++seed) assert(!ShardWindowsFontHiddenV2(name,seed));
 assert(!ShardWindowsFontHiddenV2("Century",0));
 assert(!ShardWindowsFontHiddenV2("",14666));
}
'''.replace('SYSTEM_NAMES', core)
        self.build_and_run('windows-font-package-policy', source)

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
        self.assertIn('+#if BUILDFLAG(IS_WIN)\n+    return !base::uxr_surface_v1::ShardWindowsFontHiddenV2(', patch)
        self.assertIn('+#else\n+    return !base::uxr_surface_v1::ShardFontHidden(', patch)
        # The native and noise-disabled branches remain outside the policy.
        self.assertIn('!modes.noise_disabled && !modes.raw', patch)


if __name__ == '__main__':
    unittest.main()
