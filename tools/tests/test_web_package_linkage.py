"""Check the exact native link edge and execute the existing payload encoder."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tools.tests import test_security_restoration as restoration
from tools.tests.test_enterprise_signal_scope import compiler_environment

source = restoration.source

GN = 'components/web_package/BUILD.gn'
ENCODER = 'components/web_package/signed_web_bundles/signed_web_bundle_utils.cc'


class WebPackageLinkGateTest(unittest.TestCase):
    def check_gn(self, text, passes):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            restoration.SecuritySourceGateTest().fixture(root)
            (root / GN).write_text(text, encoding='utf-8')
            if passes:
                source.verify(root, require_security=True)
            else:
                with self.assertRaisesRegex(ValueError, 'signature payload implementation'):
                    source.verify(root, require_security=True)

    def test_existing_disconnected_source_set_does_not_satisfy_library(self):
        self.check_gn('source_set("web_package_rust_impl") {\n sources = [\n'
                      '"signed_web_bundles/signed_web_bundle_utils.cc",\n ]\n}\n'
                      'static_library("web_package") {\n sources = ["web_bundle_builder.cc"]\n}\n', False)

    def test_comment_or_duplicate_is_not_a_valid_link_edge(self):
        for entry in ('# "signed_web_bundles/signed_web_bundle_utils.cc",',
                      '"signed_web_bundles/signed_web_bundle_utils.cc",\n'
                      '"signed_web_bundles/signed_web_bundle_utils.cc",'):
            with self.subTest(entry=entry):
                self.check_gn('static_library("web_package") {\n sources = [\n' + entry + '\n ]\n}\n', False)

    def test_one_real_library_source_is_admitted(self):
        self.check_gn('static_library("web_package") {\n sources = [\n'
                      '"signed_web_bundles/signed_web_bundle_utils.cc",\n ]\n}\n', True)


PREAMBLE = r'''
#include <array>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <span>
#include <vector>
// Only Chromium base container/byte-writer adapters are modelled here.
// The encoder below is read unchanged from the prepared Chromium source.
#define CHECK(value) do { if (!(value)) std::abort(); } while (false)
namespace base {
template<class T, size_t N = std::dynamic_extent> using span = std::span<T, N>;
template<class C, class R> void Extend(C& c, const R& r) { c.insert(c.end(), r.begin(), r.end()); }
template<class T, size_t N> struct SpanWriter {
  std::span<T, N> output;
  explicit SpanWriter(std::span<T, N> bytes): output(bytes) {}
  bool WriteU64BigEndian(uint64_t n) {
    if (output.size() < 8) return false;
    for (size_t i = 0; i < 8; ++i) output[i] = static_cast<uint8_t>(n >> (56 - i * 8));
    return true;
  }
};
}
namespace web_package {
struct SignedWebBundleSignatureData {
  std::span<const uint8_t> unsigned_web_bundle_hash, integrity_block_cbor, attributes_cbor;
};
std::vector<uint8_t> CreateSignaturePayload(const SignedWebBundleSignatureData&);
}
'''

MAIN = r'''
int main() {
  for (int which = 0; which < 4; ++which) {
    std::vector<uint8_t> a, b, c;
    if (which == 1) { a = {0, 255, 128}; b = {1, 0}; c = {254}; }
    if (which == 2) { a.resize(256, 0x5a); b.resize(257, 0xa5); c.resize(65536, 0); }
    if (which == 3) { a = {1}; b = {2}; c = {3}; }
    const auto result = web_package::CreateSignaturePayload({a, b, c});
    for (uint8_t n : result) std::printf("%02x", n);
    std::puts("");
  }
}
'''


@unittest.skipUnless(os.environ.get('CHROMIX_WEB_PACKAGE_SOURCE'), 'Requires effective prepared Chromium source')
class WebPackageEncoderNativeTest(unittest.TestCase):
    def test_missing_definition_fails_link_and_existing_encoder_matches_wire_vectors(self):
        src = Path(os.environ['CHROMIX_WEB_PACKAGE_SOURCE'])
        text = (src / ENCODER).read_text(encoding='utf-8')
        body = text[text.index('namespace web_package {'):]
        compiler = shutil.which('clang++') or 'C:/Program Files/LLVM/bin/clang++.exe'
        root = Path(tempfile.mkdtemp(prefix='web-package-native-', dir=os.environ.get('CHROMIX_WEB_PACKAGE_EVIDENCE')))
        environment = compiler_environment(root)
        caller = root / 'caller.cc'
        caller.write_text(PREAMBLE + MAIN, encoding='utf-8')
        inputs = [(b'', b'', b''), (bytes([0, 255, 128]), bytes([1, 0]), bytes([254])),
                  (b'\x5a' * 256, b'\xa5' * 257, b'\0' * 65536), (b'\1', b'\2', b'\3')]
        expected = [''.join((len(v).to_bytes(8, 'big') + v).hex() for v in case) for case in inputs]
        records = []
        for name, implementation in [('disconnected', None), ('linked', body),
                                     ('omitted-third-input', body.replace('  AddItemToPayload(payload, data.attributes_cbor);', ''))]:
            binary = root / (name + '.exe')
            args = [compiler, '-std=c++20', '-Wall', '-Wextra', '-Werror', str(caller)]
            if implementation is not None:
                unit = root / (name + '.cc')
                unit.write_text(PREAMBLE + implementation, encoding='utf-8')
                args.append(str(unit))
            result = subprocess.run(args + ['-o', str(binary)], env=environment, capture_output=True, text=True, timeout=90)
            (root / (name + '.link.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
            record = {'case': name, 'link_exit': result.returncode}
            if implementation is None:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('CreateSignaturePayload', result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                self.assertEqual(run.returncode, 0)
                matches = run.stdout.splitlines() == expected
                self.assertEqual(matches, name == 'linked')
                record.update(run_exit=run.returncode, wire_vectors_match=matches)
            records.append(record)
        receipt = {'source_sha256': hashlib.sha256((src / ENCODER).read_bytes()).hexdigest(),
                   'cases': records, 'full_chromium_build': False,
                   'scope': 'Unchanged encoder body with base container adapters; full native library link remains a CI gate'}
        (root / 'result.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        print('web_package_native_evidence=' + str(root / 'result.json'))


if __name__ == '__main__':
    unittest.main()
