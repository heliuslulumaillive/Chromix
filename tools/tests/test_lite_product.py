"""Product selection and admission reject drift without binding historical inputs."""
import copy
import hashlib
import json
import re
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tools import lite_patch_stack as stack, patch_selection, security_build_record

ROOT = Path(__file__).resolve().parents[2]


class ProductTests(unittest.TestCase):
    def test_product_and_legacy_are_explicit_separate_profiles(self):
        self.assertEqual(len(patch_selection.select(ROOT, 'windows', profile='lite')[1]), 26)
        with self.assertRaisesRegex(ValueError, 'unsupported Chromium patch version'):
            patch_selection.select(ROOT, 'windows')
        for platform in ('linux', 'macos', None):
            with self.assertRaises(ValueError):
                patch_selection.select(ROOT, platform, profile='lite')

    def test_active_inventory_excludes_historical_patches(self):
        inputs = stack.input_hashes(ROOT)
        self.assertIn('build/native-browser-capabilities.json', inputs)
        self.assertIn('build/native-cookie-capabilities.json', inputs)
        self.assertIn('patches/lite-win156/retirement-manifest.json', inputs)
        self.assertFalse(any('/history/' in name or '/chromium154' in name for name in inputs))
        self.assertNotIn('patches/series', inputs)
        self.assertNotIn('patches/google-services/0001-restore-google-services.patch', inputs)

    def test_source_preserves_retired_absence(self):
        from tools.lite_retirement import REMOVED_FILES
        manifest = stack.load(ROOT)
        final = {row['path']: row['after_sha256'] for row in manifest['source_files']}
        for name in REMOVED_FILES:
            self.assertIsNone(final.get(name))
        self.assertEqual(manifest['legacy_equivalence']['result'], 'ported-to-reviewed157-source')
        self.assertEqual(manifest['legacy_equivalence']['baseline_version'], '156.0.8078.79')
        self.assertIs(manifest['legacy_equivalence']['cross_version_byte_equivalence_claimed'], False)
        self.assertEqual(manifest['runtime']['chromium_version'], '157.0.8103.38')
        self.assertEqual(manifest['upstream_port']['chromium_commit'], 'a733b5afbb3ba618844ff8876b89ad1a774b2f18')
        self.assertEqual([Path(row['path']).name for row in manifest['patches'][-2:]],
                         ['25-signin-failure-recovery.patch', '26-history-sync-default.patch'])

    def test_manifest_and_patch_tampering_rejected(self):
        with tempfile.TemporaryDirectory(prefix='lite-product-test-') as tmp:
            repo = Path(tmp)
            for name in stack.input_hashes(ROOT):
                target = repo / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, target)
            product = stack.load(repo)
            patch = repo / product['patches'][0]['path']
            raw = patch.read_bytes()
            patch.write_bytes(raw + b'\n')
            with self.assertRaisesRegex(ValueError, 'Changed product patch'):
                stack.load(repo)
            patch.write_bytes(raw)
            path = repo / stack.MANIFEST
            changed = copy.deepcopy(product)
            changed['runtime']['core_commit'] = '0' * 40
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, 'manifest'):
                stack.load(repo)

    def test_windows_recipe_ignores_unselected_history_but_binds_active_bytes(self):
        before = security_build_record.recipe(ROOT)
        with mock.patch.object(security_build_record, 'sha256', side_effect=AssertionError('legacy inventory was read')):
            self.assertEqual(security_build_record.recipe(ROOT), before)
        with mock.patch.object(stack, 'input_hashes', return_value={'selected.patch': '0' * 64}):
            self.assertNotEqual(security_build_record.recipe(ROOT), before)

    def test_final_source_drift_and_missing_files_rejected(self):
        raw = b'accepted source\n'
        manifest = {'source_files': [{'path': 'a.cc', 'after_sha256': hashlib.sha256(raw).hexdigest()}]}
        with tempfile.TemporaryDirectory(prefix='lite-source-test-') as tmp, mock.patch.object(stack, 'load', return_value=manifest):
            src = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'a.cc'):
                stack.verify_source(ROOT, src)
            (src / 'a.cc').write_bytes(raw)
            self.assertEqual(stack.verify_source(ROOT, src), ['a.cc'])
            (src / 'a.cc').write_bytes(raw + b'changed\n')
            with self.assertRaisesRegex(ValueError, 'a.cc'):
                stack.verify_source(ROOT, src)

    def test_unknown_version_cannot_reuse_product_stack(self):
        with mock.patch.object(stack, 'load_pins', return_value={'ChromiumVersion': '157.0.0.1',
                'UngoogledWindowsVersion': '157.0.8103.38-1.1', 'UngoogledCommit': '0' * 40,
                'UngoogledWindowsCommit': '0' * 40}):
            with self.assertRaises(ValueError):
                stack.load(ROOT)

    def test_final_source_never_normalizes_executable_domain_tampering(self):
        accepted = b'// google.com\nconst char* url = "https://google.com";\n'
        manifest = {'source_files': [{'path': 'a.cc', 'after_sha256': hashlib.sha256(accepted).hexdigest()}],
                    'domain_full_files': []}
        rules = [(re.compile(r'google\.qjz9zk'), 'google.com')]
        with tempfile.TemporaryDirectory(prefix='lite-domains-test-') as tmp, mock.patch.object(stack, 'load', return_value=manifest):
            src = Path(tmp)
            path = src / 'a.cc'
            path.write_bytes(accepted.replace(b'google.com', b'google.qjz9zk'))
            stack.verify_source(ROOT, src, inverse_rules=rules, phase='before-domains')
            with self.assertRaisesRegex(ValueError, 'a.cc'):
                stack.verify_source(ROOT, src, inverse_rules=rules)
            path.write_bytes(accepted.replace(b'// google.com', b'// google.qjz9zk'))
            stack.verify_source(ROOT, src, inverse_rules=rules)
            manifest['domain_full_files'] = ['a.cc']
            with self.assertRaisesRegex(ValueError, 'a.cc'):
                stack.verify_source(ROOT, src, inverse_rules=rules)

    def test_deleted_source_cannot_be_replaced_by_a_directory(self):
        manifest = {'source_files': [{'path': 'removed.cc', 'after_sha256': None}]}
        with tempfile.TemporaryDirectory(prefix='lite-absent-test-') as tmp, mock.patch.object(stack, 'load', return_value=manifest):
            src = Path(tmp)
            (src / 'removed.cc').mkdir()
            with self.assertRaisesRegex(ValueError, 'regular file'):
                stack.verify_source(ROOT, src)

    def test_final_patch_verifier_keeps_version_and_transaction_checks(self):
        from tools import verify_patch_stack
        product = stack.load(ROOT)
        with tempfile.TemporaryDirectory(prefix='lite-final-test-') as tmp, mock.patch.object(stack, 'load', return_value=product):
            root = Path(tmp)
            src, core = root / 'src', root / 'core'
            (src / 'chrome').mkdir(parents=True)
            core.mkdir()
            (core / 'chromium_version.txt').write_text('157.0.8103.38\n')
            (src / 'chrome/VERSION').write_text('MAJOR=157\nMINOR=0\nBUILD=8078\nPATCH=79\n')
            (root / 'google-services-source.json').write_text(json.dumps({'patch_kind': stack.KIND,
                'patch_sha256': product['stack_sha256']}))
            with self.assertRaisesRegex(ValueError, 'source version'):
                verify_patch_stack.verify(src, ROOT, core=core, platform='windows', profile='lite')
            (root / '.google-services-preparing').write_text('unfinished')
            with self.assertRaisesRegex(ValueError, 'in progress'):
                verify_patch_stack.verify(src, ROOT, core=core, platform='windows', profile='lite')

    def test_warm_product_receipt_cannot_claim_another_variant(self):
        from tools import prepare_lite_product, prepare_google_services
        product = stack.load(ROOT)
        with tempfile.TemporaryDirectory(prefix='lite-warm-test-') as tmp, \
                mock.patch.object(stack, 'input_hashes', return_value={}), \
                mock.patch.object(prepare_google_services, 'pinned_inverse_rules', return_value=[]):
            work = Path(tmp)
            (work / 'src/chrome').mkdir(parents=True)
            (work / 'src/chrome/VERSION').write_text('MAJOR=157\nMINOR=0\nBUILD=8103\nPATCH=38\n')
            saved = {'patch_kind': stack.KIND, 'patch_sha256': product['stack_sha256'],
                     'chromium_version': '157.0.8103.38', 'variant': 'wrong',
                     'build_variant': 'fingerprint-modes-win157', 'preparation_inputs': {},
                     'preparation_sha256': hashlib.sha256(b'{}').hexdigest()}
            (work / 'google-services-source.json').write_text(json.dumps(saved))
            with self.assertRaisesRegex(ValueError, 'registered product migration'):
                prepare_lite_product.prepare(ROOT, work)
            self.assertFalse((work / '.google-services-preparing').exists())


if __name__ == '__main__':
    unittest.main()
