// Native differential acceptance for the mask-before-flatness optimization.
// Compares the shipping helper with a separately named frozen 9a06334 oracle.
#ifndef BASE_UXR_MASK_FIRST_EQUIVALENCE_H_
#define BASE_UXR_MASK_FIRST_EQUIVALENCE_H_
#include "base/uxr_graphics_noise.h"
#include "base/uxr_mask_first_reference.h"
#include <algorithm>
#include <array>
#include <bit>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace base::mask_first_equivalence {
inline constexpr char kOracleRevision[] = "9a06334258ed6039c597360038183f8693347e45";
inline constexpr char kOracleSha256[] = "2bcefcc4b7b8ab34b070b964a4fdd81c643e88052b48fd0c3a75cf18d8965465";
using Bytes = std::vector<uint8_t>;
inline uint64_t compared=0,cases=0,failures=0,checksum=1469598103934665603ull;
inline uint32_t corpus_records=0,mutation_controls=0,mutation_rejections=0;
struct Args {
  uint32_t width=0,height=0,canvas_w=0,canvas_h=0;
  int32_t x=0,y=0;
  uint64_t seed=32452843;
  size_t stride=0;
  int surface=0,mode=3;
  bool floating=false,bgra=false,premultiplied=false;
  uint32_t view_w=0,view_h=0;
  int32_t view_x=0,view_y=0;
  size_t view_stride=0;
};

uint64_t Hash(const Bytes& bytes) {
  uint64_t result=1469598103934665603ull;
  for(auto value:bytes) result=(result^value)*1099511628211ull;
  return result;
}
bool Equivalent(int expected_result,const Bytes& expected,int actual_result,const Bytes& actual) {
  return expected_result==actual_result && expected==actual;
}
int Run(bool reference,uint8_t* bytes,size_t size,const uint8_t* view,size_t view_size,const Args& a) {
  UxrPixelRegion region{span<uint8_t>(bytes,size),a.stride,a.width,a.height,a.canvas_w,a.canvas_h,a.x,a.y,a.bgra,a.premultiplied};
  region.neighborhood={span<const uint8_t>(view,view_size),a.view_stride,a.view_w,a.view_h,a.view_x,a.view_y};
  if (reference) {
    const mask_first_reference::UxrSurfacePolicy policy{
        static_cast<mask_first_reference::UxrSurfaceMode>(a.mode), a.seed};
    return static_cast<int>(a.floating
        ? mask_first_reference::UxrApplyShardWebGLFloatV2(policy, region)
        : mask_first_reference::UxrApplyGraphicsNoiseV1(
              policy, static_cast<UxrPixelSurface>(a.surface), region, 0,
              mask_first_reference::UxrPixelFlavor::kCompatibleV1));
  }
  const UxrSurfacePolicy policy{static_cast<UxrSurfaceMode>(a.mode), a.seed};
  return static_cast<int>(a.floating
      ? base::UxrApplyShardWebGLFloatV2(policy, region)
      : base::UxrApplyGraphicsNoiseV1(policy, static_cast<UxrPixelSurface>(a.surface), region));
}
int before_run(uint8_t* bytes,size_t size,const uint8_t* view,size_t view_size,const Args& args) {
  return Run(true,bytes,size,view,view_size,args);
}
int after_run(uint8_t* bytes,size_t size,const uint8_t* view,size_t view_size,const Args& args) {
  return Run(false,bytes,size,view,view_size,args);
}
Bytes Scene(unsigned w,unsigned h,bool floating,int pattern,unsigned alpha,size_t stride){
 const size_t bpp=floating?16:4;Bytes out(stride*h,0x5a);uint32_t rng=0x1234abcd;
 for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++){
  auto* p=out.data()+y*stride+x*bpp;rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;
  unsigned v[4]={137,73,91,alpha};
  if(pattern==1){v[0]=(x*7+y*3)%256;v[1]=(x*3+y*11)%256;v[2]=(x*13+y*5)%256;}
  if(pattern==2){v[0]=rng&255;v[1]=(rng>>8)&255;v[2]=(rng>>16)&255;v[3]=(x+y)%9?alpha:(rng>>24);}
  if(pattern==3){v[0]=(x%2)*255;v[1]=(y%2)*255;v[2]=((x+y)%3)*127;}
  if(pattern==4&&x>w/3&&x<w*2/3&&y>h/3&&y<h*2/3){v[0]=unsigned(x*17+y*3)&255;v[1]=unsigned(x*3+y*9)&255;v[2]=unsigned(x*11+y*7)&255;}
  if(!floating){for(int c=0;c<4;c++)p[c]=uint8_t(v[c]);}
  else for(int c=0;c<4;c++){
   float f=float(v[c])/255.f;
   if(pattern==2&&c!=3){if((x+y+c)%17==0)f=std::numeric_limits<float>::infinity();if((x+y+c)%19==0)f=std::bit_cast<float>(0x7fc01235u);if((x+y+c)%23==0)f=-0.f;if((x+y+c)%29==0)f=std::numeric_limits<float>::denorm_min();}
   if(pattern==3&&c==3&&(x+y)%13==0)f=std::bit_cast<float>(0x7fc01235u);
   std::memcpy(p+c*4,&f,4);
  }
 }
 return out;
}
Bytes Crop(const Bytes& full,unsigned w,unsigned h,size_t stride,int x,int y,unsigned cw,unsigned ch,size_t outstride,size_t bpp){
 Bytes out(outstride*ch,0xa5);
 for(unsigned oy=0;oy<ch;oy++)for(unsigned ox=0;ox<cw;ox++){
  auto* p=out.data()+oy*outstride+ox*bpp;
  if(x+int(ox)<0||y+int(oy)<0||x+ox>=w||y+oy>=h)std::memset(p,0,bpp);
  else std::memcpy(p,full.data()+(y+oy)*stride+(x+ox)*bpp,bpp);
 }
 return out;
}
void Compare(Bytes input,const Bytes& view,Args a,int viewmode,const char* tag){
 Bytes b=input,c=input;const uint8_t *bv=view.data(),*cv=view.data();size_t size=view.size();
 if(viewmode==1){bv=b.data();cv=c.data();size=b.size();a.view_w=a.width;a.view_h=a.height;a.view_x=a.x;a.view_y=a.y;a.view_stride=a.stride;}
 if(viewmode==2){bv=cv=nullptr;size=0;a.view_w=a.view_h=0;a.view_stride=0;}
 const int rb=before_run(b.data(),b.size(),bv,size,a),rc=after_run(c.data(),c.size(),cv,size,a);
 cases++;compared+=b.size();checksum^=Hash(b)+uint64_t(rb);checksum*=1099511628211ull;
 if(!Equivalent(rb,b,rc,c)){failures++;if(failures<10)std::cerr<<"Mismatch "<<tag<<" case="<<cases<<" dimensions="<<a.width<<"x"<<a.height<<" origin="<<a.x<<","<<a.y<<" float="<<a.floating<<" surface="<<a.surface<<" seed="<<a.seed<<" view="<<viewmode<<"\n";}
}
void Equivalence(){
 for(bool f:{false,true})for(auto [w,h]:{std::pair{1u,1u},{31u,33u},{67u,33u},{128u,97u}})
 for(uint64_t seed:{1ull,42ull,314159ull,32452843ull,49979687ull,0xffffffffull})
 for(int pattern:{0,1,2,3,4})for(unsigned alpha:{0u,1u,64u,255u}){
  const size_t bpp=f?16:4,stride=w*bpp+8;auto full=Scene(w,h,f,pattern,alpha,stride);
  for(int surface:{0,1,2}){if(f&&surface!=1)continue;for(bool bgra:{false,true})for(bool premul:{false,true}){
   if(f&&(bgra||premul))continue;
   for(auto crop:{std::array<int,4>{0,0,int(w),int(h)},std::array<int,4>{1,1,1,1},std::array<int,4>{int(w)-1,int(h)-1,1,1},std::array<int,4>{-1,-1,5,3},std::array<int,4>{3,5,11,9}}){
    Args a;a.width=crop[2];a.height=crop[3];a.canvas_w=w;a.canvas_h=h;a.x=crop[0];a.y=crop[1];a.seed=seed;a.stride=a.width*bpp+12;a.surface=surface;a.floating=f;a.bgra=bgra;a.premultiplied=premul;a.view_w=w;a.view_h=h;a.view_stride=stride;
    auto input=Crop(full,w,h,stride,a.x,a.y,a.width,a.height,a.stride,bpp);
    for(int viewmode:{0,1,2})Compare(input,full,a,viewmode,"generated");
   }
  }}
 }
 // Retained surface modes, out-of-domain seeds and layouts.
 auto full=Scene(67,33,false,2,255,67*4);Args a;a.width=a.canvas_w=67;a.height=a.canvas_h=33;a.stride=a.view_stride=67*4;a.view_w=67;a.view_h=33;
 for(int mode:{0,1,3})for(int surface:{0,1,2})for(uint64_t seed:{0ull,1ull,0x100000000ull,~0ull}){a.mode=mode;a.surface=surface;a.seed=seed;Compare(full,full,a,0,"mode-seed");}
 a.mode=3;a.seed=42;
 for(int invalid=0;invalid<5;invalid++){Args bad=a;if(invalid==0)bad.width=0;if(invalid==1)bad.height=0;if(invalid==2)bad.stride=1;if(invalid==3)bad.view_stride=1;if(invalid==4)bad.view_h=0xffffffffu;Compare(full,full,bad,0,"invalid");}
}

void Corpus(const std::string& root) {
  std::ifstream data(root+"/pixel-semantics-v1/pixels.bin",std::ios::binary);
  std::ifstream list(root+"/pixel-semantics-v1/cases.tsv");
  if(!data||!list) {++failures;return;}
  Bytes pool{std::istreambuf_iterator<char>(data),std::istreambuf_iterator<char>()};
  std::string line;std::getline(list,line);
  while(std::getline(list,line)) {
    if(line.empty()) continue;
    std::istringstream input(line);std::string name,extra;uint64_t width=0,height=0,offset=0,size=0;
    if(!(input>>name>>width>>height>>offset>>size)||input>>extra||!width||!height||
        width>65536||height>65536||size!=width*height*4||offset>pool.size()||size>pool.size()-offset) {
      ++failures;return;
    }
    Bytes bytes(pool.begin()+offset,pool.begin()+offset+size);++corpus_records;
    for(uint64_t seed:{1ull,2ull,42ull,314159ull,32452843ull,49979687ull,123456789ull,0xffffffffull})
      for(int surface:{0,1,2})for(int viewmode:{0,1,2}) {
        Args a;a.width=a.canvas_w=a.view_w=static_cast<uint32_t>(width);
        a.height=a.canvas_h=a.view_h=static_cast<uint32_t>(height);
        a.stride=a.view_stride=width*4;a.surface=surface;a.seed=seed;
        Compare(bytes,bytes,a,viewmode,"browser-corpus");
      }
  }
  if(corpus_records!=3) ++failures;
}

void MutationControls() {
  // A positive comparison is not useful if a changed seed or output bit can
  // also pass. Exercise the same comparator with independently damaged results.
  for(int route:{0,1,2,3}) {
    Args a;a.width=a.canvas_w=a.view_w=128;a.height=a.canvas_h=a.view_h=97;
    a.floating=route==3;a.surface=a.floating?1:route;a.seed=42;
    const size_t bpp=a.floating?16:4;a.stride=a.view_stride=a.width*bpp;
    auto input=Scene(a.width,a.height,a.floating,1,255,a.stride),expected=input,actual=input;
    const int before=before_run(expected.data(),expected.size(),input.data(),input.size(),a);
    Args wrong_seed=a;wrong_seed.seed^=0x9e3779b9u;
    const int altered=after_run(actual.data(),actual.size(),input.data(),input.size(),wrong_seed);
    ++mutation_controls;
    if(!Equivalent(before,expected,altered,actual)) ++mutation_rejections;else ++failures;
    actual=input;const int current=after_run(actual.data(),actual.size(),input.data(),input.size(),a);
    actual[17]^=1u;
    ++mutation_controls;
    if(!Equivalent(before,expected,current,actual)) ++mutation_rejections;else ++failures;
  }
}
}  // namespace base::mask_first_equivalence

inline std::pair<size_t,size_t> RunPixelMaskFirstEquivalence(const std::string& fixture_root) {
  using namespace base::mask_first_equivalence;
  cases=compared=failures=0;checksum=1469598103934665603ull;
  corpus_records=mutation_controls=mutation_rejections=0;
  Equivalence();Corpus(fixture_root);MutationControls();
  if(cases!=93857||compared!=439036140||mutation_controls!=8||mutation_rejections!=8) ++failures;
  std::cout<<"{\"mask_first_oracle_revision\":\""<<kOracleRevision
           <<"\",\"mask_first_oracle_sha256\":\""<<kOracleSha256
           <<"\",\"mask_first_equivalence_cases\":"<<cases
           <<",\"mask_first_equivalence_bytes\":"<<compared
           <<",\"mask_first_corpus_records\":"<<corpus_records
           <<",\"mask_first_mutation_controls\":"<<mutation_controls
           <<",\"mask_first_mutation_rejections\":"<<mutation_rejections
           <<",\"mask_first_oracle_checksum\":"<<checksum
           <<",\"failures\":"<<failures<<"}\n";
  return {static_cast<size_t>(cases),static_cast<size_t>(failures)};
}
#endif  // BASE_UXR_MASK_FIRST_EQUIVALENCE_H_
