"""Negative source integration gates; compiled cases run in the focused native suite."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from tools import verify_native_environment_controls_source as gate

FIXTURE = Path(__file__).with_name('fixtures') / 'native-environment-controls-156.json.gz'


class NativeEnvironmentControlSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-controls-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.files = json.loads(gzip.decompress(FIXTURE.read_bytes()))
        for name, body in self.files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding='utf-8')

    def mutate(self, name, before, after):
        path = self.root / name
        text = path.read_text(encoding='utf-8')
        self.assertIn(before, text)
        path.write_text(text.replace(before, after, 1), encoding='utf-8')
        with self.assertRaises(ValueError):
            gate.verify(self.root)

    def test_effective_source_has_all_seven_native_sites(self):
        self.assertEqual(len(gate.verify(self.root)), 7)

    def test_network_utility_must_receive_port_policy(self):
        self.mutate(gate.TARGETS[2], '"shardx-blocked-ports",', '// "shardx-blocked-ports",')

    def test_permission_query_cannot_revert_to_only_persistent_site_settings(self):
        self.mutate(gate.TARGETS[3], '? "shardx-block-microphone" : "shardx-block-camera"', '? "retired-microphone" : "retired-camera"')

    def test_actual_capture_must_still_deny_after_a_stored_grant(self):
        self.mutate(gate.TARGETS[4], 'if (IsShardXCaptureBlocked(request.audio_type) ||', 'if (false && IsShardXCaptureBlocked(request.audio_type) ||')

    def test_port_policy_cannot_be_replaced_by_comment_only_claim(self):
        self.mutate(gate.TARGETS[1], 'if (managed_ports->Contains(port)) return false;', '// if (managed_ports->Contains(port)) return false;')

    def test_invalid_explicit_port_policy_fails_closed(self):
        self.mutate(gate.TARGETS[0], 'return !valid || std::binary_search', 'return valid && std::binary_search')

    def test_decimal_accumulation_requires_pre_multiply_range_guard(self):
        self.mutate(gate.TARGETS[0], 'if (port > (65535 - digit_value) / 10)',
                    'if (port > 65535)')

    def test_only_ascii_decimal_digits_are_accepted(self):
        self.mutate(gate.TARGETS[0], "if (digit < '0' || digit > '9')",
                    "if (digit < '0' && digit > '9')")

    def test_pointer_parsing_cannot_be_hidden_by_warning_suppression(self):
        self.mutate(gate.TARGETS[0], '#include <algorithm>',
                    '#pragma clang diagnostic ignored "-Wunsafe-buffer-usage"\n#include <algorithm>')

    def test_dnt_requires_native_command_pref_layer(self):
        self.mutate(gate.TARGETS[5], 'SetValue(prefs::kEnableDoNotTrack, base::Value(dnt == "true"),', 'SetValue(prefs::kEnableDoNotTrack, base::Value(false),')

    def test_header_is_registered_in_its_native_gn_target(self):
        self.mutate('net/BUILD.gn', '"base/shardx_environment_controls.h",', '')


if __name__ == '__main__':
    unittest.main()
