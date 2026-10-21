// Compile the real production typed helper against independent browser bytes.
#include "base/uxr_graphics_noise.h"

#include <charconv>
#include <fstream>
#include <iostream>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>

namespace {
uint64_t Number(const std::string& text) {
  uint64_t value = 0;
  const auto parsed = std::from_chars(text.data(), text.data() + text.size(), value);
  if (parsed.ec != std::errc() || parsed.ptr != text.data() + text.size()) {
    std::cerr << "Invalid golden number\n";
    std::abort();
  }
  return value;
}
}

int main(int argc, char** argv) {
  if (argc != 2)
    return 2;
  const std::string directory(argv[1]);
  std::ifstream stream(directory + "/pixels.bin", std::ios::binary);
  std::ifstream table(directory + "/cases.tsv");
  if (!stream || !table)
    return 3;
  const std::vector<uint8_t> pool{std::istreambuf_iterator<char>(stream),
                                 std::istreambuf_iterator<char>()};
  size_t checks = 0, failures = 0;
  std::string line;
  std::getline(table, line);
  while (std::getline(table, line)) {
    if (!line.empty() && line.back() == '\r')
      line.pop_back();
    std::istringstream fields(line);
    std::vector<std::string> values;
    for (std::string value; std::getline(fields, value, '\t');)
      values.push_back(value);
    if (values.size() != 13)
      return 4;
    const size_t input_at = Number(values[9]), input_bytes = Number(values[10]);
    const size_t expected_at = Number(values[11]), expected_bytes = Number(values[12]);
    if (input_at > pool.size() || input_bytes > pool.size() - input_at ||
        expected_at > pool.size() || expected_bytes > pool.size() - expected_at ||
        input_bytes != expected_bytes)
      return 5;
    std::vector<uint8_t> output(pool.begin() + input_at, pool.begin() + input_at + input_bytes);
    const std::vector<uint8_t> expected(pool.begin() + expected_at, pool.begin() + expected_at + expected_bytes);
    const uint32_t component_bytes = static_cast<uint32_t>(Number(values[6]));
    const base::UxrOriginalPixelLayout layout{
        static_cast<uint32_t>(Number(values[2])),
        static_cast<uint32_t>(Number(values[3])),
        static_cast<size_t>(Number(values[4])),
        static_cast<size_t>(Number(values[5])),
        component_bytes, 3, component_bytes * 4, 0, Number(values[8]) != 0,
        Number(values[7]) != 0, 3};
    const auto result = base::UxrApplyOriginalCloakPixelsV1(output, Number(values[1]), layout);
    ++checks;
    if (output != expected ||
        (result != base::UxrPixelResult::kApplied && result != base::UxrPixelResult::kUnchanged)) {
      ++failures;
      std::cerr << values[0] << ": output mismatch or rejected layout\n";
    }
  }
  const auto check = [&](bool condition) {
    ++checks;
    failures += !condition;
  };
  const size_t captured_cases = checks;
  check(captured_cases == 71);
  std::vector<uint8_t> bytes(8 * 8 * 4, 127);
  const auto original = bytes;
  check(base::UxrApplyOriginalCloakPixelsV1(bytes, 0, {8,8,32,0,1,3,4,0,false,true,3}) ==
        base::UxrPixelResult::kUnsupportedSeed && bytes == original);
  check(base::UxrApplyOriginalCloakPixelsV1(bytes, 12345, {8,8,31,0,1,3,4,0,false,true,3}) ==
        base::UxrPixelResult::kInvalidLayout && bytes == original);
  check(base::UxrApplyOriginalCloakPixelsV1(bytes, 12345, {8,8,32,1,1,3,4,0,false,true,3}) ==
        base::UxrPixelResult::kInvalidLayout && bytes == original);
  check(base::UxrApplyOriginalCloakPixelsV1(bytes, 12345, {7,8,32,0,1,3,4,0,false,true,3}) ==
        base::UxrPixelResult::kUnchanged && bytes == original);
  for (uint32_t component_bytes : {2u, 4u}) {
    std::vector<uint8_t> nonfinite(8 * 8 * 4 * component_bytes, 0);
    for (size_t i = 0; i < nonfinite.size(); i += component_bytes) {
      const uint32_t word = component_bytes == 2 ? 0x7e01u : 0x7fc00001u;
      for (uint32_t byte = 0; byte < component_bytes; ++byte)
        nonfinite[i + byte] = static_cast<uint8_t>(word >> (byte * 8));
    }
    const auto before = nonfinite;
    check(base::UxrApplyOriginalCloakPixelsV1(nonfinite, 12345,
          {8,8,8 * component_bytes * 4,0,component_bytes,3,component_bytes * 4,0,true,false,3}) ==
          base::UxrPixelResult::kUnchanged && nonfinite == before);
  }
  std::cout << "{\"original_pixel_checks\":" << checks << ",\"captured_cases\":" << captured_cases << ","
            << "\"failures\":" << failures << "}\n";
  return failures ? 1 : 0;
}
