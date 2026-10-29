"""Validate the native voice overlay's routing and build evidence contracts."""
import hashlib
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
PATCH = 'patches/fingerprint-modes/0010-windows-native-voice-compat.patch'


class WindowsVoiceCompatibilityTest(unittest.TestCase):
    def test_ci_restoration_contains_the_backend_and_executed_suite(self):
        # CI applies this aggregate, not restoration_overlays individually.
        # The failed 40692824400 run shipped the previous DLL because this
        # aggregate had not been regenerated after adding the voice overlay.
        combined = ROOT / 'patches/google-services/0001-restore-google-services.patch'
        compiled = combined.read_text(encoding='utf-8')
        for fragment in (
            '+++ b/base/uxr_voice_compat.h',
            '+++ b/base/uxr_voice_compat_tests.cc',
            '+      "uxr_voice_compat_tests.cc",',
            '+    const int voices = RunVoiceCompatibilityTests();',
            '+  SetVoiceFromName(base::uxr_voice_compat::NativeVoiceName(voice));',
            '+  base::uxr_voice_compat::AppendWindowsCompatibilityVoices(out_voices);',
        ):
            self.assertTrue(fragment in compiled, 'CI aggregate is missing: ' + fragment)
        receipt = json.loads(combined.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['patch_sha256'], hashlib.sha256(combined.read_bytes()).hexdigest())
        self.assertIn('content/browser/speech/tts_win.cc', receipt['changed_files'])

    def test_overlay_manifest_matches_the_actual_ci_recipe(self):
        config = json.loads((ROOT / 'build/google-services-win155.json').read_text(encoding='utf-8'))
        manifest = json.loads((ROOT / 'patches/fingerprint-modes/manifest.json').read_text(encoding='utf-8'))
        active = [name for name in config['restoration_overlays']
                  if name.startswith('patches/fingerprint-modes/')]
        self.assertEqual([entry['path'] for entry in manifest['ordered_overlays']], active)
        for entry in manifest['ordered_overlays']:
            self.assertEqual(entry['sha256'], hashlib.sha256((ROOT / entry['path']).read_bytes()).hexdigest())
        combined = ROOT / 'patches/google-services/0001-restore-google-services.patch'
        self.assertEqual(manifest['combined_patch_sha256'], hashlib.sha256(combined.read_bytes()).hexdigest())

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
