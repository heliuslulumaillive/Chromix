"""Actual DirectWrite views from authenticated, external source selections."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest
import shutil

from tools.tests import test_canvas_input_mask as host
from tools.generate_private_font_packages import render

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.name == 'nt', 'Windows CNG and compiler required')
class SignedSourceCatalogFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        host.CanvasInputMaskTests.setUpClass.__func__(cls)
        (cls.root / 'base/uxr_private_font_packages_win.h').write_text(render(controlled=True), encoding='utf8')
        fixtures = ROOT / 'tools/tests/fixtures/signed-font-catalog-v3'
        cls.generations = {}
        for label in ['a', 'b', 'trailing', 'unsafe-source', 'missing-core']:
            payload = (fixtures / (label + '.bin')).read_bytes()
            folder = cls.root / label / hashlib.sha256(payload).hexdigest()
            folder.mkdir(parents=True)
            (folder / 'catalog.bin').write_bytes(payload)
            shutil.copyfile(fixtures / (label + '.sig'), folder / 'catalog.sig')
            cls.generations[label] = folder
        source = r'''
#include "base/uxr_private_font_packages_win.h"
#include <cassert>
#include <cstdlib>
using namespace base::uxr_private_fonts_v1;
int wmain(int argc,wchar_t**argv){assert(argc==5);std::wstring wide=argv[2];std::string group(wide.begin(),wide.end());
 std::shared_ptr<CatalogSelection> selection;const HRESULT result=LoadCountryCatalog(argv[1],group,&selection);
 const size_t count=static_cast<size_t>(_wtoi(argv[3]));
 if(!count){assert(FAILED(result)&&!selection);return 0;}
 assert(SUCCEEDED(result)&&selection&&selection->native_sources.size()==count);
 assert(selection->assets.size()==static_cast<size_t>(_wtoi(argv[4])));
 for(const auto* name:{L"catalog.bin",L"catalog.sig"}){const std::wstring file=std::wstring(argv[1])+L"\\"+name;
 HANDLE write=CreateFileW(file.c_str(),GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,0,nullptr);assert(write==INVALID_HANDLE_VALUE);}}
'''
        unit, cls.binary = cls.root / 'source-catalog.cc', cls.root / 'source-catalog.exe'
        unit.write_text(source, encoding='utf8')
        compiled = subprocess.run([cls.compiler, '-std=c++20', '-O2', '-I' + str(cls.root),
                                   str(unit), '-o', str(cls.binary), '-ldwrite', '-lbcrypt'],
                                  env=cls.env, capture_output=True, text=True, timeout=120)
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr

    def probe(self, label, group, count):
        document = ROOT / 'tools/tests/fixtures/signed-font-catalog-v3' / (label + '.json')
        assets = len(json.loads(document.read_bytes())['groups'].get(group, [])) if document.exists() else 0
        result = subprocess.run([str(self.binary), str(self.generations[label]), group, str(count), str(assets)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_same_compiled_loader_accepts_new_country_group_and_source(self):
        before = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.probe('a', 'sample-a', 1)
        self.probe('a', 'sample-b', 1)
        self.probe('b', 'sample-b', 2)
        self.assertEqual(hashlib.sha256(self.binary.read_bytes()).hexdigest(), before)

    def test_signatures_do_not_authorize_unsafe_sources_or_missing_common_sources(self):
        for label in ['trailing', 'unsafe-source', 'missing-core']:
            self.probe(label, 'sample-b', 0)


@unittest.skipUnless(os.name == 'nt' and os.environ.get('CHROMIX_SIGNED_CONTROLLED_STORE'),
                     'Requires reviewed native Windows sources and the signed v3 store')
class SignedSourceFontCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        host.CanvasInputMaskTests.setUpClass.__func__(cls)
        (cls.root / 'base/uxr_private_font_packages_win.h').write_text(render(controlled=True), encoding='utf8')
        cls.store = Path(os.environ['CHROMIX_SIGNED_CONTROLLED_STORE']).resolve()
        pointer = json.loads((cls.store / 'current.json').read_text('utf8'))
        cls.generation = cls.store / 'catalogs' / pointer['catalogSHA256']
        cls.document = json.loads((ROOT / 'build/controlled-external-font-resources.json').read_text('utf8'))

    def compile(self, name, source):
        unit, binary = self.root / (name + '.cc'), self.root / (name + '.exe')
        unit.write_text(source, encoding='utf8')
        result = subprocess.run([self.compiler, '-std=c++20', '-O2', '-I' + str(self.root),
                                 str(unit), '-o', str(binary), '-ldwrite', '-lbcrypt'],
                                env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return binary

    def test_all_groups_keep_only_authenticated_sources_and_safe_fallback(self):
        groups = ','.join(json.dumps(g) for g in sorted(self.document['groups']))
        cn_group = self.document['countries']['CN']
        source = r'''
#include "base/uxr_private_font_packages_win.h"
#include <cassert>
#include <iostream>
using namespace base::uxr_private_fonts_v1;
using Microsoft::WRL::ComPtr;
bool Has(IDWriteFontCollection*c,const wchar_t*n){UINT32 i=0;BOOL f=FALSE;assert(SUCCEEDED(c->FindFamilyName(n,&i,&f)));return f;}
int wmain(int argc,wchar_t**argv){
 assert(argc==3);ComPtr<IDWriteFactory> factory;
 assert(SUCCEEDED(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,__uuidof(IDWriteFactory),reinterpret_cast<IUnknown**>(factory.GetAddressOf()))));
 ComPtr<IDWriteFontCollection> native;assert(SUCCEEDED(factory->GetSystemFontCollection(&native)));
 const auto original=native->GetFontFamilyCount();
 for(const char* group:{GROUPS}){
  const bool cn=std::string_view(group)==CN_GROUP;
  std::shared_ptr<CatalogSelection> catalog;assert(SUCCEEDED(LoadCountryCatalog(argv[2],group,&catalog)));
  assert(catalog&&catalog->native_sources.size()==(cn?159u:151u));
  std::shared_ptr<Resources> additions;assert(SUCCEEDED(VerifyCatalogAssets(argv[1],*catalog,&additions)));
  std::shared_ptr<NativeViewSnapshot> snapshot;ComPtr<IDWriteFontCollection> selected;
  const HRESULT hr=CreateControlledCollection(factory.Get(),catalog->native_sources,additions,&snapshot,&selected);
  if(FAILED(hr)){std::cerr<<group<<" HRESULT="<<std::hex<<hr<<"\n";return 2;}
  assert(snapshot&&!snapshot->additions_rejected&&snapshot->paths.size()==(cn?155u:147u));
  for(const auto* n:{L"FangSong",L"KaiTi",L"SimHei",L"DengXian",L"Noto Sans SC",L"Noto Serif SC"})assert(Has(selected.Get(),n)==cn);
  for(const auto* n:{L"Agency FB",L"STSong",L"FZShuTi",L"Maple Mono Normal NF CN",L"Arial Narrow"})assert(!Has(selected.Get(),n));
  for(const auto* n:{L"Arial",L"SimSun",L"Microsoft YaHei",L"Segoe UI Emoji",L"Cambria Math",L"Cascadia Code",L"Cascadia Mono",L"Noto Sans",L"Noto Serif",L"Noto Sans Mono"})assert(Has(selected.Get(),n));
  for(const auto& path:snapshot->paths){HANDLE write=CreateFileW(path.c_str(),GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,0,nullptr);assert(write==INVALID_HANDLE_VALUE);}
  auto invalid=std::make_shared<Resources>();invalid->fonts.push_back(L"C:\\Missing-controlled-font.otf");
  std::shared_ptr<NativeViewSnapshot> kept;ComPtr<IDWriteFontCollection> fallback;
  assert(SUCCEEDED(CreateControlledCollection(factory.Get(),catalog->native_sources,invalid,&kept,&fallback)));
  assert(kept&&kept->additions_rejected&&kept->paths.size()==snapshot->paths.size());
  assert(!Has(fallback.Get(),L"Agency FB")&&!Has(fallback.Get(),L"Noto Sans")&&Has(fallback.Get(),L"FangSong")==cn);
  std::cout<<group<<" controlled_files="<<snapshot->paths.size()<<"\n";
 }
 ComPtr<IDWriteFontCollection> after;assert(SUCCEEDED(factory->GetSystemFontCollection(&after,TRUE)));
 assert(after->GetFontFamilyCount()==original&&Has(after.Get(),L"Agency FB"));
}
'''.replace('GROUPS', groups).replace('CN_GROUP', json.dumps(cn_group))
        binary = self.compile('signed-controlled-views', source)
        before = hashlib.sha256(binary.read_bytes()).hexdigest()
        result = subprocess.run([str(binary), str(self.store / 'objects'), str(self.generation)],
                                capture_output=True, text=True, timeout=240)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), before)
        print(result.stdout)

    def test_required_source_mismatch_does_not_restore_complete_host_fonts(self):
        binary = self.compile('signed-required-source-mismatch', r'''
#include "base/uxr_private_font_packages_win.h"
#include <cassert>
using namespace base::uxr_private_fonts_v1;using Microsoft::WRL::ComPtr;
int wmain(int argc,wchar_t**argv){assert(argc==2);std::shared_ptr<CatalogSelection> c;
 assert(SUCCEEDED(LoadCountryCatalog(argv[1],"not-listed",&c))&&c->assets.empty());
 ComPtr<IDWriteFactory> f;assert(SUCCEEDED(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,__uuidof(IDWriteFactory),reinterpret_cast<IUnknown**>(f.GetAddressOf()))));
 std::shared_ptr<NativeViewSnapshot> fallback;ComPtr<IDWriteFontCollection> base;
 assert(SUCCEEDED(CreateControlledCollection(f.Get(),c->native_sources,nullptr,&fallback,&base))&&fallback->paths.size()==147);
 UINT32 index=0;BOOL present=FALSE;assert(SUCCEEDED(base->FindFamilyName(L"Agency FB",&index,&present))&&!present);
 c->native_sources[0].hash=std::string(64,'0');
 std::shared_ptr<NativeViewSnapshot> kept;ComPtr<IDWriteFontCollection> fonts;
 assert(FAILED(CreateControlledCollection(f.Get(),c->native_sources,nullptr,&kept,&fonts))&&!kept&&!fonts);}
''')
        result = subprocess.run([str(binary), str(self.generation)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
