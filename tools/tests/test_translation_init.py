"""Pinned overlay roundtrip and failure mutations; C++ execution is a CI gate."""
import gzip
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from tools import verify_translation_init_source as gate

ROOT = Path(__file__).resolve().parents[2]


class TranslationInitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='shardx-translation-init-')
        self.addCleanup(self.temp.cleanup)
        self.src = Path(self.temp.name)
        self.fixture = json.loads(gzip.decompress(
            (ROOT / 'tools/tests/fixtures/translation-init-156.json.gz').read_bytes()))
        for name, text in self.fixture.items():
            path = self.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8', newline='\n')
        self.patch(False)

    def patch(self, reverse):
        program = shutil.which('patch') or 'C:/Program Files/Git/usr/bin/patch.exe'
        result = subprocess.run([program, '-p1', '--batch', '--fuzz=0', '--binary', '-E',
            '--no-backup-if-mismatch', '--reverse' if reverse else '--forward',
            '-i', str(ROOT / 'patches/native-browser/0004-translation-init-wait.patch')],
            cwd=self.src, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNone(re.search(r'fuzz|offset|FAILED', result.stdout, re.I))

    def test_exact_native_overlay_roundtrip(self):
        self.assertEqual(gate.verify(self.src), list(gate.TARGETS))
        self.patch(True)
        for name, text in self.fixture.items():
            self.assertEqual((self.src / name).read_text(encoding='utf-8'), text)
        self.assertFalse((self.src / gate.TARGETS[2]).exists())

    def test_deadline_delay_error_and_navigation_cancellation_required(self):
        mutations = [
            (0, 'init_wait.NextDelay(base::TimeTicks::Now())', 'std::nullopt'),
            (0, 'weak_method_factory_.GetWeakPtr(), init_wait)', 'base::Unretained(this), init_wait)'),
            (0, 'weak_method_factory_.InvalidateWeakPtrs();', '// no cancellation'),
            (0, 'if (error != TranslateErrors::NONE)', 'if (false)'),
            (2, 'deadline_(now + base::Seconds(15))', 'deadline_(now + base::Hours(1))'),
            (2, 'const base::TimeTicks deadline_;', 'base::Time deadline_;'),
            (2, 'remaining});', 'base::Seconds(1)});'),
        ]
        for index, before, after in mutations:
            path = self.src / gate.TARGETS[index]
            original = path.read_text(encoding='utf-8')
            self.assertIn(before, original)
            path.write_text(original.replace(before, after), encoding='utf-8')
            with self.subTest(before=before), self.assertRaises(ValueError):
                gate.verify(self.src)
            path.write_text(original, encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
