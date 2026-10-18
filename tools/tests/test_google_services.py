"""Small regression checks for the source conversion, not a browser audit."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import re

spec = importlib.util.spec_from_file_location('google_restore', Path(__file__).parents[1] / 'prepare_google_services.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


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


if __name__ == '__main__':
    unittest.main()
