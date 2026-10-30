"""Compile production pixel/halo helpers; GPU integration needs a built browser."""
from pathlib import Path
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from build_google_services_patch import sections


def materialize(destination):
    wanted = {'base/uxr_graphics_noise.h', 'base/uxr_graphics_noise.cc',
              'base/uxr_surface_modes.h', 'base/uxr_pixel_noise.h',
              'base/uxr_graphics_mode_goldens.cc'}
    for name in ['patches/0221-pixel-noise-shared-helper.patch',
                 'patches/fingerprint-modes/0001-independent-mode-contract.patch',
                 'patches/fingerprint-modes/0002-graphics.patch']:
        for path, created, data in sections((ROOT / name).read_text(encoding='utf-8')):
            if path not in wanted:
                continue
            if not created:
                raise AssertionError(path)
            value = ''.join(line[1:] for line in data.splitlines(keepends=True)
                            if line.startswith('+') and not line.startswith('+++'))
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value, encoding='utf-8', newline='\n')
    program = 'C:/Program Files/Git/usr/bin/patch.exe' if os.name == 'nt' else 'patch'
    for name in ['0009-shardx-webgl-consistent-readback.patch',
                 '0011-pixel-semantic-preservation.patch',
                 '0015-pixel-mask-first.patch']:
        chosen = ''.join(text for path, _, text in sections(
            (ROOT / 'patches/fingerprint-modes' / name).read_text(encoding='utf-8'))
            if (path.startswith('base/') and path != 'base/BUILD.gn') or path.endswith('/uxr_shard_readback.h'))
        patch = destination / name
        patch.write_text(chosen, encoding='utf-8', newline='\n')
        subprocess.run([program, '-p1', '--batch', '--fuzz=0', '-i', str(patch)],
                       cwd=destination, check=True, capture_output=True)
    return program


def callsite_excerpt(source, start):
    """Read an actual selected C++ branch, ignoring braces in comments/strings."""
    index = source.index(start)
    masked = re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"',
                    lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]),
                    source, flags=re.S)
    opened = masked.index('{', index)
    depth = 0
    for end in range(opened, len(masked)):
        depth += masked[end] == '{'
        depth -= masked[end] == '}'
        if depth == 0:
            return source[index:end + 1]
    raise AssertionError('Incomplete source branch')


def materialize_callsite_excerpts(destination, patch_program):
    fixture = json.loads(gzip.decompress((ROOT / 'tools/tests/fixtures/pixel-semantics-v1/readback-callsites-before-0011.json.gz').read_bytes()))
    tree = destination / 'callsite-sources'
    for name, text in fixture['sources'].items():
        if hashlib.sha256(text.encode()).hexdigest() != fixture['sha256'][name]:
            raise AssertionError('Callsite source fixture identity changed')
        target = tree / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8', newline='\n')
    chosen = ''.join(text for name, _, text in sections((ROOT / 'patches/fingerprint-modes/0011-pixel-semantic-preservation.patch').read_text()) if name in fixture['sources'])
    patch = tree / 'callsite.patch'
    patch.write_text(chosen, encoding='utf-8', newline='\n')
    subprocess.run([patch_program, '-p1', '--batch', '--fuzz=0', '-i', str(patch)], cwd=tree, check=True, capture_output=True)
    common = '''
const int x=2,y=3,width=5,height=6;const GLenum format=GL_RGBA,type=GL_UNSIGNED_BYTE;
void* framebuffer=nullptr;auto* buffer=&buffer_object;const int64_t offset=0,size=4096;
auto* pixels=&caller_pixels;void* data=pixels->bytes.data();
const CheckedSize buffer_size{pixels->bytes.size()},offset_in_bytes{0};
'''
    rendered = []
    for is_pbo, name in [(False, 'webgl_rendering_context_base.cc'), (True, 'webgl2_rendering_context_base.cc')]:
        text = (tree / 'third_party/blink/renderer/modules/webgl' / name).read_text()
        start = text.index('scoped_refptr<DrawingBuffer> readback_owner = GetDrawingBuffer();')
        end = text.index('    const auto modes =' if is_pbo else '    const auto policy =', start)
        prefix = text[start:end]
        if is_pbo:
            config = 'const auto modes = base::UxrConfig::GetInstance().SurfaceModes();\n'
            branch = callsite_excerpt(text, 'if (modes.webgl.mode == base::UxrSurfaceMode::kShardX &&')
        else:
            config = 'const auto surface_modes = base::UxrConfig::GetInstance().SurfaceModes();\nconst auto& selected=surface_modes.webgl;const bool enabled=!selected.IsNative()&&!surface_modes.noise_disabled&&!surface_modes.raw;\n'
            branch = callsite_excerpt(text, 'if (enabled && selected.mode == base::UxrSurfaceMode::kShardX)')
        # A source refactor removing the owner/retained transport must fail here,
        # rather than silently testing a separate hand-written implementation.
        assert 'binder(readback_owner.get(), framebuffer)' in prefix
        assert 'readback_owner->ContextGL()' in prefix
        assert 'ContextGL()->' not in branch
        assert branch.count('if (isContextLost())') >= 2
        rendered.append('void Run' + ('Pbo' if is_pbo else 'Typed') + '(){\n' + common + prefix + config + branch + '\n}\n')
    (destination / 'pixel_readback_callsites.inc').write_text('\n'.join(rendered), encoding='utf-8')


class PixelSemanticProperties(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='pixel-semantics-')
        cls.source = Path(cls.temporary.name)
        cls.patch_program = materialize(cls.source)
        materialize_callsite_excerpts(cls.source, cls.patch_program)
        cls.compiler = shutil.which('clang++') or 'C:/Program Files/LLVM/bin/clang++.exe'
        if not Path(cls.compiler).exists() and not shutil.which(cls.compiler):
            raise RuntimeError('A C++20 compiler is required; executed coverage must not be skipped')
        cls.environment = dict(os.environ)
        if os.name == 'nt':
            vcvars = 'C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat'
            setup = cls.source / 'compiler-environment.cmd'
            setup.write_text(f'@echo off\ncall "{vcvars}" >nul\nif errorlevel 1 exit /b 1\nset\n')
            result = subprocess.run(['cmd.exe', '/d', '/c', str(setup)],
                                    capture_output=True, text=True, check=True)
            for line in result.stdout.splitlines():
                if '=' in line:
                    key, value = line.split('=', 1)
                    cls.environment[key] = value
        stubs = {
            'base/base_export.h': '#pragma once\n#define BASE_EXPORT\n',
            'base/containers/span.h': '#pragma once\n#include <span>\nnamespace base { using std::span; }\n',
            'base/compiler_specific.h': '#pragma once\n#define UNSAFE_BUFFERS(x) x\n',
            'base/process/memory.h': '#pragma once\n#include <cstdlib>\nnamespace base { inline bool UncheckedMalloc(size_t n, void** p){*p=std::malloc(n);return *p!=nullptr;} struct UncheckedFreeDeleter{void operator()(void* p) const{std::free(p);}}; }\n',
        }
        for name, value in stubs.items():
            target = cls.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value, encoding='utf-8')
        cls.executable = cls.source / ('properties.exe' if os.name == 'nt' else 'properties')
        command = [cls.compiler, '-std=c++20', '-O2', '-DUXR_GRAPHICS_STANDALONE',
                   '-I' + str(cls.source), str(cls.source / 'base/uxr_graphics_noise.cc'),
                   str(cls.source / 'base/uxr_graphics_mode_goldens.cc'), '-o', str(cls.executable)]
        built = subprocess.run(command, env=cls.environment, capture_output=True, text=True)
        if built.returncode:
            raise RuntimeError(built.stdout + built.stderr)
        cls.result = subprocess.run([str(cls.executable), str(ROOT / 'tools/tests/fixtures')],
                                    capture_output=True, text=True, timeout=90)
        cls.records = [json.loads(line) for line in cls.result.stdout.splitlines()
                       if line.startswith('{')]
        # Negative builds change the implementation, never the frozen oracle.
        # Passing labels alone cannot establish bit-exact equivalence.
        cls.negative_mutations = []
        production = cls.source / 'base/uxr_graphics_noise.cc'
        original = production.read_text(encoding='utf-8')
        for label, before, after in [
                ('canvas-seed', '0x43415633u', '0x43415632u'),
                ('webgl-mask', '0x85ebca6bu), 16)', '0x85ebca6au), 16)')]:
            if original.count(before) != 1:
                raise RuntimeError('Mutation no longer selects the expected production code: ' + label)
            mutated = cls.source / (label + '.cc')
            mutated.write_text(original.replace(before, after), encoding='utf-8', newline='\n')
            executable = cls.source / (label + ('.exe' if os.name == 'nt' else ''))
            mutation_command = [cls.compiler, '-std=c++20', '-O2', '-DUXR_GRAPHICS_STANDALONE',
                '-I' + str(cls.source), str(mutated),
                str(cls.source / 'base/uxr_graphics_mode_goldens.cc'), '-o', str(executable)]
            mutation_build = subprocess.run(mutation_command, env=cls.environment, capture_output=True, text=True)
            if mutation_build.returncode:
                raise RuntimeError(mutation_build.stdout + mutation_build.stderr)
            mutation_run = subprocess.run([str(executable), str(ROOT / 'tools/tests/fixtures')],
                                          capture_output=True, text=True, timeout=90)
            cls.negative_mutations.append({'mutation': label, 'exitCode': mutation_run.returncode,
                'records': [json.loads(line) for line in mutation_run.stdout.splitlines() if line.startswith('{')],
                'stderr': mutation_run.stderr})
        gl_header = cls.source / 'gpu/command_buffer/client/gles2_interface.h'
        gl_header.parent.mkdir(parents=True, exist_ok=True)
        gl_header.write_text('''#pragma once
using GLenum=unsigned;using GLuint=unsigned;using GLint=int;using GLsizei=int;using GLintptr=long long;using GLsizeiptr=long long;
inline constexpr GLenum GL_RGBA=0x1908,GL_UNSIGNED_BYTE=0x1401,GL_FLOAT=0x1406,
GL_PACK_ALIGNMENT=0x0d05,GL_PACK_ROW_LENGTH=0x0d02,GL_PACK_SKIP_PIXELS=0x0d04,
GL_PACK_SKIP_ROWS=0x0d03,GL_PIXEL_PACK_BUFFER=0x88eb,GL_OUT_OF_MEMORY=0x505;
namespace gpu::gles2 {class GLES2Interface {public:
struct ReadPixelsCompletion{bool success=false;GLsizei columns=0,rows=0;};
virtual ~GLES2Interface()=default;
virtual bool SupportsReadPixelsCompletion() const{return false;}
virtual void BindBuffer(GLenum,GLuint)=0;virtual void PixelStorei(GLenum,GLint)=0;
virtual void BufferSubData(GLenum,GLintptr,GLsizeiptr,const void*)=0;
virtual ReadPixelsCompletion ReadPixelsWithCompletion(GLint,GLint,GLsizei,GLsizei,GLenum,GLenum,void*)=0;
};}
''', encoding='utf-8')
        halo_exe = cls.source / ('halo.exe' if os.name == 'nt' else 'halo')
        built = subprocess.run([cls.compiler, '-std=c++20', '-O2', '-I' + str(cls.source),
            str(ROOT / 'tools/tests/pixel_halo_properties.cc'), str(cls.source / 'base/uxr_graphics_noise.cc'), '-o', str(halo_exe)],
            env=cls.environment, capture_output=True, text=True)
        if built.returncode:
            raise RuntimeError(built.stdout + built.stderr)
        cls.halo_result = subprocess.run([str(halo_exe)], capture_output=True, text=True, timeout=20)
        cls.halo_records = [json.loads(line) for line in cls.halo_result.stdout.splitlines() if line.startswith('{')]
        empty_fixture = cls.source / 'header-only-fixtures'
        shutil.copytree(ROOT / 'tools/tests/fixtures/graphics-modes-v1', empty_fixture / 'graphics-modes-v1')
        (empty_fixture / 'pixel-semantics-v1').mkdir()
        (empty_fixture / 'pixel-semantics-v1/cases.tsv').write_text('name\twidth\theight\toffset\tbytes\n')
        (empty_fixture / 'pixel-semantics-v1/pixels.bin').write_bytes(b'')
        cls.empty_corpus_result = subprocess.run([str(cls.executable), str(empty_fixture)], capture_output=True, text=True, timeout=90)
        cls.empty_corpus_records = [json.loads(line) for line in cls.empty_corpus_result.stdout.splitlines() if line.startswith('{')]
        destination = os.environ.get('PIXEL_SEMANTIC_REPORT')
        if destination:
            report = Path(destination)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({'status': 'passed' if cls.result.returncode == 0 and cls.halo_result.returncode == 0 and cls.empty_corpus_result.returncode != 0 and all(r['exitCode'] != 0 for r in cls.negative_mutations) else 'failed',
                'compiler': cls.compiler, 'records': cls.records, 'stderr': cls.result.stderr,
                'haloRecords': cls.halo_records, 'haloStderr': cls.halo_result.stderr,
                'emptyCorpusNegative': {'exitCode': cls.empty_corpus_result.returncode, 'records': cls.empty_corpus_records},
                'maskFirstMutationNegatives': cls.negative_mutations,
                'scope': 'Real production helper, historical browser byte goldens, new semantics invariants; no live GL/PNG runtime acceptance'}, indent=2), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_real_helpers_and_historical_goldens(self):
        self.assertEqual(self.result.returncode, 0, self.result.stdout + self.result.stderr)
        semantic = [r for r in self.records if 'pixel_semantic_checks' in r]
        self.assertEqual(len(semantic), 1)
        self.assertGreater(semantic[0]['pixel_semantic_checks'], 100000)
        self.assertGreater(semantic[0]['positive_complex_cases'], 0)
        self.assertGreater(semantic[0]['pixel_semantic_corpus_records'], 0)
        self.assertEqual(semantic[0]['failures'], 0)
        graphics = [r for r in self.records if 'graphics_cases' in r]
        self.assertEqual(len(graphics), 1)
        self.assertEqual(graphics[0]['failures'], 0)

    def test_append_only_patch_reverses(self):
        with tempfile.TemporaryDirectory(prefix='mask-first-reverse-') as directory:
            copied = Path(directory) / 'tree'
            shutil.copytree(self.source, copied)
            for name in ['0015-pixel-mask-first.patch', '0011-pixel-semantic-preservation.patch']:
                result = subprocess.run([self.patch_program, '-R', '-p1', '--batch',
                                         '--fuzz=0', '-i', str(copied / name)], cwd=copied,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mask_first_exact_equivalence_and_mutation_controls(self):
        records = [r for r in self.records if 'mask_first_equivalence_cases' in r]
        self.assertEqual(len(records), 1)
        expected = {'mask_first_equivalence_cases': 93941, 'mask_first_equivalence_bytes': 439779036,
                    'mask_first_corpus_records': 3, 'mask_first_mutation_controls': 8,
                    'mask_first_mutation_rejections': 8, 'failures': 0}
        for name, value in expected.items():
            self.assertEqual(records[0][name], value)
        self.assertEqual(records[0]['mask_first_oracle_revision'], '9a06334258ed6039c597360038183f8693347e45')
        self.assertEqual(records[0]['mask_first_oracle_sha256'], '2bcefcc4b7b8ab34b070b964a4fdd81c643e88052b48fd0c3a75cf18d8965465')
        graphics = next(r for r in self.records if 'graphics_cases' in r)
        self.assertEqual(graphics['groups']['pixel_mask_first_equivalence'], 93941)
        for mutation in self.negative_mutations:
            with self.subTest(mutation=mutation['mutation']):
                self.assertNotEqual(mutation['exitCode'], 0)
                value = next(r for r in mutation['records'] if 'mask_first_equivalence_cases' in r)
                self.assertGreater(value['failures'], 0)

    def test_frozen_oracle_is_exact_preoptimization_source(self):
        reference = (self.source / 'base/uxr_mask_first_reference.h').read_text(encoding='utf-8')
        frozen = reference.split('// BEGIN FROZEN 9a06334 HELPER\n', 1)[1].split('// END FROZEN 9a06334 HELPER\n', 1)[0]
        restored = frozen.replace('namespace base::mask_first_reference {', 'namespace base {', 1).replace(
            '}  // namespace base::mask_first_reference\n', '}  // namespace base\n')
        self.assertEqual(hashlib.sha256(restored.encode()).hexdigest(),
                         '2bcefcc4b7b8ab34b070b964a4fdd81c643e88052b48fd0c3a75cf18d8965465')
        self.assertIn('namespace base::mask_first_reference {\n// Preserve', reference)
        self.assertIn('uint32_t UxrCloakHashV1(std::string_view bytes);', reference.split('// BEGIN FROZEN')[0])
        patch = (ROOT / 'patches/fingerprint-modes/0015-pixel-mask-first.patch').read_text()
        files = {name: text for name, _, text in sections(patch)}
        self.assertEqual(set(files), {'base/uxr_graphics_noise.cc', 'base/uxr_graphics_mode_goldens.cc',
                                     'base/uxr_mask_first_reference.h', 'base/uxr_mask_first_equivalence.h', 'base/BUILD.gn'})
        self.assertIn('"uxr_mask_first_equivalence.h"', files['base/BUILD.gn'])

    def test_halo_layout_and_pack_state(self):
        self.assertEqual(self.halo_result.returncode, 0, self.halo_result.stdout + self.halo_result.stderr)
        self.assertEqual(len(self.halo_records), 1)
        self.assertGreater(self.halo_records[0]['pixel_halo_checks'], 50)
        self.assertGreater(self.halo_records[0]['pixel_callsite_loss_checks'], 0)
        self.assertEqual(self.halo_records[0]['failures'], 0)

    def test_header_only_corpus_is_rejected(self):
        self.assertNotEqual(self.empty_corpus_result.returncode, 0)
        semantic = [r for r in self.empty_corpus_records if 'pixel_semantic_checks' in r]
        self.assertEqual(len(semantic), 1)
        self.assertEqual(semantic[0]['pixel_semantic_corpus_records'], 0)
        self.assertGreater(semantic[0]['failures'], 0)

    def test_every_live_byte_route_supplies_neighborhood(self):
        patch = (ROOT / 'patches/fingerprint-modes/0011-pixel-semantic-preservation.patch').read_text()
        files = {name: text for name, _, text in sections(patch)}
        self.assertIn('same immutable snapshot', files['third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d.cc'])
        for name in ['webgl_rendering_context_base.cc', 'webgl2_rendering_context_base.cc']:
            code = files['third_party/blink/renderer/modules/webgl/' + name]
            self.assertIn('UxrShardReadNeighborhood', code)
            self.assertIn('protected_region.neighborhood = neighborhood.view', code)
        self.assertIn('region.neighborhood =', files['third_party/blink/renderer/platform/graphics/image_data_buffer.cc'])
        self.assertIn('"uxr_pixel_semantic_properties.h"', files['base/BUILD.gn'])


if __name__ == '__main__':
    unittest.main()
