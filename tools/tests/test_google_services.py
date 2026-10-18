"""Small regression checks for the source conversion, not a browser audit."""
import importlib.util
import io
import hashlib
import json
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


class DomainRestorationTest(unittest.TestCase):
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
        executable = shutil.which('patch')
        if not executable:
            candidate = Path(r'C:\Program Files\Git\usr\bin\patch.exe')
            executable = str(candidate) if candidate.is_file() else None
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


if __name__ == '__main__':
    unittest.main()
