#!/usr/bin/env python3
"""Execute the production metadata entrypoint with deterministic dependencies.

This verifies branch/override control flow, not Chromium ABI or browser runtime.
Pass the actual restored source and optionally an older source as a negative control.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess

PREFIX = r'''
#include <iostream>
#include <map>
#include <optional>
#include <string>
#include <vector>
namespace blink {
namespace features { constexpr int kReducedSystemInfo = 1, kUACHOverrideBlank = 2; }
struct UserAgentMetadata {
  std::vector<std::string> brand_version_list, brand_full_version_list, form_factors;
  std::string platform, full_version, architecture, model, bitness, platform_version;
  bool mobile = false, wow64 = false;
  bool operator==(const UserAgentMetadata&) const = default;
};
}
bool reduced = false, blank = false;
namespace base {
class CommandLine {
 public:
  std::map<std::string, std::string> values;
  static CommandLine* ForCurrentProcess() { static CommandLine command; return &command; }
  bool HasSwitch(const char* key) const { return values.contains(key); }
  std::string GetSwitchValueASCII(const char* key) const {
    auto found = values.find(key); return found == values.end() ? "" : found->second;
  }
};
struct FeatureList {
  static bool IsEnabled(int flag) { return flag == blink::features::kReducedSystemInfo ? reduced : blank; }
};
}
std::vector<std::string> GetUserAgentBrandMajorVersionListInternal(std::optional<int>) { return {"Chromium/155"}; }
std::vector<std::string> GetUserAgentBrandFullVersionListInternal(std::optional<int>) { return {"Chromium/155.1.2.3"}; }
bool GetMobileBitForUAMetadata() { return false; }
std::string GetPlatformForUAMetadata() { return "test-platform"; }
std::optional<std::string> GetUserAgentFromCommandLine() {
  auto* cmd = base::CommandLine::ForCurrentProcess();
  return cmd->HasSwitch("user-agent") ? std::make_optional(cmd->GetSwitchValueASCII("user-agent")) : std::nullopt;
}
std::string GetEffectiveBrowserBrandFullVersion() { return "155.1.2.3"; }
std::string GetCpuArchitecture() { return "x86"; }
std::string BuildModelInfo() { return "test-model"; }
std::string GetCpuBitness() { return "64"; }
bool IsWoW64() { return false; }
std::string GetPlatformVersion() { return "19.0.0"; }
std::vector<std::string> GetFormFactorsClientHint(const blink::UserAgentMetadata&, bool) { return {"Desktop"}; }
'''

SUFFIX = r'''
int main() {
  auto& cmd = *base::CommandLine::ForCurrentProcess();
  int checks = 0, failures = 0;
  auto expect = [&](bool condition) { ++checks; if (!condition) ++failures; };
  const std::vector<const char*> keys = {"fingerprint-platform", "uxr-ua-brand",
      "uxr-ua-brand-version", "uxr-ua-full-version", "uxr-ua-platform",
      "uxr-ua-platform-version", "uxr-ua-arch", "uxr-ua-bitness", "uxr-ua-model", "uxr-ua-wow64"};
  for (bool blank_value : {false, true}) {
    blank = blank_value;
    for (bool reduced_value : {false, true}) {
      reduced = reduced_value;
      for (bool low : {false, true}) {
        cmd.values.clear();
        const auto native = GetUserAgentMetadata(low);
        expect(native.brand_version_list.size() == 1);
        expect(native.brand_full_version_list.empty() == (reduced || low));
        cmd.values["user-agent"] = "Complete custom UA/17";
        auto raw = GetUserAgentMetadata(low);
        expect(raw.brand_full_version_list.empty());
        expect(raw.architecture.empty() && raw.platform_version.empty());
        expect(raw.brand_version_list.empty() == blank);
        for (const auto* key : {"fingerprint", "uxr-fingerprint-seed", "uxr-identity-mode",
                                "uxr-ua-os", "uxr-ua-unknown"}) {
          cmd.values[key] = "12345";
          expect(GetUserAgentMetadata(low) == raw);
          cmd.values.erase(key);
        }
        for (const auto* key : keys) {
          for (const std::string& value : {std::string("explicit"), std::string("")}) {
            cmd.values = {{key, value}};
            const auto structured = GetUserAgentMetadata(low);
            cmd.values["user-agent"] = "Complete custom UA/17";
            expect(GetUserAgentMetadata(low) == structured);
            cmd.values["fingerprint"] = "12345";
            expect(GetUserAgentMetadata(low) == structured);
            expect(GetUserAgentFromCommandLine() == "Complete custom UA/17");
          }
        }
      }
    }
  }
  reduced = false;
  blank = true;
  cmd.values = {{"user-agent", "Complete custom UA/17"}, {"uxr-ua-arch", "arm"},
                {"uxr-ua-model", "TestModel"}, {"uxr-ua-bitness", "32"},
                {"uxr-ua-wow64", "true"}, {"uxr-ua-platform-version", "13.7.2"}};
  const auto metadata = GetUserAgentMetadata(false);
  expect(metadata.architecture == "arm");
  expect(metadata.model == "TestModel");
  expect(metadata.bitness == "32");
  expect(metadata.wow64);
  expect(metadata.platform_version == "13.7.2");
  expect(metadata.brand_full_version_list.size() == 1);
  expect(metadata.full_version == "155.1.2.3");
  expect(metadata.form_factors == std::vector<std::string>{"Desktop"});
  const auto low = GetUserAgentMetadata(true);
  expect(low.brand_version_list.size() == 1 && low.platform == "test-platform");
  expect(low.full_version.empty() && low.architecture.empty() && low.form_factors.empty());
  std::cout << "{\"checks\":" << checks << ",\"failed\":" << failures << "}\n";
  return failures ? 1 : 0;
}
'''


def function(path):
    text = path.read_text(encoding='utf-8')
    start = text.index('blink::UserAgentMetadata GetUserAgentMetadata(')
    end = text.index('\n}\n', start) + 2
    return text[start:end]


def run(source, out, label, compiler, vcvars):
    body = function(source)
    cpp = out / (label + '.cc')
    exe = out / (label + '.exe')
    cpp.write_text(PREFIX + '\n' + body + '\n' + SUFFIX, encoding='utf-8', newline='\n')
    build = out / (label + '-build.cmd')
    build.write_text('@echo off\ncall "' + str(vcvars) + '" >nul\nif errorlevel 1 exit /b 1\n' +
        '"' + str(compiler) + '" -std=c++20 -O2 -Wall -Wextra -Werror "' + str(cpp) +
        '" -o "' + str(exe) + '"\n', encoding='utf-8', newline='\r\n')
    compiled = subprocess.run(['cmd.exe', '/d', '/c', str(build)], capture_output=True, text=True)
    (out / (label + '-build.log')).write_text(compiled.stdout + compiled.stderr, encoding='utf-8')
    if compiled.returncode:
        raise RuntimeError('Harness compilation failed: ' + label)
    observed = subprocess.run([str(exe)], capture_output=True, text=True)
    (out / (label + '-run.log')).write_text(observed.stdout + observed.stderr, encoding='utf-8')
    result = json.loads(observed.stdout)
    result.update(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  production_function_sha256=hashlib.sha256(body.encode()).hexdigest(),
                  exit_code=observed.returncode, source=str(source))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, default=Path(r'C:\Program Files\LLVM\bin\clang++.exe'))
    parser.add_argument('--vcvars', type=Path, default=Path(r'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat'))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = {'qualification': 'Extracted production entrypoint with deterministic dependency stubs; not Chromium runtime acceptance.',
              'current': run(args.source, args.out, 'current', args.compiler, args.vcvars)}
    if args.baseline:
        result['baseline'] = run(args.baseline, args.out, 'baseline', args.compiler, args.vcvars)
    passed = result['current']['exit_code'] == 0 and result['current']['failed'] == 0
    if args.baseline:
        passed = passed and result['baseline']['exit_code'] == 1 and result['baseline']['failed'] > 0
    result['status'] = 'passed' if passed else 'failed'
    (args.out / 'report.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
