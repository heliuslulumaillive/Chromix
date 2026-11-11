"""Execute the selected product helper, preserving frozen rendering goldens."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
from build_google_services_patch import sections

class CanvasInputMaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='canvas-input-mask-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root=Path(cls.temp.name)
        names={'base/uxr_graphics_noise.cc','base/uxr_graphics_noise.h',
               'base/uxr_pixel_noise.h','base/uxr_surface_modes.h',
               'base/uxr_graphics_mode_goldens.cc','base/uxr_pixel_semantic_properties.h',
               'base/uxr_mask_first_equivalence.h','base/uxr_mask_first_reference.h'}
        for patch in (ROOT/'patches/lite-windows157').glob('*.patch'):
            for name,created,data in sections(patch.read_text(encoding='utf-8')):
                if name not in names and not (name.startswith('base/uxr_') and name.endswith('.h')):continue
                if not created:raise AssertionError('Expected complete generated helper '+name)
                value=''.join(line[1:] for line in data.splitlines(keepends=True) if line.startswith('+') and not line.startswith('+++'))
                dest=cls.root/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(value,encoding='utf-8',newline='\n')
        for name,text in {
            'base/base_export.h':'#pragma once\n#define BASE_EXPORT\n',
            'base/containers/span.h':'#pragma once\n#include <span>\nnamespace base { using std::span; }\n',
            'base/compiler_specific.h':'#pragma once\n#define UNSAFE_BUFFERS(x) x\n',
            'base/process/memory.h':'#pragma once\n#include <cstdlib>\nnamespace base { inline bool UncheckedMalloc(size_t n,void**p){*p=std::malloc(n);return *p!=nullptr;} struct UncheckedFreeDeleter{void operator()(void*p)const{std::free(p);}};}\n',
        }.items():
            path=cls.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        cls.compiler=shutil.which('clang++') or r'C:\Program Files\LLVM\bin\clang++.exe'
        if not Path(cls.compiler).is_file() and not shutil.which(cls.compiler):raise RuntimeError('C++20 compiler required')
        cls.env=dict(os.environ)
        if os.name=='nt':
            vswhere=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
            installation=subprocess.check_output([str(vswhere),'-latest','-products','*','-requires','Microsoft.VisualStudio.Component.VC.Tools.x86.x64','-property','installationPath'],text=True).strip()
            if not installation:raise RuntimeError('Native Visual C++ toolchain required')
            vc=str(Path(installation)/'VC/Auxiliary/Build/vcvars64.bat')
            script=cls.root/'compiler.cmd';script.write_text('@echo off\ncall "'+vc+'" >nul\nif errorlevel 1 exit /b 1\nset\n')
            run=subprocess.run(['cmd.exe','/d','/c',str(script)],capture_output=True,text=True,check=True)
            for line in run.stdout.splitlines():
                if '=' in line:
                    key,value=line.split('=',1);cls.env[key]=value

    def build_and_run(self,name,source=None,goldens=False):
        unit=self.root/('base/uxr_graphics_mode_goldens.cc' if goldens else name+'.cc')
        if source is not None:unit.write_text(source,encoding='utf-8')
        binary=self.root/(name+('.exe' if os.name=='nt' else ''))
        args=[self.compiler,'-std=c++20','-O2','-I'+str(self.root),str(self.root/'base/uxr_graphics_noise.cc'),str(unit),'-o',str(binary)]
        if goldens:args.insert(1,'-DUXR_GRAPHICS_STANDALONE')
        result=subprocess.run(args,env=self.env,capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result=subprocess.run([str(binary),str(ROOT/'tools/tests/fixtures')],capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_unchanged_frozen_rendering_goldens(self):
        self.build_and_run('goldens',goldens=True)

    def test_bulk_mask_ranges_match_individual_pixel_updates(self):
        from tools.tests.test_pixel_semantic_preservation import callsite_excerpt
        manifest=json.loads((ROOT/'build/lite-windows157.json').read_text())
        entry=next(row for row in manifest['source_files'] if row['path'].endswith('/graphics/image_data_buffer.cc'))
        source=(ROOT/'build/checkpoint-sources'/(entry['after_sha256']+'.cc')).read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(),entry['after_sha256'])
        helpers='\n'.join(callsite_excerpt(source.decode(),name) for name in ('uint64_t UxrInputColumnBits(', 'void UxrSetInputMaskRange('))
        self.build_and_run('ranges',r'''
#include "base/containers/span.h"
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <vector>
#define CHECK_GE(a,b) assert((a)>=(b))
#define CHECK_LE(a,b) assert((a)<=(b))
#define CHECK_EQ(a,b) assert((a)==(b))
'''+helpers+r'''
int main(){
 for(int first=0;first<=64;++first)for(int count=0;count<=64-first;++count){
  uint64_t expected=0;for(int i=0;i<count;++i)expected|=uint64_t{1}<<(first+i);
  assert(UxrInputColumnBits(first,count)==expected);
 }
 for(size_t pixels:{1,7,8,9,63,64,65,127,128,129,257,1024}){
  for(size_t first=0;first<=pixels;++first){
   for(size_t request:{0,1,2,7,8,9,16,63,64,1024}){
    size_t count=std::min(request,pixels-first);std::vector<uint8_t> actual,expected;
    if(count)expected.resize((pixels+7)/8);
    for(size_t i=first;i<first+count;++i)expected[i/8]|=1u<<(i%8);
    UxrSetInputMaskRange(actual,first,count,pixels);assert(actual==expected);
    UxrSetInputMaskRange(actual,first,count,pixels);assert(actual==expected);
    if(pixels>2){
      if(expected.empty())expected.resize((pixels+7)/8);
      for(size_t i=1;i<pixels-1;++i)expected[i/8]|=1u<<(i%8);
      UxrSetInputMaskRange(actual,1,pixels-2,pixels);assert(actual==expected);
    }
   }
  }
 }
}
''')

    def test_input_mask_preserves_only_selected_pixels_and_keeps_seed_mapping(self):
        self.build_and_run('mask',r'''
#include "base/uxr_graphics_noise.h"
#include <algorithm>
#include <array>
#include <cassert>
#include <vector>
int main(){
 constexpr unsigned w=128,h=64;
 std::vector<unsigned char> input(w*h*4);
 for(unsigned p=0;p<w*h;++p){input[p*4]=(p*17+31)&255;input[p*4+1]=(p*37+66)&255;input[p*4+2]=(p*13+123)&255;input[p*4+3]=255;}
 for(auto mode:{base::UxrSurfaceMode::kChromix,base::UxrSurfaceMode::kShardX,base::UxrSurfaceMode::kCloak}){
  base::UxrSurfacePolicy policy;policy.mode=mode;policy.seed=314159;
  auto plain=input,masked=input;
  base::UxrPixelRegion full{plain,w*4,w,h,w,h};
  full.neighborhood={input,w*4,w,h,0,0};
  assert(base::UxrApplyGraphicsNoiseV1(policy,base::UxrPixelSurface::kCanvas2D,full)==base::UxrPixelResult::kApplied);
  std::vector<unsigned char> bits(w*h/8,0);
  for(unsigned p=0;p<w*h;++p)if(p%3==0)bits[p/8]|=1u<<(p%8);
  auto region=full;region.bytes=masked;region.preserved_inputs=bits;
  base::UxrApplyGraphicsNoiseV1(policy,base::UxrPixelSurface::kCanvas2D,region);
  for(unsigned p=0;p<w*h;++p)for(unsigned c=0;c<4;++c)assert(masked[p*4+c]==(p%3==0?input[p*4+c]:plain[p*4+c]));
  auto repeat=input;region.bytes=repeat;base::UxrApplyGraphicsNoiseV1(policy,base::UxrPixelSurface::kCanvas2D,region);assert(repeat==masked);
  std::fill(bits.begin(),bits.end(),255);masked=input;region.bytes=masked;
  assert(base::UxrApplyGraphicsNoiseV1(policy,base::UxrPixelSurface::kCanvas2D,region)==base::UxrPixelResult::kUnchanged);assert(masked==input);
  std::array<unsigned char,1> short_mask{255};region.preserved_inputs=short_mask;
  assert(base::UxrApplyGraphicsNoiseV1(policy,base::UxrPixelSurface::kCanvas2D,region)==base::UxrPixelResult::kInvalidLayout);assert(masked==input);
 }
}
''')

if __name__=='__main__':unittest.main()
