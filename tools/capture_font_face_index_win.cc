// Build-time only. Authenticate every input file before signing a DirectWrite
// face index. The browser uses the resulting index without enumerating host fonts.
#define NOMINMAX
#include "generated_font_header.h"
#include "font_face_index_win.h"
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>

using Microsoft::WRL::ComPtr;
namespace font = base::uxr_private_fonts_v1;
namespace index_data = shardx_font_index;
namespace {
void Check(bool value, const char* reason) { if (!value) throw std::runtime_error(reason); }
void HR(HRESULT value, const char* reason) { Check(SUCCEEDED(value),reason); }
std::wstring Wide(const std::string& s) {
  if (s.empty()) return {};
  const int n=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s.data(),static_cast<int>(s.size()),nullptr,0);
  Check(n>0,"invalid UTF-8"); std::wstring w(n,0);
  Check(MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s.data(),static_cast<int>(s.size()),w.data(),n)==n,"UTF-8 conversion");
  return w;
}
struct LessPath {
  bool operator()(const std::wstring& a,const std::wstring& b) const {
    return CompareStringOrdinal(a.c_str(),-1,b.c_str(),-1,TRUE)==CSTR_LESS_THAN;
  }
};
std::wstring FontPath(IDWriteFontFaceReference* reference) {
  ComPtr<IDWriteFontFile> file; ComPtr<IDWriteFontFileLoader> loader; ComPtr<IDWriteLocalFontFileLoader> local;
  const void* key=nullptr;UINT32 n=0,length=0;
  if(FAILED(reference->GetFontFile(&file)) || FAILED(file->GetLoader(&loader)) || FAILED(loader.As(&local)) ||
     FAILED(file->GetReferenceKey(&key,&n)) || FAILED(local->GetFilePathLengthFromKey(key,n,&length)) ||
     !length || length>32766) return {};
  std::vector<wchar_t> w(length+1);
  if(FAILED(local->GetFilePathFromKey(key,n,w.data(),length+1))) return {};
  return {w.data(),length};
}
template<class T> void Write(std::ostream& o,T x){o.write(reinterpret_cast<const char*>(&x),sizeof(x));}
void Save(std::ostream& o,const index_data::Index& index) {
  o.write(index_data::kMagic,sizeof(index_data::kMagic));Write<uint16_t>(o,1);
  o.write(index.catalog.data(),64);Write<uint16_t>(o,static_cast<uint16_t>(index.sources.size()));
  Write<uint32_t>(o,static_cast<uint32_t>(index.faces.size()));
  for(const auto& s:index.sources) {
    Write(o,s.kind);Write<uint16_t>(o,static_cast<uint16_t>(s.name.size()));Write<uint16_t>(o,static_cast<uint16_t>(s.package.size()));
    o<<s.name<<s.package<<s.hash;Write(o,s.size);
    for(uint64_t v:{s.stamp.volume,s.stamp.file,s.stamp.size,s.stamp.modified,s.stamp.journal,s.stamp.usn})Write(o,v);
  }
  for(const auto& f:index.faces) {
    Write(o,f.file);Write(o,f.index);Write(o,f.simulations);Write<uint16_t>(o,static_cast<uint16_t>(f.axes.size()));
    for(const auto& a:f.axes){Write<uint32_t>(o,a.axisTag);Write(o,a.value);}
  }
  Check(o.good(),"index write");
}
std::vector<std::wstring> Properties(IDWriteFontSet* set,UINT32 i) {
  std::vector<std::wstring> result;
  for (auto prop:{DWRITE_FONT_PROPERTY_ID_FAMILY_NAME,DWRITE_FONT_PROPERTY_ID_FACE_NAME,DWRITE_FONT_PROPERTY_ID_FULL_NAME,
                  DWRITE_FONT_PROPERTY_ID_POSTSCRIPT_NAME,DWRITE_FONT_PROPERTY_ID_WEIGHT,DWRITE_FONT_PROPERTY_ID_STRETCH,
                  DWRITE_FONT_PROPERTY_ID_STYLE}) {
    BOOL exists=FALSE;ComPtr<IDWriteLocalizedStrings> strings;
    HR(set->GetPropertyValues(i,prop,&exists,&strings),"font property");result.emplace_back(exists?L"+":L"-");
    if(!exists)continue;
    for(UINT32 n=0;n<strings->GetCount();++n){UINT32 a=0,b=0;
      HR(strings->GetLocaleNameLength(n,&a),"locale length");HR(strings->GetStringLength(n,&b),"name length");
      std::vector<wchar_t> l(a+1),v(b+1);
      HR(strings->GetLocaleName(n,l.data(),a+1),"locale");HR(strings->GetString(n,v.data(),b+1),"name");
      result.emplace_back(l.data());result.emplace_back(v.data());
    }
  }
  return result;
}

int CompareStore(const wchar_t* generation,const wchar_t* objects,const wchar_t* groups_path) {
  ComPtr<IDWriteFactory> factory;
  HR(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,__uuidof(IDWriteFactory),reinterpret_cast<IUnknown**>(factory.GetAddressOf())),"DirectWrite");
  std::ifstream groups(groups_path);std::string group;size_t cases=0,faces=0,nonzero=0;
  while(std::getline(groups,group)){
    if(group.empty())continue;
    std::shared_ptr<font::CatalogSelection> policy;std::shared_ptr<font::Resources> additions;
    HR(font::LoadCountryCatalog(generation,group,&policy),"signed catalog");
    Check(bool(policy->face_index),"signed face index missing or invalid");
    HR(font::VerifyCatalogAssets(objects,*policy,&additions),"signed additions");
    std::shared_ptr<font::NativeViewSnapshot> original,indexed;ComPtr<IDWriteFontCollection> a,b;
    HR(font::CreateControlledCollection(factory.Get(),policy->native_sources,additions,&original,&a),"source baseline");
    HR(font::CreateControlledCollection(factory.Get(),policy->native_sources,additions,&indexed,&b,policy->face_index.get()),"indexed collection");
    Check(indexed->used_index && !indexed->additions_rejected && !original->additions_rejected,"expected indexed collection and all additions");
    ComPtr<IDWriteFontCollection1> a1,b1;ComPtr<IDWriteFontSet> aa,bb;
    HR(a.As(&a1),"expected collection");HR(b.As(&b1),"actual collection");
    HR(a1->GetFontSet(&aa),"expected set");HR(b1->GetFontSet(&bb),"actual set");
    Check(aa->GetFontCount()==bb->GetFontCount() && a1->GetFontFamilyCount()==b1->GetFontFamilyCount(),"set count mismatch");
    for(UINT32 i=0;i<aa->GetFontCount();++i){
      ComPtr<IDWriteFontFaceReference> x,y;HR(aa->GetFontFaceReference(i,&x),"source ref");HR(bb->GetFontFaceReference(i,&y),"index ref");
      Check(FontPath(x.Get())==FontPath(y.Get()) && x->GetFontFaceIndex()==y->GetFontFaceIndex() &&
            x->GetSimulations()==y->GetSimulations() && Properties(aa.Get(),i)==Properties(bb.Get(),i),"source, face or font properties differ");
      ComPtr<IDWriteFontFaceReference1> xx,yy;HR(x.As(&xx),"source axes");HR(y.As(&yy),"index axes");
      std::vector<DWRITE_FONT_AXIS_VALUE> ax(xx->GetFontAxisValueCount()),ay(yy->GetFontAxisValueCount());
      if(!ax.empty())HR(xx->GetFontAxisValues(ax.data(),static_cast<UINT32>(ax.size())),"source axis values");
      if(!ay.empty())HR(yy->GetFontAxisValues(ay.data(),static_cast<UINT32>(ay.size())),"index axis values");
      Check(ax.size()==ay.size() && (ax.empty() || !std::memcmp(ax.data(),ay.data(),ax.size()*sizeof(ax[0]))),"axis mismatch");
      ++faces;if(x->GetFontFaceIndex())++nonzero;
    }
    ++cases;
  }
  Check(cases>0 && faces>0,"empty comparison");
  std::cout<<"{\"verified\":true,\"groups\":"<<cases<<",\"faceComparisons\":"<<faces<<",\"nonzeroFaceIndices\":"<<nonzero<<"}\n";
  return 0;
}
}
int wmain(int argc,wchar_t** argv) {
  try {
    if(argc==5 && std::wstring(argv[1])==L"--compare")return CompareStore(argv[2],argv[3],argv[4]);
    Check(argc==3,"usage: capture-font-index pins.tsv index.bin");
    std::ifstream input(argv[1],std::ios::binary);index_data::Index index;
    Check(bool(std::getline(input,index.catalog)) && index_data::HexDigest(index.catalog),"expected signed catalog ID");
    std::vector<std::wstring> paths;std::map<std::wstring,uint16_t,LessPath> native_paths;
    font::Resources retained;std::string line;size_t stamped=0;
    while(std::getline(input,line)){
      std::vector<std::string> cells;size_t at=0,next;
      for(;;){next=line.find('\t',at);cells.push_back(line.substr(at,next-at));if(next==std::string::npos)break;at=next+1;}
      Check(cells.size()==6 && index.sources.size()<4096,"invalid pin row");
      index_data::Source s;s.kind=static_cast<uint8_t>(std::stoul(cells[0]));s.name=cells[1];s.package=cells[2];
      s.hash=cells[3];s.size=std::stoull(cells[4]);const auto path=Wide(cells[5]);
      Check(s.kind<=3 && index_data::Leaf(s.name) && index_data::HexDigest(s.hash),"invalid source pin");
      HANDLE h=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,
                           FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_SEQUENTIAL_SCAN,nullptr);
      Check(h!=INVALID_HANDLE_VALUE,"cannot open a selected source");retained.handles.push_back(h);
      BY_HANDLE_FILE_INFORMATION info{};
      Check(GetFileInformationByHandle(h,&info) && !(info.dwFileAttributes&(FILE_ATTRIBUTE_REPARSE_POINT|FILE_ATTRIBUTE_DIRECTORY)) &&
            font::CheckHash(h,{path.c_str(),s.hash,s.size,true}),"source differs from signed catalog");
      if(index_data::FileStamp(h,&s.stamp))++stamped;
      if(s.kind!=3)Check(native_paths.emplace(path,static_cast<uint16_t>(paths.size())).second,"duplicate source path");
      paths.push_back(path);index.sources.push_back(std::move(s));
    }
    ComPtr<IDWriteFactory6> factory;
    HR(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,__uuidof(IDWriteFactory6),
        reinterpret_cast<IUnknown**>(factory.GetAddressOf())),"DirectWrite 6");
    ComPtr<IDWriteFontCollection> original;ComPtr<IDWriteFontCollection1> original1;ComPtr<IDWriteFontSet> system;
    HR(static_cast<IDWriteFactory*>(factory.Get())->GetSystemFontCollection(&original),"system collection");HR(original.As(&original1),"collection 1");HR(original1->GetFontSet(&system),"system font set");
    ComPtr<IDWriteFontSetBuilder1> expected_builder,actual_builder;
    HR(factory->CreateFontSetBuilder(&expected_builder),"reference builder");HR(factory->CreateFontSetBuilder(&actual_builder),"indexed builder");
    std::vector<ComPtr<IDWriteFontFile>> files(paths.size());std::vector<unsigned> counts(paths.size());
    for(UINT32 i=0;i<system->GetFontCount();++i){
      ComPtr<IDWriteFontFaceReference> reference;HR(system->GetFontFaceReference(i,&reference),"system face reference");
      const auto it=native_paths.find(FontPath(reference.Get())); if(it==native_paths.end())continue;
      const uint16_t n=it->second;index_data::Face face;face.file=n;face.index=reference->GetFontFaceIndex();face.simulations=reference->GetSimulations();
      ComPtr<IDWriteFontFaceReference1> ref1;HR(reference.As(&ref1),"axis-aware reference");
      face.axes.resize(ref1->GetFontAxisValueCount());Check(face.axes.size()<=64,"axis count");
      if(!face.axes.empty())HR(ref1->GetFontAxisValues(face.axes.data(),static_cast<UINT32>(face.axes.size())),"axis values");
      if(!files[n])HR(factory->CreateFontFileReference(paths[n].c_str(),nullptr,&files[n]),"font file reference");
      ComPtr<IDWriteFontFaceReference1> recreated;
      HR(factory->CreateFontFaceReference(files[n].Get(),face.index,static_cast<DWRITE_FONT_SIMULATIONS>(face.simulations),
         face.axes.data(),static_cast<UINT32>(face.axes.size()),&recreated),"recreate face");
      HR(expected_builder->AddFontFaceReference(reference.Get()),"add original face");
      HR(actual_builder->AddFontFaceReference(recreated.Get()),"add indexed face");
      ++counts[n];index.faces.push_back(std::move(face));
    }
    for(size_t n=0;n<counts.size();++n)Check(index.sources[n].kind==3 || counts[n]>0,"required source absent from DirectWrite");
    ComPtr<IDWriteFontSet> expected,actual;
    HR(expected_builder->CreateFontSet(&expected),"reference font set");HR(actual_builder->CreateFontSet(&actual),"indexed font set");
    Check(expected->GetFontCount()==actual->GetFontCount(),"face count differs");size_t collection_faces=0,axes_faces=0;
    for(UINT32 i=0;i<expected->GetFontCount();++i){
      ComPtr<IDWriteFontFaceReference> a,b;HR(expected->GetFontFaceReference(i,&a),"expected face");HR(actual->GetFontFaceReference(i,&b),"indexed face");
      if(a->GetFontFaceIndex()!=0)++collection_faces;
      ComPtr<IDWriteFontFaceReference1> a1,b1;HR(a.As(&a1),"expected axes");HR(b.As(&b1),"indexed axes");
      std::vector<DWRITE_FONT_AXIS_VALUE> aa(a1->GetFontAxisValueCount()),bb(b1->GetFontAxisValueCount());
      if(!aa.empty())HR(a1->GetFontAxisValues(aa.data(),static_cast<UINT32>(aa.size())),"expected axes");
      if(!bb.empty())HR(b1->GetFontAxisValues(bb.data(),static_cast<UINT32>(bb.size())),"indexed axes");
      Check(aa.size()==bb.size() && (aa.empty() || !std::memcmp(aa.data(),bb.data(),aa.size()*sizeof(aa[0]))),"axis values differ");
      if(!aa.empty())++axes_faces;
      Check(FontPath(a.Get())==FontPath(b.Get()) && a->GetFontFaceIndex()==b->GetFontFaceIndex() &&
            a->GetSimulations()==b->GetSimulations() && Properties(expected.Get(),i)==Properties(actual.Get(),i),"indexed file, face order or native names/weight/style differ");
    }
    std::ostringstream stream(std::ios::binary);Save(stream,index);const std::string raw=stream.str();index_data::Index decoded;
    Check(index_data::Parse({raw.begin(),raw.end()},index.catalog,&decoded),"index decoder rejected generated data");
    std::ofstream output(argv[2],std::ios::binary);output.write(raw.data(),static_cast<std::streamsize>(raw.size()));Check(output.good(),"output failed");
    std::cout<<"{\"verified\":true,\"sources\":"<<paths.size()<<",\"stamped\":"<<stamped<<",\"nativeFaces\":"<<index.faces.size()
             <<",\"nonzeroCollectionIndices\":"<<collection_faces<<",\"facesWithAxes\":"<<axes_faces<<"}\n";
    return 0;
  }catch(const std::exception& error){std::cerr<<error.what()<<"\n";return 1;}
}
