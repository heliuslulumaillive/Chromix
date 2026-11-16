// Copyright 2026. Read-only, process-private DirectWrite font resources.
#ifndef BASE_UXR_PRIVATE_FONT_PACKAGES_WIN_H_
#define BASE_UXR_PRIVATE_FONT_PACKAGES_WIN_H_
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <array>
#include <algorithm>
#include <windows.h>

#include <bcrypt.h>
#include <cstdint>
#include <dwrite_3.h>
#include <memory>
#include <iterator>
#include <set>
#include <string>
#include <string_view>
#include <vector>
#include <utility>
#include <wrl/client.h>
namespace base::uxr_private_fonts_v1 {
struct Asset {
  const wchar_t *name;
  std::string_view sha256;
  uint64_t size;
  bool font;
};
inline constexpr Asset kAssets[] = {
  {L"LiberationMono-Bold.ttf","bd62a0672d0b9b6710b01df434c80ad54fa5f0835207eb7b17b7a761463067bb",307996ULL,true},
  {L"LiberationMono-BoldItalic.ttf","79451f3c09fe25116098853b7a2ca6e2436220ccc11af022979adbcf195be130",284068ULL,true},
  {L"LiberationMono-Italic.ttf","605c01c711b44480a7508d349dfbf3264e81fa43d69e61cfa7d10b86e764c4d1",281536ULL,true},
  {L"LiberationMono-Regular.ttf","f2b83c763e8afd21709333370bed4774337fae82267937e2b5aea7e2fbd922c1",319508ULL,true},
  {L"LiberationSans-Bold.ttf","788abee4c806d660e8aee46689dd8540cd4bb98da03dcc9d171ce3efd99a9173",414456ULL,true},
  {L"LiberationSans-BoldItalic.ttf","698da70fc191cc5f33ad4d6d3fe830fe4624b898ea2e3169955928b7c491f1ee",408996ULL,true},
  {L"LiberationSans-Italic.ttf","e5bae5c4cde31f22142753855f4f8fb86da6ff39955ed3c0a11248b0d16948b0",415816ULL,true},
  {L"LiberationSans-Regular.ttf","76d04c18ea243f426b7de1f3ad208e927008f961dc5945e5aad352d0dfde8ee8",410712ULL,true},
  {L"LiberationSerif-Bold.ttf","d754ba427cfe0bca54ae052384baa8f842da5bd6550ad4da024ac441e7a7d5ce",370096ULL,true},
  {L"LiberationSerif-BoldItalic.ttf","f17db8af71e24d2066b587546021d4f0b296be389512b658dec3c09affeb11a7",376772ULL,true},
  {L"LiberationSerif-Italic.ttf","0e3dea9f8d613e006ccfa62201f33e265d19167bd0907725c3e145368b04fc2e",375632ULL,true},
  {L"LiberationSerif-Regular.ttf","058ea80864aef09a23f45cbec2bb5400bc3dfbdea01c3f10538a21fcb497fb74",393576ULL,true},
  {L"NotoSansJP.ttf","c2f3b4d463500a2ddcd3849cded1fceeb9fd6d1c32e6cbecd568453ba50fc68f",9589900ULL,true},
  {L"NotoSerifJP.ttf","2fd527ba12b6a44ec30d796d633360da0aeba6c5d4af1304ce12bb4dc15a7dfc",13574352ULL,true},
  {L"NotoSansJP-OFL.txt","1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9",4388ULL,false},
  {L"NotoSerifJP-OFL.txt","5e0da210fb04058a8c0087985d2d456b931c2579811a49655721d3cf0c36b6d6",4350ULL,false},
  {L"Liberation-LICENSE.txt","93fed46019c38bbe566b479d22148e2e8a1e85ada614accb0211c37b2c61c19b",4414ULL,false},
  {L"private-font-packages.json","53263cba1087f0b942da56219d42544b539708a627f5f9eb8ed3491cceb2507d",7869ULL,false},
};
struct Resources {
  HANDLE directory = INVALID_HANDLE_VALUE;
  std::vector<HANDLE> handles;
  std::vector<std::wstring> fonts;
  Resources() = default;
  Resources(const Resources &) = delete;
  Resources &operator=(const Resources &) = delete;
  ~Resources() {
    for (auto h : handles)
      CloseHandle(h);
    if (directory != INVALID_HANDLE_VALUE)
      CloseHandle(directory);
  }
};
struct HashState {
  BCRYPT_ALG_HANDLE algorithm = nullptr;
  BCRYPT_HASH_HANDLE hash = nullptr;
  std::vector<unsigned char> object;
  ~HashState() {
    if (hash)
      BCryptDestroyHash(hash);
    if (algorithm)
      BCryptCloseAlgorithmProvider(algorithm, 0);
  }
};
inline bool CheckHash(HANDLE file, const Asset &asset) {
  if (asset.sha256.size() != 64)
    return false;
  LARGE_INTEGER size;
  if (!GetFileSizeEx(file, &size) || size.QuadPart < 0 ||
      static_cast<uint64_t>(size.QuadPart) != asset.size)
    return false;
  HashState state;
  DWORD length = 0, used = 0;
  if (BCryptOpenAlgorithmProvider(&state.algorithm, BCRYPT_SHA256_ALGORITHM,
                                  nullptr, 0) < 0 ||
      BCryptGetProperty(state.algorithm, BCRYPT_OBJECT_LENGTH,
                        reinterpret_cast<PUCHAR>(&length), sizeof(length),
                        &used, 0) < 0 ||
      length == 0 || length > 65536)
    return false;
  state.object.resize(length);
  if (BCryptCreateHash(state.algorithm, &state.hash, state.object.data(),
                       length, nullptr, 0, 0) < 0)
    return false;
  std::array<unsigned char, 65536> buffer;
  DWORD read = 0;
  uint64_t total = 0;
  for (;;) {
    if (!ReadFile(file, buffer.data(), static_cast<DWORD>(buffer.size()), &read,
                  nullptr))
      return false;
    if (!read)
      break;
    total += read;
    if (total > asset.size ||
        BCryptHashData(state.hash, buffer.data(), read, 0) < 0)
      return false;
  }
  std::array<unsigned char, 32> digest;
  if (total != asset.size ||
      BCryptFinishHash(state.hash, digest.data(),
                       static_cast<ULONG>(digest.size()), 0) < 0)
    return false;
  constexpr std::string_view hex = "0123456789abcdef";
  for (size_t i = 0; i < digest.size(); ++i)
    if (asset.sha256[2 * i] != hex[digest[i] >> 4] ||
        asset.sha256[2 * i + 1] != hex[digest[i] & 15])
      return false;
  return true;
}
inline HRESULT VerifyAssets(const std::wstring &supplied,
                            const Asset *assets, size_t asset_count,
                            std::shared_ptr<Resources> *result) {
  if (!result)
    return E_POINTER;
  result->reset();
  if (supplied.size() < 3 || supplied[1] != L':' ||
      (supplied[2] != L'\\' && supplied[2] != L'/'))
    return E_INVALIDARG;
  std::vector<wchar_t> full(32768);
  const DWORD count = GetFullPathNameW(
      supplied.c_str(), static_cast<DWORD>(full.size()), full.data(), nullptr);
  if (!count || count >= full.size())
    return E_INVALIDARG;
  std::wstring root(full.data(), count);
  while (root.size() > 3 && (root.back() == L'\\' || root.back() == L'/'))
    root.pop_back();
  for (size_t i = 3; i <= root.size(); ++i) {
    if (i < root.size() && root[i] != L'\\' && root[i] != L'/')
      continue;
    const DWORD flags = GetFileAttributesW(root.substr(0, i).c_str());
    if (flags == INVALID_FILE_ATTRIBUTES ||
        (flags & FILE_ATTRIBUTE_REPARSE_POINT))
      return E_ACCESSDENIED;
  }
  auto files = std::make_shared<Resources>();
  files->directory = CreateFileW(
      root.c_str(), FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE,
      nullptr, OPEN_EXISTING,
      FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
  if (files->directory == INVALID_HANDLE_VALUE)
    return HRESULT_FROM_WIN32(GetLastError());
  BY_HANDLE_FILE_INFORMATION info;
  if (!GetFileInformationByHandle(files->directory, &info) ||
      !(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) ||
      (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT))
    return E_ACCESSDENIED;
  for (size_t index = 0; index < asset_count; ++index) {
    const auto &asset = assets[index];
    const auto path = root + L"\\" + asset.name;
    HANDLE file = CreateFileW(
        path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (file == INVALID_HANDLE_VALUE)
      return HRESULT_FROM_WIN32(GetLastError());
    files->handles.push_back(file);
    if (!GetFileInformationByHandle(file, &info) ||
        (info.dwFileAttributes &
         (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY)) ||
        !CheckHash(file, asset))
      return HRESULT_FROM_WIN32(ERROR_INVALID_DATA);
    if (asset.font)
      files->fonts.push_back(path);
  }
  if (files->fonts.empty())
    return E_FAIL;
  *result = std::move(files);
  return S_OK;
}
inline HRESULT Verify(const std::wstring &supplied,
                      std::shared_ptr<Resources> *result) {
  return VerifyAssets(supplied, kAssets, sizeof(kAssets) / sizeof(kAssets[0]), result);
}
// Signed source selections; country mappings and concrete source pins stay external.
enum class NativeSourceKind { kCore, kRegional, kShared };
struct NativeSourcePin {
  std::wstring name;
  std::string hash;
  uint64_t size = 0;
  NativeSourceKind kind = NativeSourceKind::kCore;
  std::wstring package;
  Asset GetAsset() const { return {name.c_str(), hash, size, true}; }
};
// External source-controlled catalog v3: only this public trust anchor and the bounded protocol
// are compiled. Country mappings, combinations and font pins remain data.
inline constexpr unsigned char kCatalogPublicKey[] = {69,67,83,49,32,0,0,0,127,112,252,78,209,40,219,184,3,69,82,10,227,166,10,166,205,178,83,210,28,165,186,171,97,212,25,56,71,106,247,72,156,8,234,190,131,29,158,12,245,216,93,175,125,219,84,212,138,113,168,202,62,160,131,131,80,52,93,137,231,209,217,250};
inline constexpr unsigned char kCatalogMagic[] = {'S','X','F','O','N','T','3',0};
inline constexpr char kCatalogSignatureDomain[] = "ShardXFontCatalog-v3\0";
inline constexpr size_t kMaximumCatalogBytes = 1024 * 1024;

inline bool CatalogHash(const std::vector<unsigned char>& data,
                        bool signature_domain,
                        std::array<unsigned char,32>* result) {
  HashState state;
  DWORD length=0,used=0;
  if (BCryptOpenAlgorithmProvider(&state.algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0 ||
      BCryptGetProperty(state.algorithm,BCRYPT_OBJECT_LENGTH,reinterpret_cast<PUCHAR>(&length),sizeof(length),&used,0)<0 ||
      length==0 || length>65536) return false;
  state.object.resize(length);
  if (BCryptCreateHash(state.algorithm,&state.hash,state.object.data(),length,nullptr,0,0)<0) return false;
  if (signature_domain && BCryptHashData(state.hash,
      reinterpret_cast<PUCHAR>(const_cast<char*>(kCatalogSignatureDomain)),
      sizeof(kCatalogSignatureDomain)-1,0)<0) return false;
  return BCryptHashData(state.hash,const_cast<PUCHAR>(data.data()),static_cast<ULONG>(data.size()),0)>=0 &&
         BCryptFinishHash(state.hash,result->data(),static_cast<ULONG>(result->size()),0)>=0;
}

inline bool VerifyCatalogSignature(const std::vector<unsigned char>& payload,
                                   const std::vector<unsigned char>& signature) {
  if (signature.size()!=64 || payload.size()>kMaximumCatalogBytes) return false;
  std::array<unsigned char,32> digest;
  if (!CatalogHash(payload,true,&digest)) return false;
  BCRYPT_ALG_HANDLE algorithm=nullptr;
  BCRYPT_KEY_HANDLE key=nullptr;
  if (BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_ECDSA_P256_ALGORITHM,nullptr,0)<0) return false;
  const auto imported=BCryptImportKeyPair(algorithm,nullptr,BCRYPT_ECCPUBLIC_BLOB,&key,
      const_cast<PUCHAR>(kCatalogPublicKey),sizeof(kCatalogPublicKey),0);
  const bool valid=imported>=0 && BCryptVerifySignature(key,nullptr,digest.data(),static_cast<ULONG>(digest.size()),
      const_cast<PUCHAR>(signature.data()),static_cast<ULONG>(signature.size()),0)>=0;
  if (key) BCryptDestroyKey(key);
  BCryptCloseAlgorithmProvider(algorithm,0);
  return valid;
}

inline std::string CatalogHex(const unsigned char* bytes,size_t size) {
  constexpr char hex[]="0123456789abcdef";
  std::string value;value.reserve(size*2);
  for (size_t i=0;i<size;++i) {value.push_back(hex[bytes[i]>>4]);value.push_back(hex[bytes[i]&15]);}
  return value;
}

struct CatalogReader {
  const std::vector<unsigned char>& bytes;
  size_t offset=0;
  bool Read(size_t length,const unsigned char** value) {
    if (offset>bytes.size() || length>bytes.size()-offset) return false;
    *value=bytes.data()+offset;offset+=length;return true;
  }
  bool Integer(size_t width,uint64_t* result) {
    const unsigned char* value=nullptr;
    if (width>8 || !Read(width,&value)) return false;
    *result=0;for(size_t i=0;i<width;++i)*result|=static_cast<uint64_t>(value[i])<<(i*8);
    return true;
  }
};

struct CatalogFontAsset {
  std::string hash;
  std::wstring name;
  uint64_t size=0;
  bool font=false;
};

inline bool ParseCatalogGroup(const std::vector<unsigned char>& payload,
                              std::string_view requested,
                              std::vector<CatalogFontAsset>* selected,
                              std::vector<NativeSourcePin>* native_selected) {
  if(!selected||!native_selected)return false;
  selected->clear();native_selected->clear();
  CatalogReader input{payload};
  const unsigned char* magic=nullptr;
  uint64_t schema=0,asset_count=0,group_count=0,country_count=0,version_length=0;
  if (!input.Read(sizeof(kCatalogMagic),&magic) ||
      !std::equal(std::begin(kCatalogMagic),std::end(kCatalogMagic),magic) ||
      !input.Integer(2,&schema) || schema!=3 ||
      !input.Integer(2,&asset_count) || !asset_count || asset_count>4096 ||
      !input.Integer(2,&group_count) || !group_count || group_count>512 ||
      !input.Integer(2,&country_count) || !country_count || country_count>512 ||
      !input.Integer(2,&version_length) || !version_length || version_length>64) return false;
  const unsigned char* version=nullptr;
  if (!input.Read(static_cast<size_t>(version_length),&version)) return false;
  for(size_t i=0;i<version_length;++i)if(version[i]<33 || version[i]>126)return false;
  std::vector<CatalogFontAsset> assets;assets.reserve(static_cast<size_t>(asset_count));
  std::set<std::string> identities;
  for(size_t i=0;i<asset_count;++i) {
    uint64_t kind=0,size=0;const unsigned char* hash=nullptr;
    if (!input.Integer(1,&kind) || kind>1 || !input.Integer(8,&size) || !size || size>=(128ULL<<20) || !input.Read(32,&hash)) return false;
    CatalogFontAsset asset;asset.hash=CatalogHex(hash,32);asset.name=std::wstring(asset.hash.begin(),asset.hash.end())+L".blob";
    asset.size=size;asset.font=kind==1;
    if(!identities.insert(asset.hash).second)return false;
    assets.push_back(std::move(asset));
  }
  std::set<std::string> names;bool found=false;
  std::vector<std::string> group_names;
  for(size_t i=0;i<group_count;++i) {
    uint64_t length=0,count=0;const unsigned char* text=nullptr;
    if (!input.Integer(1,&length) || !length || length>96 || !input.Read(static_cast<size_t>(length),&text)) return false;
    std::string name(reinterpret_cast<const char*>(text),static_cast<size_t>(length));
    if(name[0]<'a'||name[0]>'z'||!names.insert(name).second)return false;
    for(char c:name)if(!((c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='-'))return false;
    if(!input.Integer(2,&count)||!count||count>128)return false;
    std::set<size_t> indexes;size_t fonts=0;bool license=false;uint64_t total=0;
    std::vector<CatalogFontAsset> members;
    for(size_t j=0;j<count;++j) {
      uint64_t index=0;if(!input.Integer(2,&index)||index>=assets.size()||!indexes.insert(static_cast<size_t>(index)).second)return false;
      const auto& asset=assets[static_cast<size_t>(index)];
      fonts+=asset.font;license=license||!asset.font;total+=asset.size;
      if(name==requested)members.push_back(asset);
    }
    if(!fonts||fonts>64||!license||total>(512ULL<<20))return false;
    group_names.push_back(name);
    if(name==requested){found=true;*selected=std::move(members);}
  }
  std::set<std::string> countries;size_t us_group=group_names.size();
  for(size_t i=0;i<country_count;++i) {
    const unsigned char* country=nullptr;uint64_t index=0;
    if(!input.Read(2,&country)||!input.Integer(2,&index)||index>=group_count)return false;
    if(country[0]<'A'||country[0]>'Z'||country[1]<'A'||country[1]>'Z')return false;
    if(!countries.insert(std::string(reinterpret_cast<const char*>(country),2)).second)return false;
    if(country[0]=='U'&&country[1]=='S')us_group=static_cast<size_t>(index);
  }
  if(us_group>=group_names.size())return false;
  // An unknown raw group retains only the authenticated US native selection;
  // its optional asset list stays empty. Invalid signatures never reach here.
  const std::string target_name=found?std::string(requested):group_names[us_group];

  uint64_t source_count=0;
  if(!input.Integer(2,&source_count)||!source_count||source_count>4096)return false;
  std::vector<NativeSourcePin> sources;sources.reserve(static_cast<size_t>(source_count));
  std::set<std::string> source_identities;size_t common_count=0;
  const auto safe_name=[](const unsigned char* value,size_t length) {
    if(!length||length>160)return false;
    std::string text(reinterpret_cast<const char*>(value),length);
    if(text.find("..")!=std::string::npos)return false;
    for(char c:text)if(!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_'||c=='.'||c=='-'))return false;
    return text[0]!='.'&&text[0]!='-'&&text[0]!='_';
  };
  for(size_t i=0;i<source_count;++i) {
    uint64_t kind=0,length=0,package_length=0,size=0;
    const unsigned char *name=nullptr,*package=nullptr,*hash=nullptr;
    if(!input.Integer(1,&kind)||kind>2||!input.Integer(1,&length)||length>127||
        !input.Read(static_cast<size_t>(length),&name)||!safe_name(name,static_cast<size_t>(length))||
        !input.Integer(1,&package_length)||package_length>160||
        !input.Read(static_cast<size_t>(package_length),&package)||
        !input.Integer(8,&size)||!size||size>=(128ULL<<20)||!input.Read(32,&hash))return false;
    if((kind==2)!=(package_length>0)|| (package_length&&!safe_name(package,static_cast<size_t>(package_length))))return false;
    const std::string file_name(reinterpret_cast<const char*>(name),static_cast<size_t>(length));
    const std::string package_name(reinterpret_cast<const char*>(package),static_cast<size_t>(package_length));
    if(kind==2&&package_name.find('_')==std::string::npos)return false;
    std::string identity=package_name+"|"+file_name;
    for(char& c:identity)if(c>='A'&&c<='Z')c=static_cast<char>(c-'A'+'a');
    if(!source_identities.insert(identity).second)return false;
    NativeSourcePin source;source.name.assign(file_name.begin(),file_name.end());
    source.package.assign(package_name.begin(),package_name.end());source.hash=CatalogHex(hash,32);source.size=size;
    source.kind=kind==0?NativeSourceKind::kCore:kind==1?NativeSourceKind::kRegional:NativeSourceKind::kShared;
    if(kind!=1)++common_count;
    sources.push_back(std::move(source));
  }
  if(!common_count||std::none_of(sources.begin(),sources.end(),[](const auto& s){return s.kind==NativeSourceKind::kCore;}))return false;
  for(const auto& name:group_names) {
    uint64_t count=0,total=0;
    if(!input.Integer(2,&count)||count<common_count||count>1024)return false;
    std::set<size_t> indexes;size_t common_seen=0;std::vector<NativeSourcePin> members;
    for(size_t i=0;i<count;++i) {
      uint64_t index=0;if(!input.Integer(2,&index)||index>=sources.size()||!indexes.insert(static_cast<size_t>(index)).second)return false;
      const auto& source=sources[static_cast<size_t>(index)];total+=source.size;
      if(source.kind!=NativeSourceKind::kRegional)++common_seen;
      if(name==target_name)members.push_back(source);
    }
    if(common_seen!=common_count||total>(2ULL<<30))return false;
    if(name==target_name)*native_selected=std::move(members);
  }
  return !native_selected->empty()&&input.offset==payload.size();
}

inline HRESULT OpenCatalogDirectory(const std::wstring& supplied,
                                    std::wstring* root,Resources* handles) {
  if(supplied.size()<3||supplied[1]!=L':'||(supplied[2]!=L'\\'&&supplied[2]!=L'/'))return E_INVALIDARG;
  std::vector<wchar_t> buffer(32768);
  const DWORD count=GetFullPathNameW(supplied.c_str(),static_cast<DWORD>(buffer.size()),buffer.data(),nullptr);
  if(!count||count>=buffer.size())return E_INVALIDARG;
  root->assign(buffer.data(),count);
  while(root->size()>3&&(root->back()==L'\\'||root->back()==L'/'))root->pop_back();
  for(size_t i=3;i<=root->size();++i) {
    if(i<root->size()&&(*root)[i]!=L'\\'&&(*root)[i]!=L'/')continue;
    const DWORD attributes=GetFileAttributesW(root->substr(0,i).c_str());
    if(attributes==INVALID_FILE_ATTRIBUTES||(attributes&FILE_ATTRIBUTE_REPARSE_POINT))return E_ACCESSDENIED;
  }
  handles->directory=CreateFileW(root->c_str(),FILE_READ_ATTRIBUTES,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,
      FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT,nullptr);
  if(handles->directory==INVALID_HANDLE_VALUE)return HRESULT_FROM_WIN32(GetLastError());
  BY_HANDLE_FILE_INFORMATION info;
  if(!GetFileInformationByHandle(handles->directory,&info)||!(info.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY)||
      (info.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT))return E_ACCESSDENIED;
  return S_OK;
}

inline bool ReadCatalogFile(const std::wstring& file,size_t maximum,
                            Resources* handles,std::vector<unsigned char>* bytes) {
  HANDLE input=CreateFileW(file.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,
      FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_SEQUENTIAL_SCAN,nullptr);
  if(input==INVALID_HANDLE_VALUE)return false;
  handles->handles.push_back(input);
  BY_HANDLE_FILE_INFORMATION info;LARGE_INTEGER size;
  if(!GetFileInformationByHandle(input,&info)||(info.dwFileAttributes&(FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT))||
      !GetFileSizeEx(input,&size)||size.QuadPart<=0||static_cast<uint64_t>(size.QuadPart)>maximum)return false;
  bytes->resize(static_cast<size_t>(size.QuadPart));size_t offset=0;
  while(offset<bytes->size()) {
    DWORD read=0;if(!ReadFile(input,bytes->data()+offset,static_cast<DWORD>(bytes->size()-offset),&read,nullptr)||!read)return false;
    offset+=read;
  }
  return true;
}


struct CatalogSelection {
  std::vector<CatalogFontAsset> assets;
  std::vector<NativeSourcePin> native_sources;
  std::shared_ptr<Resources> retained_catalog;
};
inline HRESULT LoadCountryCatalog(const std::wstring& catalog_directory,
                                  std::string_view group,
                                  std::shared_ptr<CatalogSelection>* result) {
  if(!result)return E_POINTER;result->reset();
  auto selection=std::make_shared<CatalogSelection>();
  selection->retained_catalog=std::make_shared<Resources>();
  auto& catalogue=*selection->retained_catalog;std::wstring root;
  HRESULT status=OpenCatalogDirectory(catalog_directory,&root,&catalogue);
  if(FAILED(status))return status;
  std::vector<unsigned char> payload,signature;
  if(!ReadCatalogFile(root+L"\\catalog.bin",kMaximumCatalogBytes,&catalogue,&payload)||
      !ReadCatalogFile(root+L"\\catalog.sig",64,&catalogue,&signature)||
      !VerifyCatalogSignature(payload,signature))return E_ACCESSDENIED;
  std::array<unsigned char,32> digest;
  if(!CatalogHash(payload,false,&digest))return E_FAIL;
  const std::string identity=CatalogHex(digest.data(),digest.size());
  const auto separator=root.find_last_of(L"\\/");
  if(separator==std::wstring::npos||root.substr(separator+1)!=std::wstring(identity.begin(),identity.end()))return E_ACCESSDENIED;
  if(!ParseCatalogGroup(payload,group,&selection->assets,&selection->native_sources))return E_INVALIDARG;
  *result=std::move(selection);return S_OK;
}
inline HRESULT VerifyCatalogAssets(const std::wstring& supplied,
                                    const CatalogSelection& selection,
                                    std::shared_ptr<Resources>* result) {
  if(!result)return E_POINTER;result->reset();
  std::vector<Asset> assets;assets.reserve(selection.assets.size());
  for(const auto& asset:selection.assets)assets.push_back({asset.name.c_str(),asset.hash,asset.size,asset.font});
  return VerifyAssets(supplied,assets.data(),assets.size(),result);
}
inline HRESULT VerifyCountry(const std::wstring& supplied,std::string_view group,
                              const std::wstring& catalog_directory,
                              std::shared_ptr<Resources>* result) {
  if(!result)return E_POINTER;result->reset();
  std::shared_ptr<CatalogSelection> selection;
  HRESULT hr=LoadCountryCatalog(catalog_directory,group,&selection);
  if(FAILED(hr))return hr;
  hr=VerifyCatalogAssets(supplied,*selection,result);
  if(FAILED(hr))return hr;
  auto& retained=*selection->retained_catalog;
  (*result)->handles.push_back(retained.directory);retained.directory=INVALID_HANDLE_VALUE;
  (*result)->handles.insert((*result)->handles.end(),retained.handles.begin(),retained.handles.end());retained.handles.clear();
  return S_OK;
}


struct NativeViewSnapshot {
  std::vector<HANDLE> handles;
  std::vector<std::wstring> paths;
  std::vector<size_t> pins;
  bool additions_rejected = false;
  ~NativeViewSnapshot() { for (HANDLE handle : handles) CloseHandle(handle); }
};
inline bool SameNativePath(std::wstring_view a, std::wstring_view b) {
  return a.size() == b.size() &&
      CompareStringOrdinal(a.data(), static_cast<int>(a.size()), b.data(),
                           static_cast<int>(b.size()), TRUE) == CSTR_EQUAL;
}
inline std::wstring_view NativeLeaf(std::wstring_view path) {
  const auto at = path.find_last_of(L"\\/");
  return at == std::wstring_view::npos ? path : path.substr(at + 1);
}
inline std::wstring_view NativeParent(std::wstring_view path) {
  const auto at = path.find_last_of(L"\\/");
  return at == std::wstring_view::npos ? std::wstring_view() : path.substr(0, at);
}
inline HRESULT NativeFontPath(IDWriteFontFaceReference* reference,
                              std::wstring* path) {
  if (!reference || !path) return E_POINTER;
  Microsoft::WRL::ComPtr<IDWriteFontFace3> face;
  HRESULT hr = reference->CreateFontFace(&face);
  if (FAILED(hr)) return hr;
  UINT32 count = 1;
  Microsoft::WRL::ComPtr<IDWriteFontFile> file;
  hr = face->GetFiles(&count, &file);
  if (FAILED(hr) || count != 1) return DWRITE_E_FILEFORMAT;
  Microsoft::WRL::ComPtr<IDWriteFontFileLoader> loader;
  Microsoft::WRL::ComPtr<IDWriteLocalFontFileLoader> local;
  const void* key = nullptr;
  UINT32 key_size = 0, length = 0;
  if (FAILED(hr = file->GetLoader(&loader)) ||
      FAILED(hr = loader.As(&local)) ||
      FAILED(hr = file->GetReferenceKey(&key, &key_size)) ||
      FAILED(hr = local->GetFilePathLengthFromKey(key, key_size, &length)))
    return hr;
  if (!length || length > 32766) return E_INVALIDARG;
  std::vector<wchar_t> buffer(length + 1);
  hr = local->GetFilePathFromKey(key, key_size, buffer.data(), length + 1);
  if (SUCCEEDED(hr)) path->assign(buffer.data(), length);
  return hr;
}
inline HRESULT CreateControlledCollection(
    IDWriteFactory* factory, const std::vector<NativeSourcePin>& source_pins,
    const std::shared_ptr<Resources>& additions,
    std::shared_ptr<NativeViewSnapshot>* retained,
    IDWriteFontCollection** output) {
  if (!factory || !retained || !output) return E_POINTER;
  retained->reset(); *output = nullptr;
  wchar_t windows_path[MAX_PATH + 1] = {};
  const UINT windows_length = GetWindowsDirectoryW(windows_path, MAX_PATH + 1);
  if (!windows_length || windows_length > MAX_PATH) return E_FAIL;
  const std::wstring system_root = std::wstring(windows_path) + L"\\Fonts";
  Microsoft::WRL::ComPtr<IDWriteFactory> original(factory);
  Microsoft::WRL::ComPtr<IDWriteFactory5> modern;
  HRESULT hr = original.As(&modern);
  if (FAILED(hr)) return hr;
  Microsoft::WRL::ComPtr<IDWriteFontCollection> native;
  Microsoft::WRL::ComPtr<IDWriteFontCollection1> native1;
  Microsoft::WRL::ComPtr<IDWriteFontSet> native_set;
  if (FAILED(hr = original->GetSystemFontCollection(&native)) ||
      FAILED(hr = native.As(&native1)) ||
      FAILED(hr = native1->GetFontSet(&native_set))) return hr;
  if (!native_set || native_set->GetFontCount() > 32768) return E_FAIL;
  Microsoft::WRL::ComPtr<IDWriteFontSetBuilder1> builder;
  if (FAILED(hr = modern->CreateFontSetBuilder(&builder))) return hr;
  const size_t count = source_pins.size();
  if (!count || count > 1024) return E_INVALIDARG;
  std::vector<bool> seen(count, false);
  std::vector<std::wstring> verified_paths(count);
  auto snapshot = std::make_shared<NativeViewSnapshot>();
  for (UINT32 index = 0; index < native_set->GetFontCount(); ++index) {
    Microsoft::WRL::ComPtr<IDWriteFontFaceReference> reference;
    std::wstring path;
    if (FAILED(native_set->GetFontFaceReference(index, &reference)) ||
        FAILED(NativeFontPath(reference.Get(), &path))) continue;
    const auto leaf = NativeLeaf(path);
    const auto parent = NativeParent(path);
    const auto package = NativeLeaf(parent);
    size_t selected = count;
    for (size_t pin = 0; pin < count; ++pin) {
      const auto& allowed = source_pins[pin];
      if (!SameNativePath(leaf, allowed.name)) continue;
      if (allowed.kind == NativeSourceKind::kShared) {
        if (!SameNativePath(package, allowed.package) ||
            !SameNativePath(NativeLeaf(NativeParent(parent)), L"WindowsApps"))
          continue;
      } else if (!SameNativePath(parent, system_root)) {
        continue;
      }
      selected = pin; break;
    }
    // Registered inbox shared sources must not silently disappear after a Store
    // update. A new package version needs another reviewed data variant.
    if (selected == count && SameNativePath(NativeLeaf(NativeParent(parent)), L"WindowsApps")) {
      for (const auto& pin : source_pins) {
        if (pin.kind != NativeSourceKind::kShared) continue;
        const auto split = pin.package.find(L'_');
        if (split == std::wstring::npos) return E_INVALIDARG;
        const auto prefix = std::wstring_view(pin.package).substr(0, split + 1);
        if (package.size() > prefix.size() && SameNativePath(package.substr(0, prefix.size()), prefix))
          return HRESULT_FROM_WIN32(ERROR_REVISION_MISMATCH);
      }
    }
    if (selected == count) continue;
    if (!seen[selected]) {
      HANDLE handle = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ,
          nullptr, OPEN_EXISTING,
          FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
      if (handle == INVALID_HANDLE_VALUE) return HRESULT_FROM_WIN32(GetLastError());
      snapshot->handles.push_back(handle);
      BY_HANDLE_FILE_INFORMATION info;
      if (!GetFileInformationByHandle(handle, &info) ||
          (info.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY)) ||
          !CheckHash(handle, source_pins[selected].GetAsset()))
        return HRESULT_FROM_WIN32(ERROR_INVALID_DATA);
      snapshot->paths.push_back(path); snapshot->pins.push_back(selected);
      verified_paths[selected] = path;
      seen[selected] = true;
    } else if (!SameNativePath(path, verified_paths[selected])) {
      // A pin does not authorize another physical pathname. Every reference
      // added below must use the exact source that was hashed and retained.
      return HRESULT_FROM_WIN32(ERROR_INVALID_DATA);
    }
    // Preserve native named instances, simulations, TTC face indices and axes.
    if (FAILED(hr = builder->AddFontFaceReference(reference.Get()))) return hr;
  }
  for (size_t pin = 0; pin < count; ++pin) {
    const auto kind = source_pins[pin].kind;
    if ((kind != NativeSourceKind::kShared) && !seen[pin])
      return HRESULT_FROM_WIN32(ERROR_REVISION_MISMATCH);
    if (kind != NativeSourceKind::kShared || !seen[pin]) continue;
    // A registered supported package retains its complete reviewed source set.
    for (size_t other = 0; other < count; ++other)
      if (source_pins[other].kind == NativeSourceKind::kShared &&
          SameNativePath(source_pins[other].package, source_pins[pin].package) &&
          !seen[other]) return E_FAIL;
  }
  const auto retry_without_additions = [&]() -> HRESULT {
    const HRESULT retry = CreateControlledCollection(
        factory, source_pins, nullptr, retained, output);
    if (SUCCEEDED(retry) && *retained) (*retained)->additions_rejected = true;
    return retry;
  };
  if (additions) for (const auto& path : additions->fonts) {
    Microsoft::WRL::ComPtr<IDWriteFontFile> file;
    if (FAILED(hr = modern->CreateFontFileReference(path.c_str(), nullptr, &file)) ||
        FAILED(hr = builder->AddFontFile(file.Get()))) return retry_without_additions();
  }
  Microsoft::WRL::ComPtr<IDWriteFontSet> set;
  Microsoft::WRL::ComPtr<IDWriteFontCollection1> collection;
  if (FAILED(hr = builder->CreateFontSet(&set)) ||
      FAILED(hr = modern->CreateFontCollectionFromFontSet(set.Get(), &collection)))
    return additions && !additions->fonts.empty() ? retry_without_additions() : hr;
  hr = collection.CopyTo(output);
  if (SUCCEEDED(hr)) *retained = std::move(snapshot);
  return hr;
}

inline HRESULT CreateCollection(IDWriteFactory *factory,
                                const std::shared_ptr<Resources> &resources,
                                IDWriteFontCollection **output) {
  if (!factory || !resources || !output)
    return E_POINTER;
  *output = nullptr;
  Microsoft::WRL::ComPtr<IDWriteFactory> original(factory);
  Microsoft::WRL::ComPtr<IDWriteFactory5> modern;
  HRESULT hr = original.As(&modern);
  if (FAILED(hr))
    return hr;
  Microsoft::WRL::ComPtr<IDWriteFontSetBuilder1> builder;
  hr = modern->CreateFontSetBuilder(&builder);
  if (FAILED(hr))
    return hr;
  // Preserve the same native view Chromium normally uses. GetSystemFontSet()
  // also imports compatibility/remote-backed faces absent from that view;
  // those can hit the local-file broker's invariant during unique-name lookup.
  Microsoft::WRL::ComPtr<IDWriteFontCollection> native_collection;
  hr = original->GetSystemFontCollection(&native_collection);
  if (FAILED(hr))
    return hr;
  Microsoft::WRL::ComPtr<IDWriteFontCollection1> native_collection1;
  hr = native_collection.As(&native_collection1);
  if (FAILED(hr))
    return hr;
  Microsoft::WRL::ComPtr<IDWriteFontSet> system;
  hr = native_collection1->GetFontSet(&system);
  if (FAILED(hr))
    return hr;
  hr = builder->AddFontSet(system.Get());
  if (FAILED(hr))
    return hr;
  // Real local font files and all of their faces/variable instances. No global
  // font registration, font-name substitution or Windows font file changes.
  for (const auto &path : resources->fonts) {
    Microsoft::WRL::ComPtr<IDWriteFontFile> file;
    hr = modern->CreateFontFileReference(path.c_str(), nullptr, &file);
    if (FAILED(hr))
      return hr;
    hr = builder->AddFontFile(file.Get());
    if (FAILED(hr))
      return hr;
  }
  Microsoft::WRL::ComPtr<IDWriteFontSet> set;
  hr = builder->CreateFontSet(&set);
  if (FAILED(hr))
    return hr;
  Microsoft::WRL::ComPtr<IDWriteFontCollection1> collection;
  hr = modern->CreateFontCollectionFromFontSet(set.Get(), &collection);
  if (FAILED(hr))
    return hr;
  return collection.CopyTo(output);
}
} // namespace base::uxr_private_fonts_v1
#endif
