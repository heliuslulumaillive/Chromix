// Properties of the production implementation; no duplicate noise algorithm.
#ifndef BASE_UXR_PIXEL_SEMANTIC_PROPERTIES_H_
#define BASE_UXR_PIXEL_SEMANTIC_PROPERTIES_H_
#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

inline std::pair<size_t, size_t> RunPixelSemanticProperties(const std::string& fixture_root) {
  size_t checks = 0, failures = 0;
  auto check = [&](bool condition) { ++checks; failures += !condition; };
  const std::array<uint32_t, 8> seeds{1, 2, 42, 314159, 32452843, 49979687,
                                     123456789, 0xffffffffu};
  size_t density_pixels = 0, density_changed = 0, positive_cases = 0;
  size_t old_canvas_changed = 0, new_canvas_changed = 0;
  auto changed_pixels = [](const auto& a, const auto& b) {
    size_t n = 0;
    for (size_t i = 0; i < a.size(); i += 4)
      n += !std::equal(a.begin() + i, a.begin() + i + 4, b.begin() + i);
    return n;
  };
  for (const auto [w, h] : {std::pair{128u, 96u}, {257u, 65u}, {67u, 33u}}) {
    for (uint32_t seed : seeds) {
      base::UxrSurfacePolicy policy;
      policy.mode = base::UxrSurfaceMode::kShardX;
      policy.seed = seed;
      for (auto surface : {base::UxrPixelSurface::kCanvas2D,
                           base::UxrPixelSurface::kWebGLReadback}) {
        for (bool premultiplied : {false, true}) {
          if (surface == base::UxrPixelSurface::kCanvas2D && premultiplied)
            continue;
          for (uint8_t alpha : {uint8_t{1}, uint8_t{64}, uint8_t{255}}) {
            std::vector<uint8_t> original(w * h * 4);
            for (uint32_t y = 0; y < h; ++y) for (uint32_t x = 0; x < w; ++x) {
              const size_t i = (y * w + x) * 4;
              const unsigned range = premultiplied ? alpha + 1 : 256;
              original[i] = (x * 7 + y * 11 + 13) % range;
              original[i + 1] = (x * 3 + y * 5 + 17) % range;
              original[i + 2] = (x * 11 + y * 19 + 29) % range;
              original[i + 3] = alpha;
              if (x >= 14 && x < 48 && y >= 10 && y < 28) {
                original[i] = 13 % range; original[i + 1] = 27 % range;
                original[i + 2] = 39 % range;
              }
            }
            auto apply = [&](std::vector<uint8_t>& output,
                             uint32_t ox, uint32_t oy, uint32_t width,
                             uint32_t height, bool own_halo) {
              base::UxrPixelRegion r{output, width * size_t{4}, width, height,
                  w, h, static_cast<int32_t>(ox), static_cast<int32_t>(oy),
                  false, premultiplied};
              std::vector<uint8_t> halo;
              if (own_halo) {
                const uint32_t left = ox ? ox - 1 : 0, top = oy ? oy - 1 : 0;
                const uint32_t right = std::min(w, ox + width + 1);
                const uint32_t bottom = std::min(h, oy + height + 1);
                halo.resize((right - left) * (bottom - top) * 4);
                for (uint32_t row = top; row < bottom; ++row)
                  std::copy_n(original.begin() + (row * w + left) * 4,
                      (right - left) * 4,
                      halo.begin() + (row - top) * (right - left) * 4);
                r.neighborhood = {halo, (right - left) * size_t{4}, right-left,
                    bottom-top, static_cast<int32_t>(left), static_cast<int32_t>(top)};
              } else {
                r.neighborhood = {output, w * size_t{4}, w, h, 0, 0};
              }
              return base::UxrApplyGraphicsNoiseV1(policy, surface, r);
            };
            auto full = original; apply(full, 0, 0, w, h, false);
            auto repeat = original; apply(repeat, 0, 0, w, h, true);
            check(full == repeat);
            auto twice = full; apply(twice, 0, 0, w, h, false);
            check(full == twice);
            const size_t altered = changed_pixels(original, full);
            if (alpha == 255) {
              density_pixels += w * h; density_changed += altered;
              positive_cases += altered > 0;
              check(altered > 0);
              check(altered * 100 < w * h * 2);  // below 2% of pixels
            }
            for (size_t i = 0; i < full.size(); ++i) {
              check(i % 4 == 3 ? full[i] == original[i] :
                    std::abs(int(full[i]) - int(original[i])) <= 1);
              if (premultiplied && i % 4 != 3)
                check(full[i] <= alpha);
            }
            for (uint32_t y = 12; y < 26; ++y) for (uint32_t x = 16; x < 46; ++x)
              check(std::equal(full.begin() + (y*w+x)*4,
                  full.begin() + (y*w+x)*4 + 4, original.begin() + (y*w+x)*4));
            for (const auto [ox, oy, cw, ch] : {
                std::array<uint32_t,4>{0,0,1,1}, {1,1,1,1}, {w-1,h-1,1,1},
                {3,5,31,17}, {16,11,29,15}, {w-3,h-3,3,3}}) {
              std::vector<uint8_t> part(cw * ch * 4), expected(part.size());
              for (uint32_t y = 0; y < ch; ++y) {
                std::copy_n(original.begin() + ((oy+y)*w+ox)*4, cw*4, part.begin()+y*cw*4);
                std::copy_n(full.begin() + ((oy+y)*w+ox)*4, cw*4, expected.begin()+y*cw*4);
              }
              apply(part, ox, oy, cw, ch, true);check(part == expected);
            }
            std::vector<uint8_t> flat(w*h*4);
            for (size_t i=0;i<flat.size();i+=4) {
              flat[i]=premultiplied ? alpha/3 : 99;
              flat[i+1]=premultiplied ? alpha/2 : 77;
              flat[i+2]=premultiplied ? alpha/4 : 155;flat[i+3]=alpha;
            }
            const auto flat_before=flat;
            apply(flat,0,0,w,h,false);check(flat==flat_before);
            if (surface == base::UxrPixelSurface::kCanvas2D && alpha==255) {
              auto old=original;
              base::UxrPixelRegion old_region{old,w*size_t{4},w,h,w,h};
              base::UxrApplyGraphicsNoiseV1(policy,surface,old_region,0,
                  base::UxrPixelFlavor::kReferenceV1);
              old_canvas_changed+=changed_pixels(old,original);
              new_canvas_changed+=altered;
            }
            if (surface == base::UxrPixelSurface::kWebGLReadback) {
              std::vector<uint8_t> exported(original.size()),expected(original.size());
              auto straight=[&](uint8_t v){return premultiplied && alpha!=255
                  ? uint8_t((uint32_t{v}*(0xff000000u/alpha)+0x800000u)>>24) : v;};
              for(uint32_t y=0;y<h;++y) for(uint32_t x=0;x<w;++x) {
                const size_t s=(y*w+x)*4,d=((h-1-y)*w+x)*4;
                for(uint32_t c=0;c<3;++c) {
                  exported[d+2-c]=straight(original[s+c]);
                  expected[d+2-c]=straight(full[s+c]);
                }
                exported[d+3]=alpha;expected[d+3]=alpha;
              }
              base::UxrPixelRegion r{exported,w*size_t{4},w,h,w,h,0,0,true,premultiplied};
              r.neighborhood={exported,r.row_stride,w,h,0,0};
              base::UxrApplyGraphicsNoiseV1(policy,base::UxrPixelSurface::kWebGLExport,r);
              check(exported==expected);
            }
          }
        }
      }
      // RGBA32F protects flat cells and keeps crop/assignment invariance.
      std::vector<float> floats(w*h*4);
      for(uint32_t y=0;y<h;++y)for(uint32_t x=0;x<w;++x) {
        size_t i=(y*w+x)*4;
        floats[i]=(x+1)/float(w+3);floats[i+1]=(y+1)/float(h+5);
        floats[i+2]=(x+y+3)/float(w+h+7);floats[i+3]=1;
      }
      auto span_bytes=[](auto& v){return base::span<uint8_t>(reinterpret_cast<uint8_t*>(v.data()),v.size()*sizeof(float));};
      auto full=floats;auto bytes=span_bytes(full);
      base::UxrPixelRegion r{bytes,w*size_t{16},w,h,w,h};
      r.neighborhood={bytes,r.row_stride,w,h,0,0};
      base::UxrApplyShardWebGLFloatV2(policy,r);check(full!=floats);
      auto twice=full;auto twice_bytes=span_bytes(twice);r.bytes=twice_bytes;r.neighborhood.bytes=twice_bytes;
      base::UxrApplyShardWebGLFloatV2(policy,r);check(twice==full);
      std::vector<float> part(17*13*4);
      for(uint32_t y=0;y<13;++y)std::copy_n(floats.begin()+((y+3)*w+5)*4,17*4,part.begin()+y*17*4);
      auto pbytes=span_bytes(part),sourcebytes=span_bytes(floats);
      base::UxrPixelRegion crop{pbytes,17*size_t{16},17,13,w,h,5,3};
      crop.neighborhood={sourcebytes,w*size_t{16},w,h,0,0};
      base::UxrApplyShardWebGLFloatV2(policy,crop);
      for(uint32_t y=0;y<13;++y)check(std::equal(part.begin()+y*17*4,part.begin()+(y+1)*17*4,full.begin()+((y+3)*w+5)*4));
      std::fill(floats.begin(),floats.end(),0.375f);
      for(size_t i=3;i<floats.size();i+=4)floats[i]=1;
      const auto before=floats;auto fb=span_bytes(floats);
      base::UxrPixelRegion flat{fb,w*size_t{16},w,h,w,h};flat.neighborhood={fb,flat.row_stride,w,h,0,0};
      base::UxrApplyShardWebGLFloatV2(policy,flat);check(floats==before);
    }
  }
  // Retain the existing 64x64 runtime fixture's bounded amplitude/density.
  // This is not a claim that all script-authored pixels have provenance guards.
  {
    std::vector<uint8_t> pixels(64*64*4);
    for(size_t p=0;p<4096;++p){pixels[p*4]=(p*17+31)&255;pixels[p*4+1]=(p*37+66)&255;pixels[p*4+2]=(p*13+123)&255;pixels[p*4+3]=255;}
    const auto before=pixels;
    base::UxrSurfacePolicy policy;policy.mode=base::UxrSurfaceMode::kShardX;policy.seed=314159;
    base::UxrPixelRegion region{pixels,64*size_t{4},64,64,64,64};
    region.neighborhood={pixels,region.row_stride,64,64,0,0};
    base::UxrApplyGraphicsNoiseV1(policy,base::UxrPixelSurface::kCanvas2D,region);
    size_t changed=0,maximum=0,alpha=0;
    for(size_t i=0;i<pixels.size();++i){changed+=pixels[i]!=before[i];alpha+=i%4==3&&pixels[i]!=before[i];maximum=std::max(maximum,size_t(std::abs(int(pixels[i])-int(before[i]))));}
    const bool ok=changed>0&&changed<=32&&maximum<=1&&alpha==0;check(ok);
    std::cout<<"{\"pixel_runtime_64_changed\":"<<changed<<",\"maximum_delta\":"<<maximum<<",\"alpha_changes\":"<<alpha<<",\"failures\":"<<!ok<<"}\n";
  }
  // These inputs were rendered by an independent native Chrome, not generated
  // from the candidate algorithm or inferred from a detector score.
  std::ifstream corpus(fixture_root + "/pixel-semantics-v1/cases.tsv");
  std::ifstream bytes_file(fixture_root + "/pixel-semantics-v1/pixels.bin", std::ios::binary);
  check(corpus.good() && bytes_file.good());
  const std::vector<uint8_t> corpus_bytes{std::istreambuf_iterator<char>(bytes_file),
                                        std::istreambuf_iterator<char>()};
  size_t corpus_records = 0;
  std::string line;std::getline(corpus,line);
  while (std::getline(corpus,line)) {
    std::istringstream row(line);std::string name;
    uint32_t w=0,h=0;size_t offset=0,length=0;
    row >> name >> w >> h >> offset >> length;
    if (!w || !h || length!=w*size_t{4}*h || offset>corpus_bytes.size() ||
        length>corpus_bytes.size()-offset) {check(false);continue;}
    const std::vector<uint8_t> original(corpus_bytes.begin()+offset,
                                       corpus_bytes.begin()+offset+length);
    ++corpus_records;
    for(uint32_t seed:seeds) {
      base::UxrSurfacePolicy policy;policy.mode=base::UxrSurfaceMode::kShardX;policy.seed=seed;
      auto old=original,now=original;
      base::UxrPixelRegion before{old,w*size_t{4},w,h,w,h};
      base::UxrApplyGraphicsNoiseV1(policy,base::UxrPixelSurface::kCanvas2D,before,0,
                                   base::UxrPixelFlavor::kReferenceV1);
      base::UxrPixelRegion after{now,w*size_t{4},w,h,w,h};
      after.neighborhood={now,after.row_stride,w,h,0,0};
      base::UxrApplyGraphicsNoiseV1(policy,base::UxrPixelSurface::kCanvas2D,after);
      const auto old_count=changed_pixels(old,original),new_count=changed_pixels(now,original);
      size_t maximum=0;for(size_t i=0;i<now.size();++i)maximum=std::max(maximum,size_t(std::abs(int(now[i])-int(original[i]))));
      check(new_count>0);check(maximum<=1);check(new_count*100<static_cast<size_t>(w)*h);
      std::cout << "{\"pixel_semantic_corpus\":\"" << name << "\",\"seed\":" << seed
                << ",\"pixels\":" << w*size_t{h} << ",\"old_changed\":" << old_count
                << ",\"new_changed\":" << new_count << ",\"maximum_delta\":" << maximum
                << ",\"failures\":" << (new_count==0 || maximum>1 ? 1 : 0) << "}\n";
    }
  }
  check(corpus_records > 0);
  std::cout << "{\"pixel_semantic_checks\":" << checks << ",\"pixel_semantic_corpus_records\":" << corpus_records
            << ",\"failures\":" << failures
            << ",\"positive_complex_cases\":" << positive_cases
            << ",\"tested_complex_pixels\":" << density_pixels
            << ",\"changed_complex_pixels\":" << density_changed
            << ",\"old_canvas_changed\":" << old_canvas_changed
            << ",\"new_canvas_changed\":" << new_canvas_changed << "}\n";
  return {checks,failures};
}
#endif
