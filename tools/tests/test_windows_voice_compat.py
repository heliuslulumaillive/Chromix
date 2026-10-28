"""Validate the native voice overlay's routing and build evidence contracts."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
PATCH = 'patches/fingerprint-modes/0010-windows-native-voice-compat.patch'


class WindowsVoiceCompatibilityTest(unittest.TestCase):
    def test_overlay_is_in_the_verified_preparation_recipe(self):
        config = json.loads((ROOT / 'build/google-services-win155.json').read_text(encoding='utf-8'))
        self.assertEqual(config['restoration_overlays'].count(PATCH), 1)
        self.assertEqual(config['restoration_overlays'][-1], PATCH)

    def test_real_backend_routing_and_native_suite_are_built(self):
        patch = (ROOT / PATCH).read_text(encoding='utf-8')
        self.assertIn('+  SetVoiceFromName(base::uxr_voice_compat::NativeVoiceName(voice));', patch)
        self.assertIn('+  base::uxr_voice_compat::AppendWindowsCompatibilityVoices(out_voices);', patch)
        self.assertIn('+      "uxr_voice_compat_tests.cc",', patch)
        self.assertIn('+    Check(voices == 0, "Windows voice compatibility routing checks");', patch)
        self.assertNotIn('third_party/blink/', patch)
        self.assertNotIn('load-extension', patch)
        self.assertNotIn('NativeMessagingHosts', patch)
        verifier = (ROOT / 'build/windows/verify-fingerprint-modes.ps1').read_text()
        self.assertIn("'voice_compat_checks'", verifier)

    def test_exact_155_catalog_and_explicit_local_scope(self):
        patch = (ROOT / PATCH).read_text(encoding='utf-8')
        pairs = re.findall(r'^\+    \{"(Google[^"\n]+)", "([a-z]{2,3}-[A-Z]{2})"\},$', patch, re.M)
        self.assertEqual(len(pairs), 19)
        self.assertEqual(len({name for name, _ in pairs}), 19)
        self.assertEqual(pairs[0], ('Google Deutsch', 'de-DE'))
        self.assertEqual(pairs[-1], ('Google 國語（臺灣）', 'zh-TW'))
        caps = json.loads((ROOT / 'build/fingerprint-modes-capabilities.json').read_text(encoding='utf-8'))
        feature = caps['windows_native_voice_compatibility']
        self.assertFalse(feature['requires_manager_extension'])
        self.assertFalse(feature['google_cloud_synthesis'])
        self.assertEqual(feature['backend'], 'Windows SAPI')


if __name__ == '__main__':
    unittest.main()
