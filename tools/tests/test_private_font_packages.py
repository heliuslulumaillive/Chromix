"""Actual DirectWrite collections and pinned-file rejection; no OS installation."""
import json,os,subprocess,tempfile,unittest
from pathlib import Path
from tools.tests import test_canvas_input_mask as host
from tools.generate_private_font_packages import render
ROOT=Path(__file__).resolve().parents[2]
class PrivateFontPackagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):host.CanvasInputMaskTests.setUpClass.__func__(cls)
    def test_actual_private_collection_and_hash_rejection(self):
        assets=Path(os.environ.get('CHROMIX_PRIVATE_FONT_ASSETS',r'G:\Shardxlite\tests\font-packs-candidate-20261114\private-fonts'))
        self.assertTrue((assets/'private-font-packages.json').is_file(),'Prepare verified private font assets before native test')
        self.assertEqual((self.root/'base/uxr_private_font_packages_win.h').read_text('utf-8'),render())
        unit=self.root/'private-font-collection.cc';binary=self.root/'private-font-collection.exe'
        bad=self.root/'bad-private-fonts';bad.mkdir();data=json.loads((assets/'private-font-packages.json').read_text('utf-8'))
        for row in [*data['fonts'],*data['licenses']]:
            content=(assets/row['file']).read_bytes()
            if row is data['fonts'][0]:content=bytes([content[0]^1])+content[1:]
            (bad/row['file']).write_bytes(content)
        (bad/'private-font-packages.json').write_bytes((assets/'private-font-packages.json').read_bytes())
        source=r'''
#include "base/uxr_private_font_packages_win.h"
#include <cassert>
#include <iostream>
using Microsoft::WRL::ComPtr;
int wmain(int argc,wchar_t**argv){
 assert(argc==3);ComPtr<IDWriteFactory> factory;
 assert(SUCCEEDED(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,__uuidof(IDWriteFactory),reinterpret_cast<IUnknown**>(factory.GetAddressOf()))));
 ComPtr<IDWriteFontCollection> before;assert(SUCCEEDED(factory->GetSystemFontCollection(&before)));
 const auto system_count=before->GetFontFamilyCount();
 std::shared_ptr<base::uxr_private_fonts_v1::Resources> files;
 assert(SUCCEEDED(base::uxr_private_fonts_v1::Verify(argv[1],&files)));
 assert(files&&files->fonts.size()==14);
 HANDLE writable=CreateFileW(files->fonts[0].c_str(),GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,0,nullptr);
 assert(writable==INVALID_HANDLE_VALUE);
 ComPtr<IDWriteFontCollection> combined;
 assert(SUCCEEDED(base::uxr_private_fonts_v1::CreateCollection(factory.Get(),files,&combined)));
 for(const auto name:{L"Noto Sans JP",L"Noto Serif JP",L"Liberation Sans",L"Liberation Serif",L"Liberation Mono"}){
  UINT32 index=0;BOOL exists=FALSE;assert(SUCCEEDED(combined->FindFamilyName(name,&index,&exists))&&exists);
  ComPtr<IDWriteFontFamily> family;assert(SUCCEEDED(combined->GetFontFamily(index,&family)));
  assert(family->GetFontCount()>=4);
  ComPtr<IDWriteFont> regular,bold;
  assert(SUCCEEDED(family->GetFirstMatchingFont(DWRITE_FONT_WEIGHT_NORMAL,DWRITE_FONT_STRETCH_NORMAL,DWRITE_FONT_STYLE_NORMAL,&regular)));
  assert(SUCCEEDED(family->GetFirstMatchingFont(DWRITE_FONT_WEIGHT_BOLD,DWRITE_FONT_STRETCH_NORMAL,DWRITE_FONT_STYLE_NORMAL,&bold)));
  assert(regular->GetWeight()==DWRITE_FONT_WEIGHT_NORMAL&&bold->GetWeight()==DWRITE_FONT_WEIGHT_BOLD);
  std::wcout<<name<<L" "<<family->GetFontCount()<<L"\n";
 }
 ComPtr<IDWriteFontCollection> unchanged;assert(SUCCEEDED(factory->GetSystemFontCollection(&unchanged,TRUE)));
 assert(unchanged->GetFontFamilyCount()==system_count);
 std::shared_ptr<base::uxr_private_fonts_v1::Resources> rejected;
 assert(FAILED(base::uxr_private_fonts_v1::Verify(argv[2],&rejected))&&!rejected);
 assert(FAILED(base::uxr_private_fonts_v1::Verify(L"relative-fonts",&rejected)));
 std::cout<<"native_system_unchanged_and_tamper_rejected\n";
}
'''
        unit.write_text(source,encoding='utf-8',newline='\n')
        result=subprocess.run([self.compiler,'-std=c++20','-O2','-I'+str(self.root),str(unit),'-o',str(binary),'-ldwrite','-lbcrypt'],env=self.env,capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result=subprocess.run([str(binary),str(assets),str(bad)],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        print(result.stdout)
if __name__=='__main__':unittest.main()
