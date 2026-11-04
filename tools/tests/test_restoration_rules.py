"""Upstream rule drift must fail before legacy restoration changes any source."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools import restoration_rules as rules

PATCH = ('--- a/prefs.cc\n+++ b/prefs.cc\n'
         '@@ -1,2 +1,2 @@\n-kSigninAllowed\n+signin_disabled\n context\n'
         '@@ -20,2 +20,2 @@\n-old_unrelated\n+new_unrelated\n context\n')
OTHER = '--- a/other.cc\n+++ b/other.cc\n@@ -1 +1 @@\n-old\n+new\n'


class RestorationRulesTest(unittest.TestCase):
    def setUp(self):
        self.config = {'chromium_version': '156.0.8078.79', 'core_commit': 'a' * 40,
                       'windows_commit': 'b' * 40, 'remove_patches': ['removed.patch'],
                       'restore_sections': {},
                       'restore_hunks': {'prefs.patch': {'prefs.cc': ['kSigninAllowed']}},
                       'restore_windows_sections': {'windows.patch': ['other.cc']}}
        self.core = [('prefs.patch', PATCH), ('removed.patch', OTHER)]
        self.windows = [('windows.patch', OTHER)]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / rules.MANIFEST).parent.mkdir(parents=True)
        (self.repo / rules.MANIFEST).write_text(json.dumps(rules.inventory(self.config, self.core, self.windows)))

    def test_reviewed_selection_preserves_unrelated_hunk(self):
        rules.validate(self.repo, self.config, self.core, self.windows)
        result = rules.retain_hunks(PATCH, ['kSigninAllowed'])
        self.assertNotIn('kSigninAllowed', result)
        self.assertIn('new_unrelated', result)

    def test_mixed_hunk_drift_is_rejected_even_when_marker_still_hits(self):
        changed = PATCH.replace(' context\n', '-other_setting\n+changed_setting\n', 1)
        self.assertIn('kSigninAllowed', changed)
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            rules.validate(self.repo, self.config, [('prefs.patch', changed), self.core[1]], self.windows)

    def test_missing_marker_is_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError, 'marker did not match'):
            rules.retain_hunks(PATCH, ['renamed_symbol'])
        config = copy.deepcopy(self.config)
        config['restore_hunks']['prefs.patch']['prefs.cc'].append('typo')
        with self.assertRaisesRegex(ValueError, 'marker did not match'):
            rules.inventory(config, self.core, self.windows)

    def test_renamed_patch_or_section_requires_a_port(self):
        for core in ([('renamed.patch', PATCH), self.core[1]],
                     [('prefs.patch', PATCH.replace('prefs.cc', 'new.cc')), self.core[1]]):
            with self.subTest(core=core), self.assertRaisesRegex(ValueError, 'did not match upstream'):
                rules.validate(self.repo, self.config, core, self.windows)
        config = copy.deepcopy(self.config)
        config['remove_patches'] = ['no-longer-present.patch']
        with self.assertRaisesRegex(ValueError, 'did not match upstream'):
            rules.inventory(config, self.core, self.windows)

    def test_extra_matching_hunk_is_not_accepted(self):
        changed = PATCH.replace('old_unrelated', 'kSigninAllowed')
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            rules.validate(self.repo, self.config, [('prefs.patch', changed), self.core[1]], self.windows)

    def test_upstream_patch_order_cannot_drift(self):
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            rules.validate(self.repo, self.config, list(reversed(self.core)), self.windows)

    def test_changed_core_pin_or_rule_scope_requires_review(self):
        for field in ('core_commit', 'windows_commit', 'chromium_version'):
            config = copy.deepcopy(self.config)
            config[field] += 'changed'
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'identity changed'):
                rules.validate(self.repo, config, self.core, self.windows)
        config = copy.deepcopy(self.config)
        config['restore_sections'] = {'prefs.patch': ['prefs.cc']}
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            rules.inventory(config, self.core, self.windows)

    def test_duplicate_series_sections_and_markers_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate core'):
            rules.inventory(self.config, self.core * 2, self.windows)
        with self.assertRaisesRegex(ValueError, 'Duplicate upstream patch section'):
            rules.sections(PATCH + PATCH)
        with self.assertRaisesRegex(ValueError, 'unique strings'):
            rules.retain_hunks(PATCH, ['kSigninAllowed', 'kSigninAllowed'])

    def test_committed_manifest_has_exact_pins_and_no_zero_match_rules(self):
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / rules.MANIFEST).read_text())
        config = json.loads((root / rules.CONFIG).read_text())
        for key in ('chromium_version', 'core_commit', 'windows_commit'):
            self.assertEqual(manifest[key], config[key])
        for row in manifest['rules']:
            self.assertEqual(row['hunk_count'], len(row['hunks_sha256']))
            self.assertTrue(row['hunks_sha256'])
            if row['kind'] == 'restore_hunks':
                self.assertTrue(all(n > 0 for n in row['marker_hunk_counts'].values()))
                self.assertNotIn(row['path'], config['restore_sections'].get(row['patch'], []))


if __name__ == '__main__':
    unittest.main()
