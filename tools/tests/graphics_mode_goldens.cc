// Tests the production helper against independently captured browser bytes.
#include "base/uxr_graphics_noise.h"

#include <array>
#include <charconv>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <sstream>
#include <string>
#include <vector>

namespace {

[[noreturn]] void BadFixture(const char* message) {
  std::cerr << "Graphics golden input failure: " << message << "\n";
  std::abort();
}

std::vector<std::string> SplitTabs(const std::string& line) {
  std::vector<std::string> fields;
  std::istringstream input(line);
  for (std::string value; std::getline(input, value, '\t');)
    fields.push_back(value);
  return fields;
}

uint64_t Number(const std::string& text) {
  uint64_t value = 0;
  const auto parsed =
      std::from_chars(text.data(), text.data() + text.size(), value);
  if (parsed.ec != std::errc() || parsed.ptr != text.data() + text.size())
    BadFixture("Invalid fixture number");
  return value;
}

int32_t SignedNumber(const std::string& text) {
  int32_t value = 0;
  const auto parsed =
      std::from_chars(text.data(), text.data() + text.size(), value);
  if (parsed.ec != std::errc() || parsed.ptr != text.data() + text.size())
    BadFixture("Invalid signed fixture number");
  return value;
}

}  // namespace

int RunGraphicsGoldenTests(const std::string& fixture_root) {
  const std::string directory = fixture_root + "/graphics-modes-v1/";
  std::ifstream binary(directory + "pixels.bin", std::ios::binary);
  std::ifstream cases(directory + "cases.tsv");
  if (!binary || !cases)
    BadFixture("Missing graphics-modes-v1 fixtures");
  const std::vector<uint8_t> pool{std::istreambuf_iterator<char>(binary),
                                  std::istreambuf_iterator<char>()};
  std::string line;
  std::getline(cases, line);
  size_t checked = 0, failed = 0;
  std::map<std::string, size_t> groups;
  while (std::getline(cases, line)) {
    if (!line.empty() && line.back() == '\r')
      line.pop_back();
    if (line.empty())
      continue;
    const auto values = SplitTabs(line);
    if (values.size() != 20)
      BadFixture("Invalid graphics fixture column count");
    const uint64_t input_at = Number(values[14]);
    const uint64_t input_size = Number(values[15]);
    const uint64_t diff_at = Number(values[16]);
    const uint64_t diff_count = Number(values[17]);
    if (input_at > pool.size() || input_size > pool.size() - input_at ||
        diff_at > pool.size() || diff_count > (pool.size() - diff_at) / 5) {
      BadFixture("Graphics fixture range is invalid");
    }
    const auto input =
        base::span<const uint8_t>(pool).subspan(input_at, input_size);
    std::vector<uint8_t> output(input.begin(), input.end());
    std::vector<uint8_t> expected(output);
    for (size_t i = 0; i < diff_count; ++i) {
      const auto patch =
          base::span<const uint8_t>(pool).subspan(diff_at + i * 5, size_t{5});
      const uint32_t at = uint32_t{patch[0]} | (uint32_t{patch[1]} << 8) |
                          (uint32_t{patch[2]} << 16) |
                          (uint32_t{patch[3]} << 24);
      if (at >= expected.size())
        BadFixture("Graphics expected-byte index is invalid");
      expected[at] = patch[4];
    }
    base::UxrSurfacePolicy policy;
    policy.mode = static_cast<base::UxrSurfaceMode>(Number(values[2]));
    policy.seed = Number(values[4]);
    const base::UxrPixelRegion region{output,
                                      static_cast<size_t>(Number(values[12])),
                                      static_cast<uint32_t>(Number(values[6])),
                                      static_cast<uint32_t>(Number(values[7])),
                                      static_cast<uint32_t>(Number(values[8])),
                                      static_cast<uint32_t>(Number(values[9])),
                                      SignedNumber(values[10]),
                                      SignedNumber(values[11]),
                                      Number(values[13]) != 0};
    const auto result = base::UxrApplyGraphicsNoiseV1(
        policy, static_cast<base::UxrPixelSurface>(Number(values[3])), region,
        static_cast<uint32_t>(Number(values[5])),
        static_cast<base::UxrPixelFlavor>(Number(values[19])));
    ++checked;
    ++groups[values[1]];
    if (output != expected || base::UxrPixelResultName(result) != values[18]) {
      ++failed;
      if (failed <= 12) {
        size_t differences = 0;
        for (size_t i = 0; i < output.size(); ++i)
          differences += output[i] != expected[i];
        std::cerr << values[0] << ": byte differences=" << differences
                  << " result=" << base::UxrPixelResultName(result)
                  << " expected=" << values[18] << "\n";
      }
    }
  }
  const std::array<std::pair<std::string_view, uint32_t>, 5> hashes{{
      {"12345_pos", 0x6bc3f2deu},
      {"12346_pos", 0x506c3b55u},
      {"12345gpu_vendor", 3732548639u},
      {"12345gpu_renderer", 1060429488u},
      {"12345hardware_concurrency", 1292801438u},
  }};
  for (const auto& [text, value] : hashes) {
    ++checked;
    if (base::UxrCloakHashV1(text) != value)
      ++failed;
  }
  // The buffer contract must reject impossible stride/range before writes.
  std::vector<uint8_t> sentinel(32, 173);
  const auto original = sentinel;
  base::UxrSurfacePolicy policy;
  policy.mode = base::UxrSurfaceMode::kShardX;
  policy.seed = 42;
  const base::UxrPixelRegion invalid{sentinel, 4, 8, 8, 8, 8};
  ++checked;
  if (base::UxrApplyGraphicsNoiseV1(policy, base::UxrPixelSurface::kCanvas2D,
                                    invalid) !=
          base::UxrPixelResult::kInvalidLayout ||
      sentinel != original) {
    ++failed;
  }
  std::cout << "{\"graphics_cases\":" << checked << ",\"failures\":" << failed
            << ",\"groups\":{";
  bool first = true;
  for (const auto& [name, count] : groups) {
    if (!first)
      std::cout << ",";
    first = false;
    std::cout << "\"" << name << "\":" << count;
  }
  std::cout << "}}\n";
  return failed ? 1 : 0;
}

#ifdef UXR_GRAPHICS_STANDALONE
int main(int argc, char** argv) {
  return argc == 2 ? RunGraphicsGoldenTests(argv[1]) : 2;
}
#endif
