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
#include <appmodel.h>
#include <map>
// On-disk face descriptors shared by the release index generator and DirectWrite.
#ifndef SHARDX_FONT_FACE_INDEX_WIN_H_
#define SHARDX_FONT_FACE_INDEX_WIN_H_

#include <windows.h>
#include <winioctl.h>
#include <dwrite_3.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace shardx_font_index {

constexpr char kMagic[] = "SXFACE1";
constexpr size_t kLimit = 4 * 1024 * 1024;
struct Stamp {
  uint64_t volume = 0, file = 0, size = 0, modified = 0, journal = 0, usn = 0;
  bool operator==(const Stamp& b) const {
    return journal && usn && volume == b.volume && file == b.file &&
           size == b.size && modified == b.modified && journal == b.journal && usn == b.usn;
  }
};
inline uint64_t Join32(DWORD high, DWORD low) {
  return (static_cast<uint64_t>(high) << 32) | low;
}
// Both controls are issued on the read-locked file, so volume elevation is
// unnecessary on supported NTFS volumes. Other filesystems take the hash path.
inline bool FileStamp(HANDLE handle, Stamp* out) {
  BY_HANDLE_FILE_INFORMATION info{};
  USN_JOURNAL_DATA_V0 journal{};
  alignas(USN_RECORD_V2) std::array<unsigned char, 2048> buffer{};
  DWORD count = 0;
  if (!GetFileInformationByHandle(handle, &info) ||
      (info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) ||
      !DeviceIoControl(handle, FSCTL_QUERY_USN_JOURNAL, nullptr, 0, &journal,
                       sizeof(journal), &count, nullptr) || count < sizeof(journal) ||
      !DeviceIoControl(handle, FSCTL_READ_FILE_USN_DATA, nullptr, 0, buffer.data(),
                       static_cast<DWORD>(buffer.size()), &count, nullptr) ||
      count < FIELD_OFFSET(USN_RECORD_V2, FileName))
    return false;
  const auto* record = reinterpret_cast<const USN_RECORD_V2*>(buffer.data());
  const auto file = Join32(info.nFileIndexHigh, info.nFileIndexLow);
  if (record->MajorVersion != 2 || record->RecordLength > count ||
      record->FileReferenceNumber != file || record->Usn <= 0 ||
      !journal.UsnJournalID || !file || record->Usn >= journal.NextUsn)
    return false;
  *out = {info.dwVolumeSerialNumber, file, Join32(info.nFileSizeHigh, info.nFileSizeLow),
          Join32(info.ftLastWriteTime.dwHighDateTime,info.ftLastWriteTime.dwLowDateTime),
          journal.UsnJournalID, static_cast<uint64_t>(record->Usn)};
  return true;
}

struct Source {
  uint8_t kind = 0;  // core, regional Windows source, registered shared source, object
  std::string name, package, hash;
  uint64_t size = 0;
  Stamp stamp;
};
struct Face {
  uint16_t file = 0;
  uint32_t index = 0, simulations = 0;
  std::vector<DWRITE_FONT_AXIS_VALUE> axes;
};
struct Index {
  std::string catalog;
  std::vector<Source> sources;
  std::vector<Face> faces;
};
class Reader {
 public:
  explicit Reader(const std::vector<unsigned char>& bytes) : bytes_(bytes) {}
  template <class T> bool Read(T* value) {
    if (bytes_.size() - pos_ < sizeof(T)) return false;
    std::memcpy(value, bytes_.data() + pos_, sizeof(T)); pos_ += sizeof(T); return true;
  }
  bool Text(size_t n, std::string* out) {
    if (bytes_.size() - pos_ < n) return false;
    out->assign(reinterpret_cast<const char*>(bytes_.data()+pos_),n); pos_+=n; return true;
  }
  bool Done() const { return pos_ == bytes_.size(); }
 private:
  const std::vector<unsigned char>& bytes_;
  size_t pos_ = 0;
};
inline bool HexDigest(const std::string& s) {
  return s.size()==64 && s.find_first_not_of("0123456789abcdef")==std::string::npos;
}
inline bool Leaf(const std::string& s) {
  return !s.empty() && s.size()<=180 && s.find("..")==std::string::npos &&
         s.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")==std::string::npos;
}
inline bool Parse(const std::vector<unsigned char>& bytes, const std::string& catalog, Index* output) {
  if (bytes.size()>kLimit) return false;
  Reader r(bytes); Index result; std::string magic;
  uint16_t version=0,files=0; uint32_t faces=0;
  if (!r.Text(sizeof(kMagic),&magic) || magic!=std::string(kMagic,sizeof(kMagic)) ||
      !r.Read(&version) || version!=1 || !r.Text(64,&result.catalog) ||
      result.catalog!=catalog || !HexDigest(result.catalog) || !r.Read(&files) ||
      !files || files>4096 || !r.Read(&faces) || !faces || faces>32768) return false;
  for (unsigned i=0;i<files;++i) {
    Source s; uint16_t n=0,p=0;
    if (!r.Read(&s.kind) || s.kind>3 || !r.Read(&n) || !r.Read(&p) ||
        !r.Text(n,&s.name) || !Leaf(s.name) || !r.Text(p,&s.package) ||
        (s.kind==2 ? !Leaf(s.package) : !s.package.empty()) ||
        !r.Text(64,&s.hash) || !HexDigest(s.hash) || !r.Read(&s.size) || !s.size ||
        !r.Read(&s.stamp.volume) || !r.Read(&s.stamp.file) || !r.Read(&s.stamp.size) ||
        !r.Read(&s.stamp.modified) || !r.Read(&s.stamp.journal) || !r.Read(&s.stamp.usn) ||
        (s.stamp.journal && s.stamp.size!=s.size)) return false;
    result.sources.push_back(std::move(s));
  }
  for (uint32_t i=0;i<faces;++i) {
    Face f; uint16_t n=0;
    if (!r.Read(&f.file) || f.file>=files || result.sources[f.file].kind==3 ||
        !r.Read(&f.index) || f.index>65535 || !r.Read(&f.simulations) ||
        (f.simulations & ~3u) || !r.Read(&n) || n>64) return false;
    f.axes.resize(n);
    for (auto& axis:f.axes) {
      uint32_t tag=0;
      if (!r.Read(&tag) || !r.Read(&axis.value) || !std::isfinite(axis.value)) return false;
      axis.axisTag=static_cast<DWRITE_FONT_AXIS_TAG>(tag);
    }
    result.faces.push_back(std::move(f));
  }
  if (!r.Done()) return false;
  *output=std::move(result);
  return true;
}
}  // namespace shardx_font_index
#endif  // SHARDX_FONT_FACE_INDEX_WIN_H_

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
inline bool VerifiedIndexedAsset(HANDLE file, const Asset& asset,
                                 const shardx_font_index::Index* index) {
  if (index) {
    for (const auto& source : index->sources) {
      if (source.kind!=3 || source.hash!=asset.sha256 || source.size!=asset.size ||
          std::wstring(source.name.begin(),source.name.end())!=asset.name) continue;
      shardx_font_index::Stamp actual;
      if (shardx_font_index::FileStamp(file,&actual) && actual==source.stamp) return true;
      break;
    }
  }
  return CheckHash(file,asset);
}
inline HRESULT VerifyAssets(const std::wstring &supplied,
                            const Asset *assets, size_t asset_count,
                            std::shared_ptr<Resources> *result, const shardx_font_index::Index* face_index = nullptr) {
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
        !VerifiedIndexedAsset(file, asset, face_index))
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

inline bool CatalogHashDomain(const std::vector<unsigned char>& data,
                        std::string_view domain,
                        std::array<unsigned char,32>* result) {
  HashState state;
  DWORD length=0,used=0;
  if (BCryptOpenAlgorithmProvider(&state.algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0 ||
      BCryptGetProperty(state.algorithm,BCRYPT_OBJECT_LENGTH,reinterpret_cast<PUCHAR>(&length),sizeof(length),&used,0)<0 ||
      length==0 || length>65536) return false;
  state.object.resize(length);
  if (BCryptCreateHash(state.algorithm,&state.hash,state.object.data(),length,nullptr,0,0)<0) return false;
  if (!domain.empty() && BCryptHashData(state.hash,
      reinterpret_cast<PUCHAR>(const_cast<char*>(domain.data())),
      static_cast<ULONG>(domain.size()),0)<0) return false;
  return BCryptHashData(state.hash,const_cast<PUCHAR>(data.data()),static_cast<ULONG>(data.size()),0)>=0 &&
         BCryptFinishHash(state.hash,result->data(),static_cast<ULONG>(result->size()),0)>=0;
}

inline bool CatalogHash(const std::vector<unsigned char>& data, bool signed_payload,
                        std::array<unsigned char,32>* result) {
  return CatalogHashDomain(data,signed_payload ? std::string_view(kCatalogSignatureDomain,sizeof(kCatalogSignatureDomain)-1) : std::string_view(), result);
}
inline bool VerifyFontSignature(const std::vector<unsigned char>& payload,
                                const std::vector<unsigned char>& signature,
                                std::string_view domain,size_t limit) {
  if (signature.size()!=64 || payload.size()>limit) return false;
  std::array<unsigned char,32> digest;
  if (!CatalogHashDomain(payload,domain,&digest)) return false;
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

inline bool VerifyCatalogSignature(const std::vector<unsigned char>& payload,
                                   const std::vector<unsigned char>& signature) {
  return VerifyFontSignature(payload,signature,
      std::string_view(kCatalogSignatureDomain,sizeof(kCatalogSignatureDomain)-1),kMaximumCatalogBytes);
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
  std::shared_ptr<shardx_font_index::Index> face_index;
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
  std::vector<unsigned char> index_bytes,index_signature;
  constexpr char face_domain[]="ShardXFontFaceIndex-v1\0";
  if (ReadCatalogFile(root+L"\\faces.bin",shardx_font_index::kLimit,&catalogue,&index_bytes) &&
      ReadCatalogFile(root+L"\\faces.sig",64,&catalogue,&index_signature) &&
      VerifyFontSignature(index_bytes,index_signature,std::string_view(face_domain,sizeof(face_domain)-1),shardx_font_index::kLimit)) {
    auto index=std::make_shared<shardx_font_index::Index>();
    if (shardx_font_index::Parse(index_bytes,identity,index.get())) selection->face_index=std::move(index);
  }
  *result=std::move(selection);return S_OK;
}
inline HRESULT VerifyCatalogAssets(const std::wstring& supplied,
                                    const CatalogSelection& selection,
                                    std::shared_ptr<Resources>* result) {
  if(!result)return E_POINTER;result->reset();
  std::vector<Asset> assets;assets.reserve(selection.assets.size());
  for(const auto& asset:selection.assets)assets.push_back({asset.name.c_str(),asset.hash,asset.size,asset.font});
  return VerifyAssets(supplied,assets.data(),assets.size(),result,selection.face_index.get());
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
  std::vector<std::shared_ptr<Resources>> directories;
  std::vector<std::wstring> paths;
  std::vector<size_t> pins;
  bool additions_rejected = false;
  bool used_index = false;
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
  Microsoft::WRL::ComPtr<IDWriteFontFile> file;
  HRESULT hr = reference->GetFontFile(&file);
  if (FAILED(hr)) return hr;
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

// Resolve only the package families authorized by this signed group. When an
// unfamiliar registration layout is encountered, use the original controlled
// discovery and all its source checks.
inline bool IndexedPackagePaths(const std::vector<NativeSourcePin>& pins,
                                std::map<std::wstring,std::wstring>* paths) {
  std::set<std::wstring> allowed, families;
  for (const auto& pin:pins) {
    if(pin.kind!=NativeSourceKind::kShared || !allowed.insert(pin.package).second)continue;
    wchar_t family[PACKAGE_FAMILY_NAME_MAX_LENGTH+1]{};
    UINT32 length=static_cast<UINT32>(std::size(family));
    if(PackageFamilyNameFromFullName(pin.package.c_str(),&length,family)!=ERROR_SUCCESS)return false;
    families.insert(family);
  }
  for(const auto& family:families){
    UINT32 count=0,length=0;
    LONG rc=GetPackagesByPackageFamily(family.c_str(),&count,nullptr,&length,nullptr);
    if(rc==ERROR_SUCCESS && count==0)continue;
    if(rc!=ERROR_INSUFFICIENT_BUFFER || count>128 || !length || length>65536)return false;
    std::vector<PWSTR> names(count);std::vector<wchar_t> storage(length);
    if(GetPackagesByPackageFamily(family.c_str(),&count,names.data(),&length,storage.data())!=ERROR_SUCCESS)return false;
    for(UINT32 n=0;n<count;++n){
      if(!allowed.count(names[n]))return false;
      UINT32 chars=0;
      if(GetPackagePathByFullName(names[n],&chars,nullptr)!=ERROR_INSUFFICIENT_BUFFER || !chars || chars>32767)return false;
      std::vector<wchar_t> path(chars);
      if(GetPackagePathByFullName(names[n],&chars,path.data())!=ERROR_SUCCESS)return false;
      (*paths)[names[n]]=path.data();
    }
  }
  return true;
}

inline HRESULT CreateIndexedCollection(
    IDWriteFactory* factory,const std::vector<NativeSourcePin>& pins,
    const shardx_font_index::Index& index,const std::shared_ptr<Resources>& additions,
    std::shared_ptr<NativeViewSnapshot>* retained,IDWriteFontCollection** output) {
  using Microsoft::WRL::ComPtr;
  ComPtr<IDWriteFactory6> modern;
  if(FAILED(factory->QueryInterface(IID_PPV_ARGS(&modern))))return S_FALSE;
  wchar_t windows_path[MAX_PATH+1]{};
  const UINT length=GetWindowsDirectoryW(windows_path,MAX_PATH+1);
  if(!length || length>MAX_PATH)return E_FAIL;
  const std::wstring system_root=std::wstring(windows_path)+L"\\Fonts";
  std::map<std::wstring,std::wstring> packages;
  if(!IndexedPackagePaths(pins,&packages))return S_FALSE;
  auto snapshot=std::make_shared<NativeViewSnapshot>();
  std::set<std::wstring> directories;
  std::map<std::pair<std::wstring,std::wstring>,size_t> rows;
  for(size_t i=0;i<index.sources.size();++i){
    const auto& s=index.sources[i];
    if(s.kind==3)continue;
    auto key=std::make_pair(std::wstring(s.package.begin(),s.package.end()),std::wstring(s.name.begin(),s.name.end()));
    if(!rows.emplace(std::move(key),i).second)return S_FALSE;
  }
  std::vector<ComPtr<IDWriteFontFile>> files(index.sources.size());
  std::vector<bool> seen(index.sources.size());
  for(size_t p=0;p<pins.size();++p){
    const auto& pin=pins[p];
    std::wstring directory=system_root;
    if(pin.kind==NativeSourceKind::kShared){
      auto registered=packages.find(pin.package);
      if(registered==packages.end())continue;
      directory=registered->second;
    }
    const auto it=rows.find({pin.package,pin.name});
    if(it==rows.end())return S_FALSE;
    const size_t row=it->second;const auto& stored=index.sources[row];
    if(stored.kind!=static_cast<uint8_t>(pin.kind) || stored.size!=pin.size || stored.hash!=pin.hash)return S_FALSE;
    if(directories.insert(directory).second){
      auto held=std::make_shared<Resources>();std::wstring canonical;
      HRESULT hr=OpenCatalogDirectory(directory,&canonical,held.get());
      if(FAILED(hr))return hr;
      snapshot->directories.push_back(std::move(held));
    }
    const std::wstring path=directory+L"\\"+pin.name;
    HANDLE handle=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_SEQUENTIAL_SCAN,nullptr);
    if(handle==INVALID_HANDLE_VALUE)return HRESULT_FROM_WIN32(GetLastError());
    snapshot->handles.push_back(handle);
    BY_HANDLE_FILE_INFORMATION info{};
    if(!GetFileInformationByHandle(handle,&info) || (info.dwFileAttributes&(FILE_ATTRIBUTE_REPARSE_POINT|FILE_ATTRIBUTE_DIRECTORY)))return E_ACCESSDENIED;
    shardx_font_index::Stamp now;
    if(!(shardx_font_index::FileStamp(handle,&now) && now==stored.stamp) && !CheckHash(handle,pin.GetAsset()))
      return HRESULT_FROM_WIN32(ERROR_INVALID_DATA);
    HRESULT hr=modern->CreateFontFileReference(path.c_str(),nullptr,&files[row]);
    if(FAILED(hr))return hr;
    snapshot->paths.push_back(path);snapshot->pins.push_back(p);
  }
  ComPtr<IDWriteFontSetBuilder1> builder;
  HRESULT hr=modern->CreateFontSetBuilder(&builder);if(FAILED(hr))return hr;
  for(const auto& f:index.faces){
    if(!files[f.file])continue;
    ComPtr<IDWriteFontFaceReference1> ref;
    hr=modern->CreateFontFaceReference(files[f.file].Get(),f.index,
        static_cast<DWRITE_FONT_SIMULATIONS>(f.simulations),f.axes.data(),static_cast<UINT32>(f.axes.size()),&ref);
    if(FAILED(hr) || FAILED(hr=builder->AddFontFaceReference(ref.Get())))return hr;
    seen[f.file]=true;
  }
  for(size_t i=0;i<files.size();++i)if(files[i] && !seen[i])return S_FALSE;
  ComPtr<IDWriteFontSet> base;
  if(FAILED(hr=builder->CreateFontSet(&base)))return hr;
  ComPtr<IDWriteFontSet> chosen=base;
  if(additions && !additions->fonts.empty()){
    ComPtr<IDWriteFontSetBuilder1> combined;ComPtr<IDWriteFontSet> all;
    hr=modern->CreateFontSetBuilder(&combined);
    if(SUCCEEDED(hr))hr=combined->AddFontSet(base.Get());
    for(const auto& path:additions->fonts){
      if(FAILED(hr))break;
      ComPtr<IDWriteFontFile> file;
      hr=modern->CreateFontFileReference(path.c_str(),nullptr,&file);
      if(SUCCEEDED(hr))hr=combined->AddFontFile(file.Get());
    }
    if(SUCCEEDED(hr))hr=combined->CreateFontSet(&all);
    if(SUCCEEDED(hr))chosen=std::move(all);else snapshot->additions_rejected=true;
  }
  ComPtr<IDWriteFontCollection1> collection;
  hr=modern->CreateFontCollectionFromFontSet(chosen.Get(),&collection);
  if(FAILED(hr) && chosen.Get()!=base.Get()){
    collection.Reset();snapshot->additions_rejected=true;
    hr=modern->CreateFontCollectionFromFontSet(base.Get(),&collection);
  }
  if(FAILED(hr))return hr;
  hr=collection.CopyTo(output);if(SUCCEEDED(hr)){snapshot->used_index=true;*retained=std::move(snapshot);}
  return hr;
}

inline HRESULT CreateControlledCollection(
    IDWriteFactory* factory, const std::vector<NativeSourcePin>& source_pins,
    const std::shared_ptr<Resources>& additions,
    std::shared_ptr<NativeViewSnapshot>* retained,
    IDWriteFontCollection** output, const shardx_font_index::Index* index=nullptr) {
  if (!factory || !retained || !output) return E_POINTER;
  retained->reset(); *output = nullptr;
  if(index){
    const HRESULT indexed=CreateIndexedCollection(factory,source_pins,*index,additions,retained,output);
    if(indexed!=S_FALSE)return indexed;
  }
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
