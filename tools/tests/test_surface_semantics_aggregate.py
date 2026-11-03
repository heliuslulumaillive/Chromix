"""Safety and composition boundaries for the isolated semantic aggregate builder."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import rebuild_surface_semantics_aggregate as rebuild


class SurfaceSemanticsAggregateTest(unittest.TestCase):
    def test_auto_eol_is_limited_to_the_two_declared_text_inputs(self):
        row = rebuild.verify_selector_bytes('CHROMIUM_WINDOWS_VERSION', b'155\n', b'155\r\n')
        self.assertNotEqual(row['base_blob_sha256'], row['working_sha256'])
        with self.assertRaises(ValueError):
            rebuild.verify_selector_bytes('CHROMIUM_WINDOWS_VERSION', b'155\n', b'156\r\n')
        with self.assertRaises(ValueError):
            rebuild.verify_selector_bytes('tools/patch_selection.py', b'155\n', b'155\r\n')

    def test_only_new_explicit_ordered_overlays_are_admitted(self):
        old = ['patches/fingerprint-modes/0010-windows-native-voice-compat.patch']
        good = ['patches/fingerprint-modes/0011-pixels.patch',
                'patches/fingerprint-modes/0014-headless.patch']
        self.assertEqual(rebuild.validate_overlay_names(good, old), good)
        for bad in [[], old, good[::-1], good + good[:1],
                    ['patches/fingerprint-modes/../0011-pixels.patch'],
                    ['patches/fingerprint-modes/0011-pixels.patch',
                     'patches/fingerprint-modes/0011-other.patch']]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                rebuild.validate_overlay_names(bad, old)

    def test_existing_candidate_requires_same_base_and_no_unrelated_config_edits(self):
        base = {'chromium_version': '155.0.8059.39', 'restoration_overlays': ['old']}
        self.assertIsNone(rebuild.ensure_config_prefix(base, copy.deepcopy(base), {}))
        addition = ['patches/fingerprint-modes/0011-pixels.patch']
        marker = {'schema_version': 1, 'base_commit': rebuild.BASE_COMMIT,
                  'base_aggregate_sha256': rebuild.BASE_AGGREGATE_SHA256, 'overlays': addition}
        manifest = {rebuild.INTEGRATION_KEY: marker}
        current = {**base, 'restoration_overlays': ['old', *addition]}
        self.assertEqual(rebuild.ensure_config_prefix(base, current, manifest), marker)
        with self.assertRaises(ValueError):
            rebuild.ensure_config_prefix(base, {**current, 'chromium_version': '156'}, manifest)
        with self.assertRaises(ValueError):
            rebuild.ensure_config_prefix(base, current,
                {rebuild.INTEGRATION_KEY: {**marker, 'base_commit': 'unknown'}})

    def test_unrelated_sections_remain_exact_and_new_source_is_created(self):
        original = {'a.cc': 'opaque unchanged section\n', 'b.cc': 'previous b section\n'}
        result = rebuild.compose(original, {'b.cc': b'old\n', 'new.h': None},
                                 {'b.cc': b'new\n', 'new.h': b'header\n'}, {'b.cc', 'new.h'})
        self.assertEqual(result['a.cc'], original['a.cc'])
        self.assertIn('--- a/b.cc\n+++ b/b.cc\n', result['b.cc'])
        self.assertIn('--- /dev/null\n+++ b/new.h\n', result['new.h'])
        with self.assertRaises(ValueError):
            rebuild.compose(original, {'b.cc': b'old'}, {'b.cc': None}, {'b.cc'})

    def test_unrelated_manifest_or_source_receipt_changes_are_refused(self):
        base = {'strategy': 'compatible-v1', 'ordered_overlays': [{'path': 'voice', 'sha256': 'old'}],
                'combined_patch_sha256': 'base', 'combined_patch_changed_files': 1}
        new = {**base, 'combined_patch_sha256': 'new',
               'ordered_overlays': [*base['ordered_overlays'], {'path': 'new'}]}
        rebuild.ensure_manifest_base(base, new)
        with self.assertRaises(ValueError):
            rebuild.ensure_manifest_base(base, {**new, 'strategy': 'different'})
        with self.assertRaises(ValueError):
            rebuild.ensure_manifest_base(base, {**new, 'ordered_overlays': [{'path': 'voice', 'sha256': 'changed'}]})
        receipt = {'version': '155.0.8059.39', 'patch_sha256': rebuild.sha(b'base'),
                   'changed_files': ['a'], 'source_inputs': [{'path': 'old', 'sha256': 'old-hash'}]}
        current = {**receipt, 'patch_sha256': rebuild.sha(b'new'), 'changed_files': ['a', 'b'],
                   'source_inputs': [*receipt['source_inputs'], {'path': 'new', 'sha256': 'new-hash'}]}
        rebuild.ensure_receipt_base(receipt, current, b'new', ['a', 'b'], {'new'})
        with self.assertRaises(ValueError):
            rebuild.ensure_receipt_base(receipt, current, b'new', ['a', 'b'], set())
        with self.assertRaises(ValueError):
            rebuild.ensure_receipt_base(receipt, {**current, 'version': '156'}, b'new', ['a', 'b'], {'new'})

    def test_duplicate_sections_and_escape_paths_are_refused(self):
        patch = b'--- a/a.cc\n+++ b/a.cc\n@@ -1 +1 @@\n-old\n+new\n'
        self.assertEqual(list(rebuild.parse_sections(patch)), ['a.cc'])
        with self.assertRaises(ValueError):
            rebuild.parse_sections(patch + patch)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            for name in ['../x', '/x', 'C:/x', 'a\\b', 'a//b']:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    rebuild.owned(root, name)

    def test_history_collision_blocks_publication_before_current_files_change(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            current = root / 'build/config.json'
            current.parent.mkdir(parents=True)
            current.write_bytes(b'old config')
            history_data = b'old aggregate'
            digest = rebuild.sha(history_data)
            history = root / f'patches/google-services/history/{digest}.patch'
            history.parent.mkdir(parents=True)
            history.write_bytes(b'wrong history')
            (root / 'patches/fingerprint-modes').mkdir()
            with self.assertRaises(ValueError):
                rebuild.publish(root, {'build/config.json': b'new config'}, {digest: history_data})
            self.assertEqual(current.read_bytes(), b'old config')

    def test_failed_metadata_transaction_restores_earlier_writes_and_retains_marker(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            (root / 'build').mkdir()
            (root / 'patches/fingerprint-modes').mkdir(parents=True)
            for name in ['a', 'b']:
                (root / 'build' / name).write_bytes(b'old ' + name.encode())
            actual = rebuild.atomic_write
            calls = 0
            def fail_second(path, data):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError('synthetic failed second write')
                actual(path, data)
            with mock.patch.object(rebuild, 'atomic_write', side_effect=fail_second), self.assertRaises(OSError):
                rebuild.publish(root, {'build/a': b'new a', 'build/b': b'new b'}, {})
            self.assertEqual((root / 'build/a').read_bytes(), b'old a')
            self.assertEqual((root / 'build/b').read_bytes(), b'old b')
            self.assertTrue((root / 'patches/fingerprint-modes/.surface-semantics-rebuild-in-progress.json').is_file())


if __name__ == '__main__':
    unittest.main()
