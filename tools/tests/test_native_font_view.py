"""Actual source-filtered DirectWrite collections; no Windows font changes."""
import json,os,subprocess,unittest
from pathlib import Path
from tools.tests import test_canvas_input_mask as host
from tools.generate_private_font_packages import render
ROOT=Path(__file__).resolve().parents[2]
@unittest.skipUnless(os.environ.get('CHROMIX_NATIVE_FONT_VIEW_HOST')=='1','Requires the reviewed Windows 25H2 font source variants')
class NativeFontViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):host.CanvasInputMaskTests.setUpClass.__func__(cls)
    def compile(self,name,source):
        unit=self.root/(name+'.cc');binary=self.root/(name+'.exe');unit.write_text(source,encoding='utf8')
        r=subprocess.run([self.compiler,'-std=c++20','-O2','-I'+str(self.root),str(unit),'-o',str(binary),'-ldwrite','-lbcrypt'],env=self.env,capture_output=True,text=True,timeout=120)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr);return binary
    def test_all_country_additions_use_the_same_filtered_native_boundary(self):
        store=os.environ.get('CHROMIX_COUNTRY_FONT_OBJECTS')
        if not store:self.skipTest('Actual managed resource objects required')
        groups=json.loads((ROOT/'build/country-font-resources.json').read_text('utf8'))['groups']
        cases=','.join('"'+name+'"' for name in sorted(groups))
        source=r'''
#include "base/uxr_private_font_packages_win.h"
#include <cassert>
#include <iostream>
using namespace base::uxr_private_fonts_v1;using Microsoft::WRL::ComPtr;
bool Has(IDWriteFontCollection*c,const wchar_t*n){UINT32 i=0;BOOL found=FALSE;assert(SUCCEEDED(c->FindFamilyName(n,&i,&found)));return found;}
int wmain(int argc,wchar_t**argv){assert(argc==2);ComPtr<IDWriteFactory> factory;assert(SUCCEEDED(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,__uuidof(IDWriteFactory),reinterpret_cast<IUnknown**>(factory.GetAddressOf()))));
 for(const char* group:{GROUP_CASES}){
  const bool cn=std::string_view(group)=="hans-cn";std::shared_ptr<Resources> additions;assert(SUCCEEDED(VerifyCountry(argv[1],group,&additions)));std::shared_ptr<NativeViewSnapshot> snapshot;ComPtr<IDWriteFontCollection> selected;
  assert(SUCCEEDED(CreateControlledCollection(factory.Get(),cn,additions,&snapshot,&selected)));assert(snapshot&&!snapshot->additions_rejected&&snapshot->paths.size()==(cn?155u:147u));
  for(const auto* name:{L"FangSong",L"KaiTi",L"SimHei",L"DengXian",L"Noto Sans SC",L"Noto Serif SC"})assert(Has(selected.Get(),name)==cn);
  for(const auto* name:{L"Agency FB",L"STSong",L"FZShuTi",L"Maple Mono Normal NF CN",L"Arial Narrow"})assert(!Has(selected.Get(),name));
  for(const auto* name:{L"Arial",L"SimSun",L"Microsoft YaHei",L"Segoe UI Emoji",L"Cambria Math",L"Cascadia Code",L"Cascadia Mono",L"Noto Sans",L"Noto Serif",L"Noto Sans Mono"})assert(Has(selected.Get(),name));
  std::cout<<group<<" filtered_native_files="<<snapshot->paths.size()<<"\n";
 }
}
'''.replace('GROUP_CASES',cases)
        binary=self.compile('country-filtered',source);r=subprocess.run([str(binary),store],capture_output=True,text=True,timeout=180)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr);print(r.stdout)
    def test_native_source_selection_and_cn_only_module(self):
        self.assertEqual((self.root/'base/uxr_private_font_packages_win.h').read_text('utf8'),render())
        source=r'''
#include "base/uxr_private_font_packages_win.h"
#include <cassert>
#include <iostream>
using namespace base::uxr_private_fonts_v1;
using Microsoft::WRL::ComPtr;
bool Has(IDWriteFontCollection* collection,const wchar_t* name){UINT32 i=0;BOOL found=FALSE;assert(SUCCEEDED(collection->FindFamilyName(name,&i,&found)));return found;}
int main(){
 ComPtr<IDWriteFactory> factory;assert(SUCCEEDED(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,__uuidof(IDWriteFactory),reinterpret_cast<IUnknown**>(factory.GetAddressOf()))));
 ComPtr<IDWriteFontCollection> native;assert(SUCCEEDED(factory->GetSystemFontCollection(&native)));const auto original=native->GetFontFamilyCount();
 for(bool cn:{false,true}){
  std::shared_ptr<NativeViewSnapshot> kept;ComPtr<IDWriteFontCollection> selected;
  const HRESULT hr=CreateControlledCollection(factory.Get(),cn,nullptr,&kept,&selected);
  if(FAILED(hr)){std::cerr<<"controlled_hresult="<<std::hex<<hr<<"\n";return 1;}
  assert(kept && kept->paths.size()==(cn?155u:147u));
  for(const auto* name:{L"Arial",L"Arial Black",L"Microsoft YaHei",L"Microsoft JhengHei",L"SimSun",L"NSimSun",L"MS Gothic",L"Yu Gothic UI",L"Malgun Gothic",L"Cambria Math",L"Segoe UI Emoji",L"Cascadia Code",L"Cascadia Mono"})assert(Has(selected.Get(),name)==Has(native.Get(),name));
  for(const auto* name:{L"FangSong",L"KaiTi",L"SimHei",L"DengXian",L"Noto Sans SC",L"Noto Serif SC"})assert(Has(selected.Get(),name)==cn);
  for(const auto* name:{L"STSong",L"FZShuTi",L"Agency FB",L"Maple Mono Normal NF CN",L"Arial Narrow"})assert(!Has(selected.Get(),name));
  for(const auto& path:kept->paths){
   if(SameNativePath(NativeLeaf(path),L"CascadiaCode.ttf")||SameNativePath(NativeLeaf(path),L"CascadiaMono.ttf"))assert(!SameNativePath(NativeLeaf(NativeParent(path)),L"Fonts"));
  }
  ComPtr<IDWriteFontCollection1> selected1;ComPtr<IDWriteFontSet> set;assert(SUCCEEDED(selected.As(&selected1))&&SUCCEEDED(selected1->GetFontSet(&set)));
  if(!cn)assert(set->GetFontCount()==252);
  auto missing=std::make_shared<Resources>();missing->fonts.push_back(L"C:\\Windows\\Fonts\\DefinitelyAbsentFont-PrivateView.otf");
  std::shared_ptr<NativeViewSnapshot> fallback;ComPtr<IDWriteFontCollection> base_only;
  assert(SUCCEEDED(CreateControlledCollection(factory.Get(),cn,missing,&fallback,&base_only)));
  assert(fallback&&fallback->additions_rejected&&fallback->paths.size()==kept->paths.size());
  assert(Has(base_only.Get(),L"FangSong")==cn&&!Has(base_only.Get(),L"Agency FB"));
  std::cout<<"cn="<<cn<<" files="<<kept->paths.size()<<" faces="<<set->GetFontCount()<<"\n";
 }
 ComPtr<IDWriteFontCollection> after;assert(SUCCEEDED(factory->GetSystemFontCollection(&after,TRUE)));assert(after->GetFontFamilyCount()==original);assert(Has(after.Get(),L"FangSong"));assert(Has(after.Get(),L"Agency FB"));
 std::cout<<"native_global_collection_unchanged\n";
}
'''
        binary=self.compile('native-view',source);r=subprocess.run([str(binary)],capture_output=True,text=True,timeout=120)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr);print(r.stdout)
    def test_unreviewed_required_source_is_rejected(self):
        header=self.root/'base/uxr_private_font_packages_win.h';text=header.read_text('utf8');view=json.loads((ROOT/'build/native-font-view.json').read_text('utf8'))
        sha=view['core'][0]['sha256'];self.assertIn(sha,text);header.write_text(text.replace(sha,'0'*64),encoding='utf8')
        binary=self.compile('wrong-source',r'''
#include "base/uxr_private_font_packages_win.h"
#include <cassert>
using Microsoft::WRL::ComPtr;
int main(){ComPtr<IDWriteFactory> f;assert(SUCCEEDED(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,__uuidof(IDWriteFactory),reinterpret_cast<IUnknown**>(f.GetAddressOf()))));std::shared_ptr<base::uxr_private_fonts_v1::NativeViewSnapshot> kept;ComPtr<IDWriteFontCollection> out;assert(FAILED(base::uxr_private_fonts_v1::CreateControlledCollection(f.Get(),false,nullptr,&kept,&out)));assert(!kept&&!out);}
''')
        r=subprocess.run([str(binary)],capture_output=True,text=True,timeout=120);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
if __name__=='__main__':unittest.main()
