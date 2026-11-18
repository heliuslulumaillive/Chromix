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
