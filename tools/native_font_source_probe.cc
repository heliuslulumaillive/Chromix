#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <dwrite_3.h>
#include <wrl/client.h>
#include <iostream>
#include <map>
#include <string>
#include <vector>
using Microsoft::WRL::ComPtr;
std::string Utf8(const std::wstring& value) {
  int length=WideCharToMultiByte(CP_UTF8,0,value.data(),static_cast<int>(value.size()),nullptr,0,nullptr,nullptr);
  std::string out(length,'\0');
  WideCharToMultiByte(CP_UTF8,0,value.data(),static_cast<int>(value.size()),out.data(),length,nullptr,nullptr);
  return out;
}
std::string Quoted(const std::wstring& value) {
  std::string out="\"";
  for (char ch:Utf8(value)) {if(ch=='\\'||ch=='\"')out+='\\';out+=ch;}
  return out+'\"';
}
int wmain() {
  ComPtr<IDWriteFactory> factory;
  HRESULT hr=DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,__uuidof(IDWriteFactory),reinterpret_cast<IUnknown**>(factory.GetAddressOf()));
  if(FAILED(hr))return 1;
  ComPtr<IDWriteFontCollection> collection;hr=factory->GetSystemFontCollection(&collection);if(FAILED(hr))return 2;
  ComPtr<IDWriteFontCollection1> collection1;hr=collection.As(&collection1);if(FAILED(hr))return 3;
  ComPtr<IDWriteFontSet> set;hr=collection1->GetFontSet(&set);if(FAILED(hr))return 4;
  std::map<std::wstring,unsigned> paths;unsigned rejected=0;
  for(UINT32 index=0;index<set->GetFontCount();++index) {
    ComPtr<IDWriteFontFaceReference> ref;ComPtr<IDWriteFontFace3> face;
    if(FAILED(set->GetFontFaceReference(index,&ref))||FAILED(ref->CreateFontFace(&face))){++rejected;continue;}
    UINT32 count=1;ComPtr<IDWriteFontFile> file;
    if(FAILED(face->GetFiles(&count,&file))||count!=1){++rejected;continue;}
    ComPtr<IDWriteFontFileLoader> loader;ComPtr<IDWriteLocalFontFileLoader> local;
    const void* key=nullptr;UINT32 key_size=0,length=0;
    if(FAILED(file->GetLoader(&loader))||FAILED(loader.As(&local))||FAILED(file->GetReferenceKey(&key,&key_size))||FAILED(local->GetFilePathLengthFromKey(key,key_size,&length))){++rejected;continue;}
    std::vector<wchar_t> path(length+1);
    if(FAILED(local->GetFilePathFromKey(key,key_size,path.data(),length+1))){++rejected;continue;}
    ++paths[std::wstring(path.data(),length)];
  }
  std::cout<<"{\"nativeFaces\":"<<set->GetFontCount()<<",\"nonLocalOrUnreadableFaces\":"<<rejected<<",\"files\":[";
  bool comma=false;
  for(const auto& [path,faces]:paths){if(comma)std::cout<<',';comma=true;std::cout<<"{\"path\":"<<Quoted(path)<<",\"faces\":"<<faces<<'}';}
  std::cout<<"]}\n";
  return rejected?5:0;
}
