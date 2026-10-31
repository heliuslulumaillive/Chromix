"""Retirement regressions against the effective final-source tree, not flags."""
from __future__ import annotations
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from tools.build_lite_retirement_patch import mask, matching, remove_function
from tools.lite_retirement import verify_retirement, REMOVED_FILES

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = 'patches/lite-win156/0001-retire-bridge-runtime-synthetic.patch'
PROTECTED = (
    'base/uxr_cloak_hardware.cc', 'base/uxr_cloak_hardware.h',
    'base/uxr_gpu_modes.h', 'base/uxr_graphics_noise.cc', 'base/uxr_graphics_noise.h',
    'base/uxr_pixel_noise.h', 'base/uxr_surface_math.h',
    'base/uxr_voice_compat.h', 'content/browser/speech/tts_win.cc',
    'third_party/blink/renderer/platform/graphics/canvas_fingerprint_context.h',
    'third_party/blink/renderer/platform/graphics/image_data_buffer.cc',
    'third_party/blink/renderer/platform/graphics/image_data_buffer.h',
    'third_party/blink/renderer/core/offscreencanvas/offscreen_canvas.cc',
    'third_party/blink/renderer/core/html/canvas/canvas_async_blob_creator.cc',
    'third_party/blink/renderer/modules/webgl/uxr_shard_readback.h',
    'third_party/blink/renderer/modules/webgl/webgl2_rendering_context_base.cc',
    'chrome/browser/webauthn/authenticator_request_dialog_controller.cc',
    'device/fido/win/authenticator.cc',
)

def extract_function(text, name):
    match = re.search(r'(?m)^[^\n;{}]*\b' + re.escape(name) + r'\s*\(', text)
    if not match:
        raise AssertionError(name)
    brace = text.index('{', matching(text, text.index('(', match.start())) + 1)
    return text[match.start():matching(text, brace, '{', '}') + 1]

def compiler_environment(root):
    environment = dict(os.environ)
    if os.name == 'nt':
        vcvars = Path('C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat')
        if not vcvars.is_file():
            raise RuntimeError('Visual Studio compiler environment is required')
        setup = root / 'environment.cmd'
        setup.write_text('@echo off\ncall "' + str(vcvars) + '" >nul\nif errorlevel 1 exit /b 1\nset\n')
        result = subprocess.run(['cmd.exe', '/d', '/c', str(setup)], capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                environment[key] = value
    return environment

class RecipeTests(unittest.TestCase):
    def test_retirement_is_last_and_donor_inputs_stay_whole(self):
        config = json.loads((ROOT / 'build/google-services-win155.json').read_text())
        self.assertEqual(config['restoration_overlays'][-1], OVERLAY)
        self.assertEqual(config['restoration_overlays'].count(OVERLAY), 1)
        self.assertTrue(config['lite_retirement']['source_gate_required'])
        baseline = json.loads((ROOT / 'build/patch-stack-windows155-baseline.json').read_text())
        self.assertEqual(len(baseline['patches']), 231)
        for entry in baseline['patches']:
            self.assertEqual(hashlib.sha256((ROOT / entry['path']).read_bytes()).hexdigest(), entry['sha256'])

    def test_cleanup_patch_deletes_implementations_and_normal_hooks(self):
        patch = (ROOT / OVERLAY).read_text()
        for name in REMOVED_FILES:
            self.assertIn('--- a/' + name + '\n+++ /dev/null\n', patch)
        self.assertIn('-  RecordBridgeOp(20u, text.Utf8(), {x, y});', patch)
        self.assertIn('-  RecordWebGLOp(63u,', patch)
        self.assertIn('+    [PerWorldBindings, ImplementedAs=OpenShadowRoot]', patch)

    def test_function_removal_does_not_misclassify_calls_or_strings(self):
        source = 'bool Retired(int x) { const char* s = "}\\\""; return x; }\nvoid Keep() { if (Retired(1)) { use(); } }\n'
        result = remove_function(source, 'Retired')
        self.assertIn('if (Retired(1))', result)
        self.assertNotIn('bool Retired', result)

@unittest.skipUnless(os.environ.get('CHROMIX_LITE_RETIREMENT_TREE') and os.environ.get('CHROMIX_LITE_DONOR_TREE'),
                     'Supply exact final and donor source fixtures for integration coverage')
class EffectiveSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ['CHROMIX_LITE_RETIREMENT_TREE']).resolve()
        cls.donor = Path(os.environ['CHROMIX_LITE_DONOR_TREE']).resolve()

    def test_retired_subsystems_are_physically_absent(self):
        checked = verify_retirement(self.source)
        self.assertGreater(len(checked), 30)

    def test_current_graphics_autogpu_voice_and_passkey_files_are_identical(self):
        for name in PROTECTED:
            with self.subTest(path=name):
                self.assertEqual((self.source / name).read_bytes(), (self.donor / name).read_bytes())

    def test_native_gl_calls_and_object_deletions_survive_hook_removal(self):
        name = 'third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc'
        before = mask((self.donor / name).read_text())
        after = mask((self.source / name).read_text())
        calls = lambda t: collections.Counter(re.findall(r'ContextGL\(\)->(\w+)\(', t))
        self.assertEqual(calls(before), calls(after))
        self.assertEqual(before.count('DeleteObject('), after.count('DeleteObject('))
        self.assertNotIn('RecordWebGLOp', after)

    def test_uniform_matrix_native_arguments_and_error_paths_survive_without_staging(self):
        name = 'third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc'
        function = extract_function((self.source / name).read_text(), 'WebGLRenderingContextBase::uniformMatrix4fv')
        self.assertNotIn('std::vector', function)
        self.assertNotIn('matrix.reserve', function)
        self.assertNotIn('matrix.push_back', function)
        self.assertIn('ValidateUniformMatrixParameters(', function)
        self.assertIn('ContextGL()->UniformMatrix4fv(location->Location(), length, transpose, data);', function)
        with tempfile.TemporaryDirectory(dir=os.environ.get('CHROMIX_LITE_RETIREMENT_TEST_ROOT')) as temp:
            root = Path(temp)
            cpp, binary = root / 'uniform.cc', root / ('uniform.exe' if os.name == 'nt' else 'uniform')
            code = r"""#include <array>
#include <cassert>
#include <cstddef>
#include <span>
using GLfloat=float;using GLuint=unsigned;using GLboolean=bool;
namespace base {template<class T>using span=std::span<T>;}
struct WebGLUniformLocation {int Location()const{return 7;}};
struct GL {int calls=0,location=0;GLuint count=0;GLboolean transpose=false;const GLfloat* data=nullptr;
void UniformMatrix4fv(int l,GLuint n,GLboolean t,const GLfloat* p){++calls;location=l;count=n;transpose=t;data=p;}};
struct WebGLRenderingContextBase {GL gl;bool lost=false,valid=true;int validations=0;
bool isContextLost()const{return lost;}GL* ContextGL(){return &gl;}
bool ValidateUniformMatrixParameters(const char*,const WebGLUniformLocation*,GLboolean,base::span<const GLfloat> v,unsigned,std::size_t,std::size_t,const GLfloat** p,GLuint* n){++validations;if(!valid)return false;*p=v.data();*n=static_cast<GLuint>(v.size()/16);return true;}
void uniformMatrix4fv(const WebGLUniformLocation*,GLboolean,base::span<const GLfloat>);
};
""" + function + r"""
int main(){WebGLUniformLocation location;std::array<float,32> values{};for(unsigned i=0;i<values.size();++i)values[i]=float(i)+.5f;const auto before=values;
WebGLRenderingContextBase current;current.uniformMatrix4fv(&location,false,values);assert(current.gl.calls==1&&current.gl.location==7&&current.gl.count==2&&!current.gl.transpose&&current.gl.data==values.data()&&values==before);
WebGLRenderingContextBase lost;lost.lost=true;lost.uniformMatrix4fv(&location,false,values);assert(lost.gl.calls==0&&lost.validations==0);
WebGLRenderingContextBase invalid;invalid.valid=false;invalid.uniformMatrix4fv(&location,false,values);assert(invalid.gl.calls==0&&invalid.validations==1);}
"""
            cpp.write_text(code, encoding='utf-8')
            compiler = shutil.which('clang++') or 'C:/Program Files/LLVM/bin/clang++.exe'
            built = subprocess.run([compiler,'-std=c++20','-Wall','-Wextra','-Werror',str(cpp),'-o',str(binary)],
                                   env=compiler_environment(root),capture_output=True,text=True,timeout=90)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            run = subprocess.run([str(binary)],capture_output=True,text=True,timeout=20)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)

    def test_native_authorities_are_retained_after_synthetic_removal(self):
        files = {
            'third_party/blink/renderer/modules/keyboard/keyboard_layout.cc': 'result->layout_map',
            'third_party/blink/renderer/modules/plugins/dom_plugin_array.cc': 'GetPluginData()',
            'third_party/blink/renderer/modules/credentialmanagement/public_key_credential.cc': 'IsUserVerifyingPlatformAuthenticatorAvailable(',
            'third_party/blink/renderer/modules/speech/speech_synthesis.cc': 'for (auto& mojom_voice : mojom_voices)',
        }
        for name, native in files.items():
            self.assertIn(native, (self.source / name).read_text())

    def test_reintroduced_bridge_hook_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('CHROMIX_LITE_RETIREMENT_TEST_ROOT')) as temp:
            root = Path(temp).resolve()
            from tools.lite_retirement import CHECKED_FILES
            for name in CHECKED_FILES:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.source / name, target)
            target = root / 'third_party/blink/renderer/modules/canvas/canvas2d/canvas_path.cc'
            with target.open('a', encoding='utf-8') as f:
                f.write('\nvoid Reintroduced() { RecordBridgeOp(1, "", {}); }\n')
            with self.assertRaisesRegex(ValueError, 'RecordBridgeOp'):
                verify_retirement(root)

    def test_compile_remaining_surface_goldens_without_retired_resources(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('CHROMIX_LITE_RETIREMENT_TEST_ROOT')) as temp:
            root = Path(temp)
            compiler = shutil.which('clang++') or 'C:/Program Files/LLVM/bin/clang++.exe'
            binary = root / ('surface.exe' if os.name == 'nt' else 'surface')
            built = subprocess.run([compiler, '-std=c++20', '-O2', '-DUXR_SURFACE_GOLDEN_STANDALONE',
                                    '-I' + str(self.source), str(self.source / 'base/uxr_surface_golden_tests.cc'),
                                    '-o', str(binary)], env=compiler_environment(root),
                                   capture_output=True, text=True, timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            record = json.loads(run.stdout.strip().splitlines()[-1])
            self.assertGreater(record['checks'], 0)
            self.assertEqual(record['failures'], 0)
            print('remaining_surface_goldens=' + json.dumps(record))

    def test_compile_actual_full_config_rejects_retired_modes_and_keeps_native_defaults(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('CHROMIX_LITE_RETIREMENT_TEST_ROOT')) as temp:
            root = Path(temp)
            stubs = {
                'base/base_export.h': '#pragma once\n#define BASE_EXPORT\n',
                'base/containers/flat_map.h': '#pragma once\n#include <map>\nnamespace base { template<class K,class V> using flat_map=std::map<K,V>; }\n',
                'base/synchronization/lock.h': '#pragma once\n#include <mutex>\nnamespace base { using Lock=std::mutex; using AutoLock=std::lock_guard<Lock>; }\n',
                'base/no_destructor.h': '#pragma once\n#include <new>\nnamespace base { template<class T> class NoDestructor { public: NoDestructor(){new (storage_) T;} T& operator*(){return *reinterpret_cast<T*>(storage_);} private: alignas(T) unsigned char storage_[sizeof(T)]; }; }\n',
                'base/strings/string_number_conversions.h': '''#pragma once
#include <charconv>
#include <cstdint>
#include <string_view>
namespace base {
template<class T> bool Parse(std::string_view s,T* v){auto r=std::from_chars(s.data(),s.data()+s.size(),*v);return r.ec==std::errc()&&r.ptr==s.data()+s.size();}
inline bool StringToInt(std::string_view s,int* v){return Parse(s,v);}
inline bool StringToUint(std::string_view s,unsigned* v){return Parse(s,v);}
inline bool StringToUint64(std::string_view s,uint64_t* v){return Parse(s,v);}
inline bool StringToDouble(std::string_view s,double* v){return Parse(s,v);}
}
''',
                'base/strings/string_util.h': '''#pragma once
#include <string_view>
namespace base { enum TrimPositions {TRIM_ALL}; inline std::string_view TrimWhitespaceASCII(std::string_view s,TrimPositions){const auto first=s.find_first_not_of(" \\t\\r\\n\\f\\v");if(first==std::string_view::npos)return {};return s.substr(first,s.find_last_not_of(" \\t\\r\\n\\f\\v")-first+1);} inline bool IsStringUTF8(std::string_view s) {
for(size_t i=0;i<s.size();){unsigned c=static_cast<unsigned char>(s[i++]);if(c<128)continue;unsigned n=0,v=0,min=0;
if(c>=0xc2&&c<=0xdf){n=1;v=c&31;min=128;}else if(c>=0xe0&&c<=0xef){n=2;v=c&15;min=2048;}else if(c>=0xf0&&c<=0xf4){n=3;v=c&7;min=65536;}else return false;
while(n--){if(i==s.size())return false;unsigned b=static_cast<unsigned char>(s[i++]);if((b&192)!=128)return false;v=(v<<6)|(b&63);}if(v<min||v>0x10ffff||(v>=0xd800&&v<=0xdfff))return false;}return true;} }
''',
            }
            for name, text in stubs.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            main = root / 'main.cc'
            main.write_text('''#include "base/uxr_config.h"
#include <cassert>
#include <string>
int main(){auto& c=base::UxrConfig::GetInstance();
for(const char* surface:{"fonts","audio","media"})for(const char* mode:{"chromix","cloak"}){assert(!c.SetAll({{std::string("uxr-")+surface+"-mode",mode}}));assert(!c.IsInitialized());}
for(const char* key:{"uxr-font-policy","uxr-font-whitelist","uxr-audio-render","uxr-windows-font-metrics"})assert(!c.SetAll({{key,"native"}}));
assert(c.SetAll({}));const auto m=c.SurfaceModes();assert(m.fonts.IsNative()&&m.audio.IsNative()&&m.media.IsNative());assert(m.canvas.IsLegacy()&&m.webgl.IsLegacy());}
''')
            compiler = shutil.which('clang++') or 'C:/Program Files/LLVM/bin/clang++.exe'
            binary = root / ('config.exe' if os.name == 'nt' else 'config')
            built = subprocess.run([compiler, '-std=c++20', '-Wall', '-Wextra', '-Werror',
                                    '-I' + str(root), '-I' + str(self.source),
                                    str(self.source / 'base/uxr_config.cc'), str(main), '-o', str(binary)],
                                   env=compiler_environment(root), capture_output=True, text=True, timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_compile_actual_backend_policy_and_runtime_console_semantics(self):
        # This is a narrow compiled acceptance of the actual modified functions,
        # with transport/container shims. It is not a Chromium native build.
        with tempfile.TemporaryDirectory(dir=os.environ.get('CHROMIX_LITE_RETIREMENT_TEST_ROOT')) as temp:
            root = Path(temp)
            compiler = shutil.which('clang++') or 'C:/Program Files/LLVM/bin/clang++.exe'
            self.assertTrue(Path(compiler).is_file(), 'C++ compiler required for executed coverage')
            environment = dict(os.environ)
            if os.name == 'nt':
                vcvars = Path('C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat')
                self.assertTrue(vcvars.is_file())
                setup = root / 'environment.cmd'
                setup.write_text('@echo off\ncall "' + str(vcvars) + '" >nul\nif errorlevel 1 exit /b 1\nset\n')
                result = subprocess.run(['cmd.exe', '/d', '/c', str(setup)], capture_output=True, text=True, check=True)
                for line in result.stdout.splitlines():
                    if '=' in line:
                        key, value = line.split('=', 1)
                        environment[key] = value
            header = (self.source / 'base/uxr_config.h').read_text()
            start = re.search(r'struct\s+(?:BASE_EXPORT\s+)?UxrGpuBackendPolicy', header).start()
            brace = header.index('{', start)
            policy = header[start:matching(header, brace, '{', '}') + 2].replace('BASE_EXPORT ', '')
            config = (self.source / 'base/uxr_config.cc').read_text()
            runtime = (self.source / 'v8/src/inspector/v8-runtime-agent-impl.cc').read_text()
            code = '''#include <map>
#include <string>
#include <cstdint>
#include <charconv>
#include <cassert>
template<class K,class V> using flat_map=std::map<K,V>;
bool StringToUint64(const std::string& s,uint64_t* v){auto r=std::from_chars(s.data(),s.data()+s.size(),*v);return r.ec==std::errc()&&r.ptr==s.data()+s.size();}
''' + policy + '\n' + extract_function(config, 'ParseGpuBackend') + '''
struct V8ConsoleMessage {};
struct V8RuntimeAgentImpl { bool m_enabled=false; int reports=0;
bool enabled() const { return m_enabled; }
void reportMessage(V8ConsoleMessage*,bool){++reports;}
void messageAdded(V8ConsoleMessage*);
};
''' + extract_function(runtime, 'V8RuntimeAgentImpl::messageAdded') + '''
int main(){
 UxrGpuBackendPolicy p; assert(ParseGpuBackend({},&p));assert(p.native&&!p.seeded_pixel_noise);
 p={}; assert(ParseGpuBackend({{"uxr-pixel-noise","seeded"},{"uxr-canvas-seed","17"}},&p));assert(p.native&&p.seeded_pixel_noise&&p.canvas_pixel_noise&&p.pixel_noise_seed==17);
 p={}; assert(ParseGpuBackend({{"uxr-pixel-noise","native"}},&p));assert(!p.canvas_pixel_noise);
 p={}; assert(ParseGpuBackend({{"uxr-pixel-noise","seeded"},{"uxr-disable-fingerprint-noise",""}},&p));assert(!p.canvas_pixel_noise);
 p={}; assert(!ParseGpuBackend({{"uxr-pixel-noise","seeded"},{"uxr-canvas-seed","0"}},&p));
 V8RuntimeAgentImpl r; V8ConsoleMessage m; r.messageAdded(&m);assert(r.reports==0&&!r.enabled());r.m_enabled=true;r.messageAdded(&m);assert(r.reports==1&&r.enabled());
}
'''
            cpp, binary = root / 'retirement.cc', root / ('retirement.exe' if os.name == 'nt' else 'retirement')
            cpp.write_text(code, encoding='utf-8')
            built = subprocess.run([compiler, '-std=c++20', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(binary)],
                                   capture_output=True, text=True, env=environment, timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

if __name__ == '__main__':
    unittest.main()
