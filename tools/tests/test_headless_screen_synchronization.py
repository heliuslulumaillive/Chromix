"""Exercise the actual header supplied by the headless display overlay."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / 'patches/fingerprint-modes/0014-headless-screen-synchronization.patch'


def added_file(name):
    text = PATCH.read_text(encoding='utf-8')
    part = text.split('+++ b/' + name + '\n', 1)[1].split('--- a/', 1)[0]
    return ''.join(line[1:] + '\n' for line in part.splitlines()
                   if line.startswith('+') and not line.startswith('+++'))


class HeadlessScreenSynchronization(unittest.TestCase):
    def test_browser_only_and_native_precedence_are_explicit(self):
        text = PATCH.read_text(encoding='utf-8')
        self.assertIn('command_line->HasSwitch("headless")', text)
        self.assertIn('!command_line->HasSwitch("screen-info")', text)
        self.assertIn('!command_line->HasSwitch("force-device-scale-factor")', text)
        self.assertIn('command_line->HasSwitch("uxr-screen-width")', text)
        self.assertIn('command_line->HasSwitch("uxr-screen-height")', text)
        self.assertIn('command_line->HasSwitch("uxr-device-pixel-ratio")', text)
        self.assertIn('available_height != height - taskbar', text)
        self.assertNotIn('AppendSwitchASCII("window-size"', text)

    def test_actual_geometry_header(self):
        compiler = os.environ.get('CHROMIX_NATIVE_UNIT_CXX')
        if not compiler:
            self.skipTest('Set CHROMIX_NATIVE_UNIT_CXX in a configured C++ toolchain environment')
        program = r'''
#include "base/uxr_headless_screen.h"
#include <cassert>
#include <iostream>
#include <limits>
int main() {
  using base::MakeUxrHeadlessScreenInfo;
  int checks = 0;
  const auto expect = [&](int w, int h, int aw, int ah, double d,
                          int pw, int ph, int right, int bottom) {
    auto s = MakeUxrHeadlessScreenInfo(w,h,aw,ah,d);
    assert(s); ++checks;
    assert(s->pixel_width == pw && s->pixel_height == ph); ++checks;
    assert(s->work_area_right == right && s->work_area_bottom == bottom); ++checks;
    assert(s->device_pixel_ratio == d); ++checks;
  };
  expect(1280,720,1280,680,2,2560,1440,0,80);
  expect(1280,720,1280,680,1.25,1600,900,0,50);
  expect(1280,720,1280,680,1,1280,720,0,40);
  expect(1280,720,1280,680,0.5,640,360,0,20);
  expect(1001,721,1000,700,1.25,1252,902,2,27);
  expect(32768,32768,32768,32768,8,262144,262144,0,0);
  expect(1,1,0,0,0.5,1,1,1,1);
  for (double d : {0.0,0.25,0.499,8.001,-1.0,
                    std::numeric_limits<double>::infinity(),
                    std::numeric_limits<double>::quiet_NaN()}) {
    assert(!MakeUxrHeadlessScreenInfo(1280,720,1280,680,d)); ++checks;
  }
  for (int d : {-100,0,32769,std::numeric_limits<int>::max()}) {
    assert(!MakeUxrHeadlessScreenInfo(d,720,0,680,2)); ++checks;
    assert(!MakeUxrHeadlessScreenInfo(1280,d,1280,0,2)); ++checks;
  }
  for (int bad : {-1,1281,std::numeric_limits<int>::max()}) {
    assert(!MakeUxrHeadlessScreenInfo(1280,720,bad,680,2)); ++checks;
  }
  for (int bad : {-1,721,std::numeric_limits<int>::max()}) {
    assert(!MakeUxrHeadlessScreenInfo(1280,720,1280,bad,2)); ++checks;
  }
  std::cout << "{\"headless_screen_checks\":" << checks << ",\"failures\":0}\n";
}
'''
        with tempfile.TemporaryDirectory(prefix='headless-screen-unit-') as temp:
            root = Path(temp)
            (root / 'base').mkdir()
            (root / 'base/uxr_headless_screen.h').write_text(added_file('base/uxr_headless_screen.h'), encoding='utf-8')
            src = root / 'test.cc'
            src.write_text(program, encoding='utf-8')
            exe = root / ('test.exe' if os.name == 'nt' else 'test')
            built = subprocess.run([compiler,'-std=c++20','-O2','-UNDEBUG','-I',str(root),str(src),'-o',str(exe)], capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            run = subprocess.run([str(exe)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            self.assertIn('"headless_screen_checks":49',run.stdout)
            print(run.stdout.strip())


if __name__ == '__main__':
    unittest.main()
