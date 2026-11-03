"""Small regression checks for the source conversion, not a browser audit."""
import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import re
import sys
from unittest import mock

spec = importlib.util.spec_from_file_location('google_restore', Path(__file__).parents[1] / 'prepare_google_services.py')
module = importlib.util.module_from_spec(spec)
with mock.patch.object(sys, 'path', [str(Path(__file__).parents[1]), *sys.path]):
    spec.loader.exec_module(module)
    import build_google_services_patch as generator
    import verify_google_services_source as source_checks


class DomainRestorationTest(unittest.TestCase):
    def test_source_gate_rejects_a_checkpoint_without_the_voice_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)
            for name, fragments in source_checks.REQUIRED.items():
                path = src / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('\n'.join('"' + fragment + '";' if fragment.startswith('https://') else fragment
                                          for fragment in fragments), encoding='utf-8')
            source_checks.verify(src)
            for name in ('base/BUILD.gn', 'base/uxr_surface_modes_test_main.cc',
                         'content/browser/speech/tts_win.cc'):
                path = src / name
                original = path.read_text(encoding='utf-8')
                for fragment in source_checks.REQUIRED[name]:
                    path.write_text(original.replace(fragment, '// ' + fragment), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, name.replace('.', r'\.')):
                        source_checks.verify(src)
                path.write_text(original, encoding='utf-8')

    def test_google_appid_restoration_survives_domain_list_regeneration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / 'src'
            name = module.WEBAUTHN_VALIDATION_FILES[0]
            checker = src / name
            checker.parent.mkdir(parents=True)
            checker.write_text('if (caller_origin.DomainIs("9oo91e.qjz9zk")) {}\n')
            rules = root / 'rules'
            rules.write_text('google\\.com#9oo91e.qjz9zk\n')
            listing = root / 'list'
            listing.write_text('# checker deliberately excluded\n')
            with mock.patch.object(module, 'pinned_inverse_rules', return_value=[(re.compile('9oo91e.qjz9zk'), 'google.com')]):
                self.assertEqual(module.restore_domains(src, None, rules, listing, pinned=True)['files_changed'], 1)
                self.assertIn('DomainIs("google.com")', checker.read_text())
                unchanged = checker.stat().st_mtime_ns
                self.assertEqual(module.restore_domains(src, None, rules, listing, pinned=True)['files_changed'], 0)
                self.assertEqual(checker.stat().st_mtime_ns, unchanged)

    def test_source_gate_rejects_substituted_appid_and_removed_origin_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)
            for name, fragments in source_checks.REQUIRED.items():
                path = src / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('\n'.join('"' + fragment + '";' if fragment.startswith('https://') else fragment
                                          for fragment in fragments), encoding='utf-8')
            source_checks.verify(src)
            checker = src / module.WEBAUTHN_VALIDATION_FILES[0]
            original = checker.read_text()
            checker.write_text(original + '\n// old comment: 9oo91e.qjz9zk\n')
            source_checks.verify(src)
            for changed in (original.replace('google.com', '9oo91e.qjz9zk'),
                            original.replace('!appid_url.has_ref()', 'true'),
                            original.replace('caller_origin.DomainIs("google.com")', '// caller_origin.DomainIs("google.com")')):
                checker.write_text(changed)
                with self.assertRaisesRegex(ValueError, 'webauth_request_security_checker_impl'):
                    source_checks.verify(src)
            checker.write_text(original)
            gn = src / 'chrome/test/BUILD.gn'
            original_gn = gn.read_text()
            gn.write_text('\n'.join('# ' + line for line in original_gn.splitlines()))
            with self.assertRaisesRegex(ValueError, 'chrome/test/BUILD.gn'):
                source_checks.verify(src)
            gn.write_text(original_gn)
            header = src / module.WEBAUTHN_VALIDATION_FILES[1]
            header.write_text(header.read_text().replace('www.gstatic.com', 'www.95tat1c.qjz9zk'))
            with self.assertRaisesRegex(ValueError, 'webauth_request_security_checker_impl'):
                source_checks.verify(src)

    def test_known_domain_variants_restore_without_original_archive(self):
        values = {
            'accounts.9oo91e.qjz9zk': 'accounts.google.com',
            'cryptauthvault.9oo91eapis.qjz9zk': 'cryptauthvault.googleapis.com',
            'www.95tat1c.qjz9zk/securitykey/a/9oo91e.qjz9zk/origins.json': 'www.gstatic.com/securitykey/a/google.com/origins.json',
            'f0ntz.9oo91e8p1.qjz9zk': 'fonts.googleapis.com',
            r'9oo91e\.qjz9zk': r'google\.com',
            '9oo91e21p.qjz9zk': 'googlezip.net',
            'b3ac0n21.9vt2.qjz9zk': 'beacons1.gvt2.com',
            'goo.gle.qjz9zk': 'goo.gle',
        }
        for before, expected in values.items():
            value=before
            for pattern,replacement in reversed(module.DOMAIN_INVERSES):
                value=re.sub(pattern,replacement,value)
            self.assertEqual(value,expected)

    def test_cpp_comments_are_not_rewritten(self):
        source = '// "google.com" is mentioned in documentation\nconst auto url = "https://google.com/";\n'
        result = module.CPP_TOKENS.sub(lambda token: token[0] if token.group('comment') else token[0].replace('google.com', 'example.com'), source)
        self.assertIn('// "google.com"', result)
        self.assertIn('"https://example.com/"', result)

    def test_preserves_later_fingerprint_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / 'src'
            src.mkdir()
            original = b'const char* rp = "google.com";\r\nconst char* appid = "https://www.gstatic.com/securitykey/origins.json";\r\n'
            current = original.replace(b'google.com', b'9oo91e.qjz9zk').replace(b'gstatic.com', b'95tat1c.qjz9zk') + b'// later fingerprint implementation\r\n'
            (src / 'checker.cc').write_bytes(current)
            rules = root / 'rules'
            rules.write_text('google\\.com#9oo91e.qjz9zk\ngstatic\\.com#95tat1c.qjz9zk\n')
            listing = root / 'list'
            listing.write_text('checker.cc\n')
            cache = root / 'cache.tar.gz'
            with tarfile.open(cache, 'w:gz') as archive:
                entry = tarfile.TarInfo('orig/checker.cc')
                entry.size = len(original)
                archive.addfile(entry, io.BytesIO(original))
            report = module.restore_domains(src, cache, rules, listing)
            self.assertEqual(report['files_changed'], 1)
            self.assertEqual((src / 'checker.cc').read_bytes(), original + b'// later fingerprint implementation\r\n')
            self.assertEqual(module.restore_domains(src, cache, rules, listing)['files_changed'], 0)

    def test_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                module.inside(Path(directory), '../outside')


class PrunedSourceRestorationTest(unittest.TestCase):
    def test_replay_baseline_prunes_sources_but_restoration_keeps_them(self):
        names = ['signin_pref_names.cc', 'signin_pref_names.h']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for variant in ('baseline', 'restored'):
                tree = root / variant
                tree.mkdir()
                for name in names + ['cached.obj']:
                    (tree / name).write_bytes(b'unchanged input')
                generator.prune_selected_sources(tree, names, keep=names if variant == 'restored' else ())
                self.assertEqual([(tree / name).exists() for name in names], [variant == 'restored'] * 2)
                self.assertEqual((tree / 'cached.obj').read_bytes(), b'unchanged input')

    def test_mixed_cleanup_does_not_restore_an_unavailable_header(self):
        baseline = '#include "existing.h"\n'
        restored = baseline + '#include "signin.h"\n#include "safe_browsing.h"\n'
        result, removed = generator.filter_pruned_includes(baseline, restored, {'safe_browsing.h'})
        self.assertEqual(result, baseline + '#include "signin.h"\n')
        self.assertEqual(removed, ['safe_browsing.h'])

    def test_shipped_patch_recreates_both_missing_preference_files(self):
        repo = Path(__file__).resolve().parents[2]
        patch = repo / 'patches/google-services/0001-restore-google-services.patch'
        report = json.loads(patch.with_suffix('.json').read_text())
        required = {'components/signin/public/base/signin_pref_names.cc',
                    'components/signin/public/base/signin_pref_names.h'}
        creations = {name: section for name, new, section in generator.sections(patch.read_text()) if new}
        self.assertTrue(required.issubset(creations), 'The real checkpoint has neither file; modifications alone cannot restore them')
        self.assertTrue(required.issubset(report['baseline_pruned_sources']))
        hashes = {item['path']: item['sha256'] for item in report['source_inputs']}
        # Match the worker; Strawberry Perl's older patch on the hosted-runner
        # PATH aborts when applying /dev/null file-creation hunks.
        candidate = Path(r'C:\Program Files\Git\usr\bin\patch.exe')
        executable = str(candidate) if candidate.is_file() else shutil.which('patch')
        if not executable:
            self.skipTest('GNU patch is unavailable')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subset = root / 'create.patch'
            subset.write_text(''.join(creations[name] for name in sorted(required)), encoding='utf-8', newline='\n')
            result = subprocess.run([executable, '-p1', '--batch', '--forward', '--fuzz=0', '-i', str(subset)], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name in required:
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), hashes[name])


class BuildReceiptTest(unittest.TestCase):
    def test_receipt_uses_selected_output_and_never_exports_credentials(self):
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work, bundle = root / 'work', root / 'bundle'
            work.mkdir()
            bundle.mkdir()
            source = {'variant': 'google-services-self-use', 'chromium_version': '155.0.8059.39',
                      'patch_sha256': 'a' * 64, 'preparation_sha256': 'b' * 64}
            (work / 'google-services-source.json').write_text(json.dumps(source))
            (bundle / 'chrome.exe').write_bytes(b'synthetic executable')
            default = work / 'src/out/Default/args.gn'
            default.parent.mkdir(parents=True)
            default.write_text('wrong configuration')
            selected = work / 'src/out/Chromix/args.gn'
            selected.parent.mkdir()
            selected.write_text('google_default_client_secret="synthetic-secret-not-for-report"')
            environment = {**os.environ, 'GITHUB_SHA': 'c' * 40, 'GITHUB_RUN_ID': '123',
                           'GITHUB_REPOSITORY': 'fixture/chromix', 'GITHUB_RUN_ATTEMPT': '1',
                           'GOOGLE_API_KEY': 'synthetic-api-key-not-for-report'}
            subprocess.run([sys.executable, '-B', str(repo / 'tools/write_google_services_build_record.py'),
                            '--bundle', str(bundle), '--work', str(work), '--gn-args', str(selected)],
                           env=environment, check=True, capture_output=True, text=True, timeout=15)
            raw = (bundle / 'google-services-build.json').read_text()
            result = json.loads(raw)
            self.assertEqual(result['gn_args_sha256'], hashlib.sha256(selected.read_bytes()).hexdigest())
            self.assertEqual(result['files']['chrome.exe'], hashlib.sha256(b'synthetic executable').hexdigest())
            self.assertNotIn('synthetic-secret-not-for-report', raw)
            self.assertNotIn('synthetic-api-key-not-for-report', raw)


if __name__ == '__main__':
    unittest.main()
