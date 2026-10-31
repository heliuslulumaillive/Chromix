"""Fail-closed source/resource checks, not compiled Safe Browsing acceptance."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

with mock.patch.object(sys, 'path', [str(Path(__file__).parents[1]), *sys.path]):
    import restored_source_resources as resource
    import verify_google_services_source as source
    import prepare_google_services as preparation
    import build_google_services_patch as generator


class RestoredResourceTest(unittest.TestCase):
    def fixture(self, root):
        repo, src = root / 'repo', root / 'src'
        repo.mkdir()
        src.mkdir()
        payload = repo / 'policy.pb'
        data = bytes.fromhex('0800220a101220012a0410001801')
        payload.write_bytes(data)
        config = {'restore_pruned_resources': {'resources/policy.pb': {
            'repository_path': 'policy.pb', 'sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data),
        }}}
        return repo, src, config, data

    def test_restores_binary_and_preserves_unchanged_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, src, config, data = self.fixture(Path(directory))
            with self.assertRaises(ValueError):
                resource.verify(src, repo, config)
            self.assertEqual(resource.restore(src, repo, config), ['resources/policy.pb'])
            target = src / 'resources/policy.pb'
            self.assertEqual(target.read_bytes(), data)
            timestamp = target.stat().st_mtime_ns
            resource.restore(src, repo, config)
            self.assertEqual(target.stat().st_mtime_ns, timestamp)
            resource.verify(src, repo, config)

    def test_tampered_source_is_rejected_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, src, config, _ = self.fixture(Path(directory))
            resource.restore(src, repo, config)
            target = src / 'resources/policy.pb'
            target.write_bytes(b'unexpected')
            with self.assertRaisesRegex(ValueError, 'unexpected bytes'):
                resource.restore(src, repo, config)
            self.assertEqual(target.read_bytes(), b'unexpected')

    def test_payload_identity_and_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, src, config, _ = self.fixture(Path(directory))
            (repo / 'policy.pb').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                resource.restore(src, repo, config)
            spec = config['restore_pruned_resources']['resources/policy.pb']
            for name in ['', '../outside.pb', '/absolute.pb', r'C:\outside.pb']:
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'escapes'):
                    resource.restore(src, repo, {'restore_pruned_resources': {name: spec}})


class SecuritySourceGateTest(unittest.TestCase):
    def fixture(self, src):
        requirements = {name: list(values) for name, values in source.REQUIRED.items()}
        for name, values in source.SECURITY_REQUIRED.items():
            requirements.setdefault(name, []).extend(values)
        for name, fragments in requirements.items():
            path = src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('\n'.join('"' + value + '";' if value.startswith('https://') else value
                                      for value in fragments), encoding='utf-8')
        leak = src / 'components/password_manager/core/browser/leak_detection/leak_detection_check_impl.cc'
        leak.write_text(leak.read_text() + '\nbool LeakDetectionCheck::CanStartLeakCheck() {\n'
                        'const bool enabled = prefs.GetBoolean(prefs::kPasswordLeakDetectionEnabled);\n'
                        'return enabled && !LeakDetectionCheck::IsURLBlockedByPolicy(prefs);\n}\n')
        download = src / 'components/download/internal/common/download_item_impl.cc'
        download.write_text(download.read_text() + '\nvoid DownloadItemImpl::SetDangerType(int danger_type) {\n'
                            'danger_type_ = danger_type;\n}\n')
        updater = src / 'components/update_client/update_checker.cc'
        updater.write_text(updater.read_text() + '\nvoid UpdateCheckerImpl::CheckForUpdates() {\n'
                           'update_check_callback_ = std::move(update_check_callback);\n}\n')
        return leak, download

    def test_real_source_paths_and_defaults_required_not_only_ui(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)
            self.fixture(src)
            source.verify(src, require_security=True)
            for name in [
                'chrome/browser/safe_browsing/services_delegate_desktop.cc',
                'chrome/browser/browser_process_impl.cc',
                'components/safe_browsing/core/common/BUILD.gn',
                'components/safe_browsing/core/common/safe_browsing_prefs.cc',
            ]:
                path = src / name
                original = path.read_text()
                path.write_text('\n'.join('// ' + line for line in original.splitlines()))
                with self.subTest(name=name), self.assertRaises(ValueError):
                    source.verify(src, require_security=True)
                path.write_text(original)

    def test_early_leak_return_rejected_even_with_retained_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)
            leak, _ = self.fixture(src)
            original = leak.read_text()
            for value in ['false', 'true']:
                leak.write_text(original.replace('CanStartLeakCheck() {',
                                                'CanStartLeakCheck() { return ' + value + ';'))
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'policy gate'):
                    source.verify(src, require_security=True)

    def test_disabled_build_target_rejected_even_when_sources_are_listed(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)
            self.fixture(src)
            path = src / 'chrome/browser/safe_browsing/BUILD.gn'
            path.write_text('if (false) {\n' + path.read_text() + '\n}\n')
            with self.assertRaisesRegex(ValueError, 'build target is disabled'):
                source.verify(src, require_security=True)

    def test_download_result_cannot_be_flattened_to_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)
            _, download = self.fixture(src)
            download.write_text(download.read_text().replace(
                'danger_type_ = danger_type;\n}',
                'danger_type_ = DOWNLOAD_DANGER_TYPE_NOT_DANGEROUS;\n}'))
            with self.assertRaisesRegex(ValueError, 'danger classification'):
                source.verify(src, require_security=True)

    def test_security_component_updates_cannot_silently_return(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)
            self.fixture(src)
            path = src / 'components/update_client/update_checker.cc'
            path.write_text(path.read_text().replace('CheckForUpdates() {',
                                                    'CheckForUpdates() { return;'))
            with self.assertRaisesRegex(ValueError, 'update requests are disabled'):
                source.verify(src, require_security=True)

    def test_repository_policy_binds_resource_and_standard_default(self):
        repo = Path(__file__).resolve().parents[2]
        config = json.loads((repo / 'build/google-services-win155.json').read_text())
        self.assertEqual(config['security_restoration']['safe_browsing_mode'], 1)
        self.assertIn('safe_browsing_mode = 1', (repo / 'build/args.security.gn').read_text())
        payloads = resource.resources(repo, config)
        self.assertEqual(set(payloads), {'components/safe_browsing/content/resources/empty_file_type_policy.pb'})
        self.assertTrue(set(payloads).issubset(config['restore_pruned_sources']))
        self.assertIn('ungoogled-chromium/windows/windows-disable-download-warning-prompt.patch',
                      config['restore_windows_sections'])


class SecurityPreparationIdentityTest(unittest.TestCase):
    def test_exact_build_variant_and_capability_version_must_agree(self):
        for version, variant in [('155.0.8059.39', 'fingerprint-modes-win155'),
                                 ('156.0.8078.79', 'fingerprint-modes-win156')]:
            self.assertEqual(preparation.validate_fingerprint_identity(
                {'chromium_version': version},
                {'chromium_version': version, 'build_variant': variant}), variant)
        with self.assertRaisesRegex(ValueError, 'capability identity'):
            preparation.validate_fingerprint_identity(
                {'chromium_version': '156.0.8078.79'},
                {'chromium_version': '156.0.8078.79', 'build_variant': 'fingerprint-modes-win155'})
        with self.assertRaisesRegex(ValueError, 'Unsupported exact'):
            preparation.validate_fingerprint_identity({'chromium_version': '156.0.8078.7'}, {})

    def test_external_source_revisions_are_fixed_and_deps_is_not_executed(self):
        webrtc, v8 = 'a' * 40, 'b' * 40
        deps = ("'webrtc_git': 'https://webrtc.googlesource.com',\n"
                "'chromium_git': 'https://chromium.googlesource.com',\n"
                "'v8_revision': '" + v8 + "',\n"
                "'src/third_party/webrtc': Var('webrtc_git') + '/src.git' + '@' + '" + webrtc + "',\n"
                "'src/v8': Var('chromium_git') + '/v8/v8.git' + '@' + Var('v8_revision'),\n"
                "raise Exception('DEPS must not be executed')").encode()
        self.assertEqual(generator.webrtc_revision_from_deps(deps), webrtc)
        self.assertEqual(generator.v8_revision_from_deps(deps), v8)
        with self.assertRaises(ValueError):
            generator.v8_revision_from_deps(deps.replace(v8.encode(), b'main'))
        with self.assertRaises(ValueError):
            generator.webrtc_revision_from_deps(deps.replace(b'https://webrtc.googlesource.com', b'https://example.invalid'))

    def test_other_base_is_rejected_before_any_preparation_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, work = root / 'repo', root / 'work'
            (repo / 'build').mkdir(parents=True)
            (repo / 'build/google-services-win155.json').write_text(json.dumps({'chromium_version': '156.0.8078.79'}))
            (work / 'src/chrome').mkdir(parents=True)
            (work / 'src/chrome/VERSION').write_text('MAJOR=155\nMINOR=0\nBUILD=8059\nPATCH=39\n')
            args = ['prepare', '--repo', str(repo), '--work', str(work), '--patch-bin', 'unused']
            with mock.patch.object(sys, 'argv', args), self.assertRaisesRegex(ValueError, 'limited to Chromium'):
                preparation.main()
            self.assertFalse((work / '.google-services-preparing').exists())

    def test_old_aggregate_cannot_be_mislabeled_as_new_base(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, work = root / 'repo', root / 'work'
            (repo / 'build').mkdir(parents=True)
            (repo / 'build/google-services-win155.json').write_text(json.dumps({'chromium_version': '156.0.8078.79'}))
            (work / 'src/chrome').mkdir(parents=True)
            (work / 'src/chrome/VERSION').write_text('MAJOR=156\nMINOR=0\nBUILD=8078\nPATCH=79\n')
            patch = repo / 'patches/google-services/0001-restore-google-services.patch'
            patch.parent.mkdir(parents=True)
            patch.write_bytes(b'old aggregate\n')
            patch.with_suffix('.json').write_text(json.dumps({
                'version': '155.0.8059.39',
                'patch_sha256': hashlib.sha256(patch.read_bytes()).hexdigest(),
            }))
            args = ['prepare', '--repo', str(repo), '--work', str(work), '--patch-bin', 'unused']
            with mock.patch.object(sys, 'argv', args), self.assertRaisesRegex(ValueError, 'aggregate does not match'):
                preparation.main()
            self.assertFalse((work / '.google-services-preparing').exists())


if __name__ == '__main__':
    unittest.main()
