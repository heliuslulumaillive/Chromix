"""Pinned-source application and mutation tests for native browser ownership.

These tests execute GNU patch and the source gate. The C++ tests included in the
overlay require the Chromium Windows unit-test target and are not claimed here.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from tools import verify_native_browser_source as gate

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name('fixtures') / 'native-browser-upstream-156.json.gz'


class NativeBrowserRecipeTests(unittest.TestCase):
    def test_google_defaults_restore_exact_upstream_sections(self):
        config = json.loads((ROOT / 'build/google-services-win155.json').read_text(encoding='utf-8'))
        self.assertIn('core/ungoogled-chromium/replace-google-search-engine-with-nosearch.patch', config['remove_patches'])
        restored = config['restore_sections']['extra/inox-patchset/0006-modify-default-prefs.patch']
        self.assertIn('chrome/browser/profiles/profile.cc', restored)
        self.assertIn('chrome/browser/ui/browser_ui_prefs.cc', restored)
        self.assertIn('components/password_manager/core/browser/password_manager.cc', restored)
        self.assertIn(gate.OVERLAY, config['restoration_overlays'])
        self.assertEqual(config['native_browser']['capabilities'], gate.CAPABILITIES)

    def test_capability_scope_preserves_user_choices_and_native_history(self):
        contract = json.loads((ROOT / gate.CAPABILITIES).read_text(encoding='utf-8'))
        self.assertEqual(contract['contract'], gate.CONTRACT)
        self.assertEqual(contract['session_restore_default'], 'launcher-explicit-global-choice')
        self.assertEqual(contract['history_retention']['permanent'], 0)
        self.assertFalse(contract['windows_avatar']['manager_image_input'])
        self.assertTrue(contract['windows_avatar']['managed_single_regular_profile'])
        self.assertEqual(contract['managed_environment']['native_profile_switching'], 'retained')
        self.assertIn('runtime acceptance is required', contract['qualification'])
        overlay = (ROOT / gate.OVERLAY).read_text(encoding='utf-8')
        self.assertNotIn('history_backend.cc', overlay)
        self.assertNotIn('keep-old-history', overlay)
        self.assertNotIn('session_startup_pref.cc', overlay)
        self.assertNotIn('pref_registry', overlay)
        self.assertNotIn('sync_preferences', overlay)


class NativeBrowserSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = shutil.which('gpatch') or shutil.which('patch')
        fallback = Path('C:/Program Files/Git/usr/bin/patch.exe')
        if not cls.program and fallback.is_file():
            cls.program = str(fallback)
        if not cls.program:
            raise unittest.SkipTest('GNU patch is required for source application')
        cls.fixture = json.loads(gzip.decompress(FIXTURE.read_bytes()))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='shardx-native-source-')
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name)
        for row in self.fixture['files']:
            data = row['content'].encode('utf-8')
            self.assertEqual(hashlib.sha256(data).hexdigest(), row['sha256'])
            target = self.source / row['path']
            self.assertTrue(target.resolve().is_relative_to(self.source.resolve()))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.apply()

    def apply(self, reverse=False):
        result = subprocess.run([self.program, '-p1', '--batch', '--fuzz=0', '--binary',
            '--no-backup-if-mismatch', '--reverse' if reverse else '--forward',
            '-i', str(ROOT / gate.OVERLAY)], cwd=self.source, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=20)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIsNone(re.search(r'fuzz|offset|FAILED', output, re.I), output)

    def change(self, name, old, new):
        path = self.source / name
        original = path.read_text(encoding='utf-8')
        self.assertEqual(original.count(old), 1)
        path.write_text(original.replace(old, new), encoding='utf-8', newline='\n')

    def test_pinned_overlay_roundtrip_and_effective_shell_gate(self):
        self.assertEqual(len(gate.verify_shell(self.source)), 7)
        self.apply(reverse=True)
        self.assertFalse((self.source / 'chrome/browser/profiles/shardx_native_browser_unittest.cc').exists())
        for row in self.fixture['files']:
            self.assertEqual(hashlib.sha256((self.source / row['path']).read_bytes()).hexdigest(), row['sha256'])

    def test_missing_relaunch_cleanup_is_rejected(self):
        self.change('chrome/browser/ui/views/frame/browser_window_property_manager_win.cc',
            '    ClearManagedRelaunchDetails(hwnd_);', '    // omitted cleanup')
        with self.assertRaisesRegex(ValueError, 'relaunch details'):
            gate.verify_shell(self.source)

    def test_lean_windows_build_requires_explicit_shellapi_declaration(self):
        self.assertIn('WIN32_LEAN_AND_MEAN', self.fixture['compile_contract']['ci_relevant_defines'])
        self.assertEqual(self.fixture['compile_contract']['required_explicit_includes'], ['shellapi.h'])
        self.change('chrome/browser/ui/views/frame/browser_window_property_manager_win.cc',
                    '#include <shellapi.h>', '// shellapi declaration omitted')
        with self.assertRaisesRegex(ValueError, 'shellapi'):
            gate.verify_shell(self.source)

    def test_managed_identity_must_not_depend_on_data_root(self):
        self.change('chrome/browser/shell_integration_win.cc',
            'profile_path.BaseName().AsUTF8Unsafe()',
            'profile_path.DirName().BaseName().AsUTF8Unsafe()')
        with self.assertRaises(ValueError):
            gate.verify_shell(self.source)

    def test_single_profile_avatar_cannot_be_removed_silently(self):
        self.change('chrome/browser/taskbar/taskbar_decorator_win.cc',
            '.GetNumberOfProfiles() <= 1 && !managed_avatar',
            '.GetNumberOfProfiles() <= 1')
        with self.assertRaisesRegex(ValueError, 'taskbar_decorator'):
            gate.verify_shell(self.source)

    def test_native_shortcut_gate_cannot_be_bypassed_by_enable_switch(self):
        self.change('chrome/browser/profiles/profile_shortcut_manager_win.cc',
            '  if (shell_integration::win::IsShardXManagedEnvironment())\n    return false;',
            '  // no environment gate')
        with self.assertRaisesRegex(ValueError, 'feature gate'):
            gate.verify_shell(self.source)

    def test_no_manager_avatar_pixels_or_native_profile_switch_block(self):
        for row in self.fixture['files']:
            source = (self.source / row['path']).read_text(encoding='utf-8')
            self.assertNotIn('shardx-avatar-path', source)
            self.assertNotIn('shardx-avatar-png', source)
        tests = (self.source / 'chrome/browser/profiles/shardx_native_browser_unittest.cc').read_text(encoding='utf-8')
        self.assertIn('ManagedEnvironmentOwnsProfileShellIdentity', tests)
        self.assertIn('InvalidManagedMarkerPreservesNativeIdentity', tests)
        self.assertEqual(len(re.findall(r'TEST\(ShardXNativeBrowserTest,', tests)), 4)


if __name__ == '__main__':
    unittest.main()
