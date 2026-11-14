// Copyright 2026. Read-only, process-private DirectWrite font resources.
#ifndef BASE_UXR_PRIVATE_FONT_PACKAGES_WIN_H_
#define BASE_UXR_PRIVATE_FONT_PACKAGES_WIN_H_
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <array>
#include <windows.h>

#include <bcrypt.h>
#include <cstdint>
#include <dwrite_3.h>
#include <memory>
#include <string>
#include <string_view>
#include <vector>
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
inline HRESULT Verify(const std::wstring &supplied,
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
  for (const auto &asset : kAssets) {
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
  Microsoft::WRL::ComPtr<IDWriteFontSet> system;
  hr = modern->GetSystemFontSet(&system);
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
