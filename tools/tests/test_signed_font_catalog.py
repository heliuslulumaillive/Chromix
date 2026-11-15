"""Real CNG verification, DirectWrite loading and updates without recompilation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from tools.tests import test_canvas_input_mask as host
from tools.generate_private_font_packages import render

ROOT=Path(__file__).resolve().parents[2]
FIXTURES=ROOT/'tools/tests/fixtures/signed-font-catalog-v2'


@unittest.skipUnless(os.name=='nt','Native Windows CNG and DirectWrite required')
class SignedFontCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        host.CanvasInputMaskTests.setUpClass.__func__(cls)
        cls.objects=cls.root/'objects';cls.objects.mkdir()
        source=Path(os.environ.get('CHROMIX_PRIVATE_FONT_ASSETS',r'G:\Shardxlite\tests\font-packs-candidate-20261114\private-fonts'))
        manifest=json.loads((ROOT/'build/private-font-packages.json').read_bytes())
        needed={a['sha256']:a for f in ['a','b'] for assets in json.loads((FIXTURES/(f+'.json')).read_bytes())['groups'].values() for a in assets}
        for row in [*manifest['fonts'],*manifest['licenses']]:
            if row['sha256'] not in needed:continue
            file=source/row['file'];content=file.read_bytes()
            assert len(content)==row['size'] and hashlib.sha256(content).hexdigest()==row['sha256']
            shutil.copyfile(file,cls.objects/(row['sha256']+'.blob'))
        cls.generations={}
        for label in ['a','b','trailing']:
            payload=(FIXTURES/(label+'.bin')).read_bytes();identity=hashlib.sha256(payload).hexdigest()
            directory=cls.root/label/identity;directory.mkdir(parents=True)
            (directory/'catalog.bin').write_bytes(payload);(directory/'catalog.sig').write_bytes((FIXTURES/(label+'.sig')).read_bytes())
            cls.generations[label]=directory
        source=r'''
#include "base/uxr_private_font_packages_win.h"
#include <cassert>
#include <cstdlib>
#include <iostream>
using namespace base::uxr_private_fonts_v1;
int wmain(int argc,wchar_t** argv) {
  assert(argc==5);const size_t expected=static_cast<size_t>(_wtoi(argv[4]));
  std::wstring wide=argv[3];std::string group(wide.begin(),wide.end());
  std::shared_ptr<Resources> files;
  const HRESULT result=VerifyCountry(argv[1],group,argv[2],&files);
  if(!expected){assert(FAILED(result)&&!files);std::cout<<"rejected\n";return 0;}
  assert(SUCCEEDED(result)&&files&&files->fonts.size()==expected);
  for(const auto& file:files->fonts){HANDLE writable=CreateFileW(file.c_str(),GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,0,nullptr);assert(writable==INVALID_HANDLE_VALUE);}
  for(const auto name:{L"catalog.bin",L"catalog.sig"}){const std::wstring file=std::wstring(argv[2])+L"\\"+name;HANDLE writable=CreateFileW(file.c_str(),GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,0,nullptr);assert(writable==INVALID_HANDLE_VALUE);}
  Microsoft::WRL::ComPtr<IDWriteFactory> factory;
  assert(SUCCEEDED(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,__uuidof(IDWriteFactory),reinterpret_cast<IUnknown**>(factory.GetAddressOf()))));
  Microsoft::WRL::ComPtr<IDWriteFontCollection> native,combined,unchanged;
  assert(SUCCEEDED(factory->GetSystemFontCollection(&native)));
  assert(SUCCEEDED(CreateCollection(factory.Get(),files,&combined)));
  assert(combined->GetFontFamilyCount()>=native->GetFontFamilyCount());
  assert(SUCCEEDED(factory->GetSystemFontCollection(&unchanged,TRUE))&&unchanged->GetFontFamilyCount()==native->GetFontFamilyCount());
  std::cout<<"accepted fonts="<<files->fonts.size()<<"\n";
}
'''
        assert (cls.root/'base/uxr_private_font_packages_win.h').read_text('utf-8')==render()
        unit=cls.root/'signed-catalog.cc';unit.write_text(source,encoding='utf-8',newline='\n');cls.binary=cls.root/'signed-catalog.exe'
        result=subprocess.run([cls.compiler,'-std=c++20','-O2','-I'+str(cls.root),str(unit),'-o',str(cls.binary),'-ldwrite','-lbcrypt'],env=cls.env,capture_output=True,text=True,timeout=120)
        assert result.returncode==0,result.stdout+result.stderr

    def probe(self,catalogue,group,expected,objects=None):
        result=subprocess.run([str(self.binary),str(objects or self.objects),str(catalogue),group,str(expected)],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result.stdout.strip()

    def test_signed_external_update_adds_country_group_and_font_to_unchanged_binary(self):
        before=hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.assertIn('fonts=1',self.probe(self.generations['a'],'sample-a',1))
        self.assertEqual(self.probe(self.generations['a'],'sample-b',0),'rejected')
        self.assertIn('fonts=2',self.probe(self.generations['b'],'sample-b',2))
        self.assertEqual(hashlib.sha256(self.binary.read_bytes()).hexdigest(),before)

    def test_even_valid_signatures_cannot_authorize_trailing_or_unknown_data(self):
        self.assertEqual(self.probe(self.generations['trailing'],'sample-b',0),'rejected')
        self.assertEqual(self.probe(self.generations['b'],'not-present',0),'rejected')

    def test_tampering_missing_assets_and_wrong_generation_fail_closed(self):
        directory=self.root/'tampered'/self.generations['b'].name;directory.mkdir(parents=True)
        for name in ['catalog.bin','catalog.sig']:shutil.copyfile(self.generations['b']/name,directory/name)
        data=bytearray((directory/'catalog.sig').read_bytes());data[0]^=1;(directory/'catalog.sig').write_bytes(data)
        self.assertEqual(self.probe(directory,'sample-b',0),'rejected')
        empty=self.root/'missing-assets';empty.mkdir()
        self.assertEqual(self.probe(self.generations['b'],'sample-b',0,empty),'rejected')
        wrong=self.root/'wrong-generation';wrong.mkdir()
        for name in ['catalog.bin','catalog.sig']:shutil.copyfile(self.generations['b']/name,wrong/name)
        self.assertEqual(self.probe(wrong,'sample-b',0),'rejected')

    def test_signed_catalog_does_not_authorize_altered_font_bytes(self):
        broken=self.root/'altered-font-bytes';broken.mkdir()
        document=json.loads((FIXTURES/'b.json').read_bytes())
        first=next(a['file'] for a in document['groups']['sample-b'] if a['kind']=='font')
        for source in self.objects.iterdir():
            target=broken/source.name
            if source.name==first:
                data=bytearray(source.read_bytes());data[0]^=1;target.write_bytes(data)
            else:os.link(source,target)
        self.assertEqual(self.probe(self.generations['b'],'sample-b',0,broken),'rejected')


if __name__=='__main__':unittest.main()
