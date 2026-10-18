"""Small regression checks for the source conversion, not a browser audit."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('google_restore', Path(__file__).parents[1] / 'prepare_google_services.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DomainRestorationTest(unittest.TestCase):
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
