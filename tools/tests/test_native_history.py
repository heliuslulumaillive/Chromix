import copy
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tools import native_history_probe as runtime
from tools import verify_history_retention_source as source

ROOT = Path(__file__).resolve().parents[2]


def good_history():
    rows = []
    for name, days, ages in runtime.POLICIES:
        now = 1793650000000
        args = [] if days is None else ['--shardx-managed-environment=01234567-89ab-4cde-8123-456789abcdef',
                                       '--shardx-history-retention-days=' + str(days)]
        rows.append({'name': name, 'days': days, 'completed': True,
            'reference_unix_ms': now, 'actual_arguments': args,
            'seeded_visit_counts': {str(age): 1 for age in runtime.AGES},
            'expiration_wait_seconds': 36, 'found_ages': ages[:], 'database_check': 'ok',
            'found': [{'url': 'http://127.0.0.1:1234/page/ShardXHistoryProbe' + str(age),
                       'time': now - age * 86400000} for age in ages],
            'after_manual_delete': [], 'private_history': []})
    return rows


class HistoryRuntimeEvidenceTests(unittest.TestCase):
    def test_native_search_expiration_manual_delete_and_private_results_required(self):
        good = good_history()
        self.assertTrue(all(row['passed'] for row in runtime.assess(good)))
        for field, value in [('found_ages', [10]), ('found', []), ('actual_arguments', []),
                ('expiration_wait_seconds', 0), ('seeded_visit_counts', {}),
                ('after_manual_delete', [{}]), ('private_history', [{}]), ('database_check', 'corrupt')]:
            bad = copy.deepcopy(good); bad[0][field] = value
            with self.subTest(field=field):
                self.assertFalse(all(row['passed'] for row in runtime.assess(bad)))
        self.assertFalse(all(row['passed'] for row in runtime.assess(good[:1])))


class HistorySourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='shardx-history-source-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = json.loads(gzip.decompress((ROOT / 'tools/tests/fixtures/history-retention-source-156.json.gz').read_bytes()))
        for name, data in self.fixture.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data, encoding='utf-8', newline='\n')
        self.patch(False)

    def patch(self, reverse):
        program = shutil.which('patch') or 'C:/Program Files/Git/usr/bin/patch.exe'
        result = subprocess.run([program, '-p1', '--batch', '--fuzz=0', '--binary',
            '--no-backup-if-mismatch', '--reverse' if reverse else '--forward',
            '-i', str(ROOT / 'patches/native-browser/0003-history-retention.patch')],
            cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exact_overlay_roundtrip_and_native_maintenance(self):
        self.assertEqual(len(source.verify(self.root)), 5)
        self.patch(True)
        for name, data in self.fixture.items():
            self.assertEqual((self.root / name).read_text(encoding='utf-8'), data)

    def test_age_gate_and_maintenance_cannot_be_silently_removed(self):
        name = source.TARGETS[1]
        path = self.root / name
        original = path.read_text(encoding='utf-8')
        for before, after in [('expire_visits_ && ExpireSomeOldHistory(', 'ExpireSomeOldHistory('),
            ('ExpireOldSegmentData(base::Time::Now() - expiration_threshold_);', '// removed'),
            ('  expire_visits_ = expire_visits;', '  expire_visits_ = true;')]:
            path.write_text(original.replace(before, after), encoding='utf-8')
            with self.subTest(before=before), self.assertRaises(ValueError):
                source.verify(self.root)


if __name__ == '__main__':
    unittest.main()
