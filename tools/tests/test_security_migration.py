"""Reviewed same-156 payload migration is explicit and content-bound."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
import re

from tools import security_checkpoint as c
from tools import security_migration as m
with patch.object(sys, 'path', [str(Path(__file__).resolve().parents[1]), *sys.path]):
    from tools import prepare_google_services as preparation


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.work = self.root / 'work'
        self.work.mkdir()
        self.old = hashlib.sha256(b'old patch').hexdigest()
        self.new = hashlib.sha256(b'new patch').hexdigest()
        self.rule = {'id': 'test-transition', 'chromium_version': m.VERSION,
                     'core_commit': m.CORE, 'windows_commit': m.WINDOWS, 'arch': 'x64',
                     'source_commit': 'a' * 40, 'source_recipe_sha256': 'b' * 64,
                     'target_recipe_sha256': 'c' * 64, 'source_preparation_sha256': 'd' * 64,
                     'target_preparation_sha256': 'e' * 64,
                     'source_aggregate_sha256': self.old, 'target_aggregate_sha256': self.new}
        history = self.repo / 'patches/google-services/history'
        history.mkdir(parents=True)
        (history / (self.old + '.patch')).write_bytes(b'old patch')
        (history.parent / '0001-restore-google-services.patch').write_bytes(b'new patch')
        self.write_policy()
        self.receipt = {'variant': 'google-services-self-use', 'build_variant': 'fingerprint-modes-win156',
                        'chromium_version': m.VERSION, 'patch_sha256': self.old,
                        'preparation_sha256': 'd' * 64}
        (self.work / 'google-services-source.json').write_text(json.dumps(self.receipt))
        self.manifest = {**c.IDENTITY, 'source_commit': 'a' * 40, 'recipe_sha256': 'b' * 64,
                         'run_id': '123', 'stage': 1, 'gn_args_sha256': 'f' * 64,
                         'source_receipt_sha256': m.sha256(self.work / 'google-services-source.json')}
        self.manifest_path = self.root / 'checkpoint.json'
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.addCleanup(patch.stopall)
        patch.object(m, 'recipe', return_value='c' * 64).start()

    def write_policy(self):
        (self.repo / 'build').mkdir(exist_ok=True)
        (self.repo / m.POLICY).write_text(json.dumps({'schema_version': 1, 'migrations': [self.rule]}))

    def test_exact_transition_has_separate_source_and_target_receipts(self):
        original_bytes = (self.work / 'google-services-source.json').read_bytes()
        result = m.verify_source(self.repo, self.work, self.manifest_path)
        self.assertEqual(result['status'], 'source-admitted')
        self.assertEqual((self.work / '.security-origin/source-receipt.json').read_bytes(), original_bytes)
        self.receipt.update(patch_sha256=self.new, preparation_sha256='e' * 64,
                            unchanged_source_timestamps_preserved=27)
        (self.work / 'google-services-source.json').write_text(json.dumps(self.receipt))
        result = m.verify_target(self.repo, self.work)
        self.assertEqual(result['status'], 'source-transition-complete')
        self.assertEqual(result['unchanged_source_timestamps_preserved'], 27)
        self.assertEqual(result['native_acceptance'], 'not_tested')

    def test_unregistered_source_recipe_or_version_is_rejected(self):
        for key, value in [('recipe_sha256', '1' * 64), ('source_commit', '1' * 40),
                           ('chromium_version', '155.0.8059.39'), ('arch', 'arm64')]:
            manifest = copy.deepcopy(self.manifest)
            manifest[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                m.registered_rule(self.repo, manifest)

    def test_missing_history_or_wrong_target_patch_rejected(self):
        p = self.repo / 'patches/google-services/0001-restore-google-services.patch'
        p.write_bytes(b'not the accepted target')
        with self.assertRaisesRegex(ValueError, 'Target aggregate'):
            m.registered_rule(self.repo, self.manifest)

    def test_placeholder_policy_is_not_executable(self):
        self.rule['target_preparation_sha256'] = '0' * 64
        self.write_policy()
        with self.assertRaisesRegex(ValueError, 'not been finalized'):
            m.registered_rule(self.repo, self.manifest)

    def test_modified_donor_receipt_refused(self):
        (self.work / 'google-services-source.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'changed after'):
            m.verify_source(self.repo, self.work, self.manifest_path)

    def test_source_admission_does_not_prove_target_preparation(self):
        m.verify_source(self.repo, self.work, self.manifest_path)
        with self.assertRaisesRegex(ValueError, 'registered target'):
            m.verify_target(self.repo, self.work)

    def test_changed_policy_mid_transition_refused(self):
        m.verify_source(self.repo, self.work, self.manifest_path)
        self.rule['id'] = 'different transition'
        self.write_policy()
        with self.assertRaisesRegex(ValueError, 'changed during'):
            m.verify_target(self.repo, self.work)

    def test_prepared_domain_scan_reads_only_affected_targets(self):
        src = self.work / 'src'
        src.mkdir()
        (src / 'changed.cc').write_text('"google.qjz9zk"')
        (src / 'unrelated.cc').write_text('"google.qjz9zk"')
        with patch.object(preparation, 'pinned_inverse_rules', return_value=[(re.compile('google.qjz9zk'), 'google.com')]):
            result = preparation.restore_domains(src, None, self.root / 'rules', self.root / 'never-read-list',
                                                  ['changed.cc'], pinned=True, include_domain_list=False)
        self.assertEqual((src / 'changed.cc').read_text(), '"google.com"')
        self.assertEqual((src / 'unrelated.cc').read_text(), '"google.qjz9zk"')
        self.assertEqual(result['strategy'], 'verified-prepared-targets-only')


class RegisteredCheckoutTest(unittest.TestCase):
    def test_current_checkout_matches_the_explicit_transition(self):
        root = Path(__file__).resolve().parents[2]
        rule = json.loads((root / m.POLICY).read_text(encoding='utf-8'))['migrations'][0]
        donor = {**c.IDENTITY, 'source_commit': rule['source_commit'],
                 'recipe_sha256': rule['source_recipe_sha256']}
        self.assertEqual(m.registered_rule(root, donor)['id'], rule['id'])


if __name__ == '__main__':
    unittest.main()
