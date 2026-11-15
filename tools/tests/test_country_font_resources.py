"""Exercise the compiled native resource helper against actual selected files."""
import os
import json
from pathlib import Path
import subprocess
import unittest
from tools.tests import test_canvas_input_mask as host
from tools.generate_private_font_packages import render


@unittest.skipUnless(os.environ.get('CHROMIX_COUNTRY_FONT_OBJECTS'), 'Actual shared font resource store required')
class CountryFontResourcesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        host.CanvasInputMaskTests.setUpClass.__func__(cls)

    def test_actual_selected_groups_are_read_locked_and_preserve_native_fonts(self):
        self.assertEqual((self.root/'base/uxr_private_font_packages_win.h').read_text('utf-8'), render())
        source = r'''
#include "base/uxr_private_font_packages_win.h"
#include <cassert>
#include <iostream>
using Microsoft::WRL::ComPtr;
int wmain(int argc,wchar_t**argv) {
  assert(argc == 2);
  ComPtr<IDWriteFactory> factory;
  assert(SUCCEEDED(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,__uuidof(IDWriteFactory),reinterpret_cast<IUnknown**>(factory.GetAddressOf()))));
  ComPtr<IDWriteFontCollection> system;
  assert(SUCCEEDED(factory->GetSystemFontCollection(&system)));
  const auto baseline=system->GetFontFamilyCount();
  ComPtr<IDWriteFontCollection1> baseline1;assert(SUCCEEDED(system.As(&baseline1)));
  ComPtr<IDWriteFontSet> baselineSet;assert(SUCCEEDED(baseline1->GetFontSet(&baselineSet)));
  struct Case {const char* group;size_t fontCount;std::vector<const wchar_t*> families;};
  for(const auto test:{/* COUNTRY_CASES */}) {
    const auto group=test.group;
    std::shared_ptr<base::uxr_private_fonts_v1::Resources> files;
    assert(SUCCEEDED(base::uxr_private_fonts_v1::VerifyCountry(argv[1],group,&files)));
    assert(files && files->fonts.size() == test.fontCount);
    for(const auto& font:files->fonts) {
      HANDLE writable=CreateFileW(font.c_str(),GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,0,nullptr);
      assert(writable==INVALID_HANDLE_VALUE);
    }
    ComPtr<IDWriteFontCollection> selected;
    assert(SUCCEEDED(base::uxr_private_fonts_v1::CreateCollection(factory.Get(),files,&selected)));
    for(const auto name:test.families) {
      UINT32 index=0;BOOL exists=FALSE;
      assert(SUCCEEDED(selected->FindFamilyName(name,&index,&exists)) && exists);
    }
    ComPtr<IDWriteFontCollection1> selected1;assert(SUCCEEDED(selected.As(&selected1)));
    ComPtr<IDWriteFontSet> selectedSet;assert(SUCCEEDED(selected1->GetFontSet(&selectedSet)));
    for(UINT32 index=0;index<selectedSet->GetFontCount();++index) {
      ComPtr<IDWriteFontFaceReference> ref;assert(SUCCEEDED(selectedSet->GetFontFaceReference(index,&ref)));
      UINT32 oldIndex=0;BOOL inNative=FALSE;
      assert(SUCCEEDED(baselineSet->FindFontFaceReference(ref.Get(),&oldIndex,&inNative)));
      if(inNative)continue;
      ComPtr<IDWriteFontFace3> face;assert(SUCCEEDED(ref->CreateFontFace(&face)));
      UINT32 count=1;ComPtr<IDWriteFontFile> fontFile;assert(SUCCEEDED(face->GetFiles(&count,&fontFile))&&count==1);
      ComPtr<IDWriteFontFileLoader> loader;assert(SUCCEEDED(fontFile->GetLoader(&loader)));
      ComPtr<IDWriteLocalFontFileLoader> local;assert(SUCCEEDED(loader.As(&local)));
    }
    for(const auto name:{L"SitkaBanner",L"SitkaBanner-Bold",L"SitkaSmall"}) {
      DWRITE_FONT_PROPERTY property={DWRITE_FONT_PROPERTY_ID_POSTSCRIPT_NAME,name,L""};
      ComPtr<IDWriteFontSet> before,after;
      assert(SUCCEEDED(baselineSet->GetMatchingFonts(&property,1,&before)));
      assert(SUCCEEDED(selectedSet->GetMatchingFonts(&property,1,&after)));
      assert(before->GetFontCount()==after->GetFontCount());
    }
    for(UINT32 i=0;i<baseline;++i) {
      ComPtr<IDWriteFontFamily> family;ComPtr<IDWriteLocalizedStrings> names;
      assert(SUCCEEDED(system->GetFontFamily(i,&family)));
      assert(SUCCEEDED(family->GetFamilyNames(&names)));
      UINT32 length=0;assert(SUCCEEDED(names->GetStringLength(0,&length)));
      std::vector<wchar_t> name(length+1);assert(SUCCEEDED(names->GetString(0,name.data(),length+1)));
      UINT32 index=0;BOOL exists=FALSE;
      assert(SUCCEEDED(selected->FindFamilyName(name.data(),&index,&exists)) && exists);
    }
    std::cout<<group<<" selected_files="<<files->fonts.size()<<" families="<<selected->GetFontFamilyCount()<<"\n";
  }
  std::shared_ptr<base::uxr_private_fonts_v1::Resources> invalid;
  assert(FAILED(base::uxr_private_fonts_v1::VerifyCountry(argv[1],"all",&invalid)) && !invalid);
  assert(FAILED(base::uxr_private_fonts_v1::VerifyCountry(L"relative","ja",&invalid)) && !invalid);
  ComPtr<IDWriteFontCollection> unchanged;
  assert(SUCCEEDED(factory->GetSystemFontCollection(&unchanged,TRUE)) && unchanged->GetFontFamilyCount()==baseline);
  std::cout<<"native_inventory_unchanged_and_unknown_group_rejected\n";
}
'''
        manifest=json.loads((Path(__file__).resolve().parents[2]/'build/country-font-resources.json').read_text('utf-8'))
        cases=[]
        for name,assets in sorted(manifest['groups'].items()):
            families=manifest.get('groupFamilies',{}).get(name,[])
            cases.append('Case{'+json.dumps(name)+','+str(sum(a['kind']=='font' for a in assets))+',{'+','.join('L'+json.dumps(f) for f in families)+'}}')
        source=source.replace('/* COUNTRY_CASES */',','.join(cases))
        unit=self.root/'country-font-resources.cc'; binary=self.root/'country-font-resources.exe'
        unit.write_text(source,encoding='utf-8',newline='\n')
        result=subprocess.run([self.compiler,'-std=c++20','-O2','-I'+str(self.root),str(unit),'-o',str(binary),'-ldwrite','-lbcrypt'],env=self.env,capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result=subprocess.run([str(binary),os.environ['CHROMIX_COUNTRY_FONT_OBJECTS']],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        print(result.stdout)


if __name__=='__main__': unittest.main()
