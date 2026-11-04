"""Aggregate derivation gates and exact-byte source reuse, with no network calls."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from google_services_inputs import (GENERATOR_INPUTS, PinnedSourceCache, aggregate_inputs,
                                    identity, native_browser_input_names, sha256, verify_aggregate_inputs,
                                    write_if_changed)
from build_google_services_patch import search_engines_revision_from_deps
import prepare_google_services as preparation


class AggregateInputsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='aggregate-inputs-')
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.config = {'chromium_version': '156.0.8078.79', 'core_commit': 'a' * 40,
                       'windows_commit': 'b' * 40,
                       'restoration_overlays': ['patches/native-browser/one.patch'],
                       'restore_pruned_resources': {'data.bin': {'repository_path': 'build/data.bin'}}}
        names = [*GENERATOR_INPUTS, 'patches/base.patch', 'patches/native-browser/one.patch', 'build/data.bin']
        for name in names:
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(('fixture ' + name).encode())
        self.config_path = self.repo / 'build/google-services-win155.json'
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        self.selection = {'series_sha256': 'c' * 64, 'patches': [
            {'path': 'patches/base.patch', 'sha256': sha256((self.repo / 'patches/base.patch').read_bytes())}]}
        self.selector = patch('patch_selection.select_legacy', return_value=(self.selection, []))
        self.selector.start()
        self.addCleanup(self.selector.stop)
        self.snapshot = aggregate_inputs(self.repo)
        self.report = {'generation_inputs': self.snapshot,
                       'generation_inputs_sha256': identity(self.snapshot)}

    def test_overlay_config_generator_selector_and_payload_changes_require_regeneration(self):
        verify_aggregate_inputs(self.repo, self.report)
        for name in ('patches/native-browser/one.patch', 'patches/base.patch',
                     'tools/build_google_services_patch.py', 'tools/google_services_inputs.py',
                     'tools/platform_pins.py', 'build/data.bin'):
            target = self.repo / name
            original = target.read_bytes()
            with self.subTest(name=name):
                target.write_bytes(original + b'\nchanged')
                with self.assertRaisesRegex(ValueError, 'aggregate is stale'):
                    verify_aggregate_inputs(self.repo, self.report)
            target.write_bytes(original)
        config = {**self.config, 'extra_sources': ['new/native.cc']}
        self.config_path.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'aggregate is stale'):
            verify_aggregate_inputs(self.repo, self.report)

    def test_unbound_or_self_rehashed_stale_receipts_are_rejected(self):
        for changed in ({}, {**self.report, 'generation_inputs_sha256': '0' * 64}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                verify_aggregate_inputs(self.repo, changed)
        changed = copy.deepcopy(self.report)
        changed['generation_inputs']['ordered_overlays'] = []
        changed['generation_inputs_sha256'] = identity(changed['generation_inputs'])
        with self.assertRaises(ValueError):
            verify_aggregate_inputs(self.repo, changed)

    def test_overlay_order_and_inventory_are_part_of_the_contract(self):
        second = self.repo / 'patches/native-browser/two.patch'
        second.write_bytes(b'new overlay')
        self.config['restoration_overlays'].append('patches/native-browser/two.patch')
        self.config_path.write_text(json.dumps(self.config))
        snapshot = aggregate_inputs(self.repo)
        report = {'generation_inputs': snapshot, 'generation_inputs_sha256': identity(snapshot)}
        self.config['restoration_overlays'].reverse()
        self.config_path.write_text(json.dumps(self.config))
        with self.assertRaises(ValueError):
            verify_aggregate_inputs(self.repo, report)

    def test_prepare_rejects_stale_aggregate_before_accepting_existing_marker(self):
        work = self.repo / 'work'
        version = work / 'src/chrome/VERSION'
        version.parent.mkdir(parents=True)
        version.write_text('MAJOR=156\nMINOR=0\nBUILD=8078\nPATCH=79\n')
        marker = work / 'google-services-source.json'
        marker.write_bytes(b'original marker must survive')
        aggregate = self.repo / 'patches/google-services/0001-restore-google-services.patch'
        aggregate.parent.mkdir(parents=True)
        aggregate.write_bytes(b'old aggregate')
        aggregate.with_suffix('.json').write_text(json.dumps({
            'version': self.config['chromium_version'], 'patch_sha256': sha256(aggregate.read_bytes())}))
        args = ['prepare', '--repo', str(self.repo), '--work', str(work), '--patch-bin', 'must-not-run']
        with patch.object(sys, 'argv', args), self.assertRaisesRegex(ValueError, 'aggregate is stale'):
            preparation.main()
        self.assertEqual(marker.read_bytes(), b'original marker must survive')
        self.assertFalse((work / '.google-services-preparing').exists())

    def test_native_contract_inputs_are_explicit_and_cannot_redirect(self):
        native = {'version': 1, 'capabilities': 'build/native-browser-capabilities.json',
                  'source_gate_required': True}
        config = {**self.config, 'native_browser': native}
        self.assertEqual(native_browser_input_names(config),
                         ('build/native-browser-capabilities.json', 'tools/verify_native_browser_source.py',
                          'tools/verify_native_environment_controls_source.py',
                 'tools/verify_history_retention_source.py', 'tools/build_history_retention_patch.py',
                          'tools/verify_translation_init_source.py', 'tools/build_translation_init_patch.py',
                          'tools/build_lite_retirement_patch.py', 'tools/build_session_lifecycle_patch.py'))
        for key, value in [('capabilities', '../outside.json'), ('version', True),
                           ('source_gate_required', False), ('source_gate_required', 1)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                native_browser_input_names({**config, 'native_browser': {**native, key: value}})

    def test_prepared_source_checks_execute_the_native_gate_without_inheriting_acceptance(self):
        config = {**self.config, 'native_browser': {'version': 1,
                  'capabilities': 'build/native-browser-capabilities.json', 'source_gate_required': True}}
        with patch.object(preparation, 'verify_signin_source', return_value=['google.cc']), \
                patch('verify_native_browser_source.verify', return_value=['native.cc']) as native:
            self.assertEqual(preparation.verify_prepared_sources(self.repo / 'src', self.repo, config),
                             ['google.cc', 'native.cc'])
            native.assert_called_once_with(self.repo / 'src', self.repo)
        with patch.object(preparation, 'verify_signin_source', return_value=[]), \
                patch('verify_native_browser_source.verify', side_effect=ValueError('missing native implementation')):
            with self.assertRaisesRegex(ValueError, 'missing native implementation'):
                preparation.verify_prepared_sources(self.repo / 'src', self.repo, config)


class SourceCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pinned-source-cache-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cached = self.root / 'cached'
        self.output = self.root / 'output'
        self.cached.mkdir()
        self.data = b'known pinned source\n'
        self.report = {'version': '156.0.8078.79', 'source_inputs': [
            {'path': 'a/source.cc', 'sha256': sha256(self.data)}]}
        self.receipt = self.root / 'receipt.json'
        (self.cached / 'a').mkdir()
        (self.cached / 'a/source.cc').write_bytes(self.data)

    def cache(self):
        self.receipt.write_text(json.dumps(self.report), encoding='utf-8')
        return PinnedSourceCache('156.0.8078.79', [(self.cached, self.receipt)])

    def test_known_cache_and_existing_output_are_verified_without_network_or_timestamp_changes(self):
        cache = self.cache()
        download = Mock(side_effect=AssertionError('must not download'))
        source = self.cached / 'a/source.cc'
        source_before = source.stat().st_mtime_ns
        self.assertEqual(cache.materialize(self.output, 'a/source.cc', download), self.data)
        output = self.output / 'a/source.cc'
        os.utime(output, ns=(1600000000000000000, 1600000000000000000))
        output_before = output.stat().st_mtime_ns
        self.assertEqual(cache.materialize(self.output, 'a/source.cc', download), self.data)
        self.assertEqual(output.stat().st_mtime_ns, output_before)
        self.assertEqual(source.stat().st_mtime_ns, source_before)
        download.assert_not_called()

    def test_only_unlisted_or_missing_sources_are_downloaded(self):
        cache = self.cache()
        download = Mock(return_value=b'new pinned input')
        self.assertEqual(cache.materialize(self.output, 'new.cc', download), b'new pinned input')
        download.assert_called_once()
        self.assertFalse((self.cached / 'new.cc').exists())
        (self.cached / 'a/source.cc').unlink()
        download = Mock(return_value=self.data)
        self.assertEqual(cache.materialize(self.output, 'a/source.cc', download), self.data)
        download.assert_called_once()

    def test_unlisted_existing_pristine_is_not_trusted(self):
        self.output.mkdir()
        (self.output / 'new.cc').write_bytes(b'unverified stale bytes')
        download = Mock(return_value=b'new pinned input')
        self.assertEqual(self.cache().materialize(self.output, 'new.cc', download), b'new pinned input')
        download.assert_called_once()

    def test_output_cannot_mutate_the_readonly_cache_tree(self):
        cache = self.cache()
        download = Mock(side_effect=AssertionError('must not download'))
        with self.assertRaisesRegex(ValueError, 'read-only'):
            cache.materialize(self.cached, 'new.cc', download)
        self.assertFalse((self.cached / 'new.cc').exists())
        download.assert_not_called()

    def test_corrupt_cache_or_pristine_fails_without_silent_overwrite(self):
        cache = self.cache()
        download = Mock(side_effect=AssertionError('must not download'))
        (self.cached / 'a/source.cc').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'Cached source differs'):
            cache.materialize(self.output, 'a/source.cc', download)
        self.assertFalse((self.output / 'a/source.cc').exists())
        (self.cached / 'a/source.cc').write_bytes(self.data)
        cache.materialize(self.output, 'a/source.cc', download)
        (self.output / 'a/source.cc').write_bytes(b'corrupt output')
        with self.assertRaisesRegex(ValueError, 'Existing pristine source differs'):
            cache.materialize(self.output, 'a/source.cc', download)
        self.assertEqual((self.output / 'a/source.cc').read_bytes(), b'corrupt output')

    def test_cached_and_downloaded_bytes_must_match_the_registered_hash(self):
        (self.cached / 'a/source.cc').unlink()
        with self.assertRaisesRegex(ValueError, 'Downloaded source differs'):
            self.cache().materialize(self.output, 'a/source.cc', lambda: b'wrong upstream response')
        self.assertFalse((self.output / 'a/source.cc').exists())

    def test_wrong_version_duplicate_hashes_and_traversal_fail_before_use(self):
        for name in ('../outside', '/absolute', 'a/../escape', 'C:/path', 'a\\file', 'a//file'):
            self.report['source_inputs'][0]['path'] = name
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Unsafe'):
                self.cache()
        self.report['source_inputs'][0]['path'] = 'a/source.cc'
        self.report['version'] = '155.0.8059.39'
        with self.assertRaisesRegex(ValueError, 'another Chromium version'):
            self.cache()
        self.report['version'] = '156.0.8078.79'
        self.report['source_inputs'].append(copy.deepcopy(self.report['source_inputs'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.cache()

    def test_external_source_must_match_the_revision_parsed_from_deps(self):
        row = self.report['source_inputs'][0]
        row.update(upstream_repository='https://example.test/pinned', upstream_revision='a' * 40)
        cache = self.cache()
        with self.assertRaisesRegex(ValueError, 'upstream revision'):
            cache.materialize(self.output, 'a/source.cc', Mock(),
                              external={'repository': row['upstream_repository'], 'revision': 'b' * 40})
        with self.assertRaisesRegex(ValueError, 'upstream revision'):
            cache.materialize(self.output, 'a/source.cc', Mock())
        self.assertEqual(cache.materialize(self.output, 'a/source.cc', Mock(),
                         external={'repository': row['upstream_repository'], 'revision': 'a' * 40}), self.data)

    def test_multiple_caches_support_source_manifest_files_and_reject_conflicting_hashes(self):
        self.cache()
        second = self.root / 'second.json'
        second.write_text(json.dumps({'version': self.report['version'], 'files': [
            {'path': 'b.cc', 'sha256': sha256(b'b')}] }))
        cache = PinnedSourceCache(self.report['version'], [(self.cached, self.receipt), (self.cached, second)])
        self.assertEqual(cache.materialize(self.output, 'b.cc', lambda: b'b'), b'b')
        second.write_text(json.dumps({'version': self.report['version'], 'files': [
            {'path': 'a/source.cc', 'sha256': sha256(b'wrong')}] }))
        with self.assertRaisesRegex(ValueError, 'receipts disagree'):
            PinnedSourceCache(self.report['version'], [(self.cached, self.receipt), (self.cached, second)])

    def test_deps_reuse_requires_exact_receipt_and_preserves_mtime(self):
        deps = self.root / 'DEPS'
        deps.write_bytes(b'fixed DEPS')
        download = Mock(side_effect=AssertionError('must not download'))
        with self.assertRaisesRegex(ValueError, 'exact SHA256'):
            self.cache().deps(self.output / 'DEPS', download, deps)
        self.report['external_source_pins'] = {'v8': {'chromium_deps_sha256': sha256(deps.read_bytes())}}
        cache = self.cache()
        self.assertEqual(cache.deps(self.output / 'DEPS', download, deps), b'fixed DEPS')
        before = (self.output / 'DEPS').stat().st_mtime_ns
        cache.deps(self.output / 'DEPS', download, deps)
        self.assertEqual((self.output / 'DEPS').stat().st_mtime_ns, before)
        deps.write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'Cached DEPS differs'):
            cache.deps(self.output / 'DEPS', download, deps)
        download.assert_not_called()

    def test_identical_generated_output_is_not_rewritten(self):
        target = self.root / 'output.patch'
        self.assertTrue(write_if_changed(target, b'patch'))
        before = target.stat().st_mtime_ns
        self.assertFalse(write_if_changed(target, b'patch'))
        self.assertEqual(target.stat().st_mtime_ns, before)


class SearchEngineDependencyTests(unittest.TestCase):
    def test_only_the_fixed_public_search_engine_checkout_is_accepted(self):
        source = ("'chromium_git': 'https://chromium.googlesource.com',\n"
                  "'src/third_party/search_engines_data/resources':\n"
                  "Var('chromium_git') + '/external/search_engines_data.git' + '@' + '" + 'a' * 40 + "',\n")
        self.assertEqual(search_engines_revision_from_deps(source.encode()), 'a' * 40)
        for changed in (source.replace('https://chromium.googlesource.com', 'https://wrong.test'),
                        source.replace('/external/search_engines_data.git', '/external/other.git'),
                        source.replace('a' * 40, 'main'),
                        source.replace("resources':", "resources_internal':")):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                search_engines_revision_from_deps(changed.encode())


if __name__ == '__main__':
    unittest.main()
