"""Exact-source overlay and fail-closed contract mutation tests, no live cookies."""
import gzip
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from tools import verify_native_cookie_source as gate

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name('fixtures') / 'native-cookie-source-156.json.gz'


class NativeCookieSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-cookie-source-')
        self.addCleanup(self.temp.cleanup)
        self.src = Path(self.temp.name)
        self.fixture = json.loads(gzip.decompress(FIXTURE.read_bytes()))
        for name, text in self.fixture['effective'].items():
            path = self.src / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8', newline='\n')

    def mutate(self, name, old, new):
        path = self.src / name; text = path.read_text(encoding='utf-8')
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding='utf-8', newline='\n')
        with self.assertRaises(ValueError): gate.verify(self.src, ROOT)

    def test_all_native_sites_and_one_shot_contract(self):
        self.assertEqual(len(gate.verify(self.src, ROOT)), 15)

    def test_eager_import_cannot_be_replaced_with_navigation_only_start(self):
        self.mutate(gate.TARGETS[3], 'bool ServiceIsCreatedWithBrowserContext() const override { return true; }',
                    'bool ServiceIsCreatedWithBrowserContext() const override { return false; }')

    def test_cookie_backend_rejection_cannot_be_ignored(self):
        self.mutate(gate.TARGETS[3], 'if (!result.status.IsInclude())', 'if (false)')

    def test_receipt_must_precede_successful_navigation(self):
        self.mutate(gate.TARGETS[3], 'Finish(success && written)', 'Finish(success)')

    def test_native_readback_is_required_after_quota_eviction(self):
        self.mutate(gate.TARGETS[3], 'if (!VerifyReadback(*batch_, cookies, base::Time::Now(), &counts_))', 'if (false)')

    def test_explicit_cookie_path_may_not_be_truncated(self):
        self.mutate(gate.TARGETS[2], 'path_string ? *path_string : ""', 'path_string ? "/" : ""')

    def test_https_may_not_silently_elevate_secure_attribute(self):
        self.mutate(gate.TARGETS[2], 'const bool secure = item.FindBool("secure").value_or(false)',
                    'const bool secure = true')

    def test_recreated_profile_checks_completed_ledger_before_missing_envelope(self):
        self.mutate(gate.TARGETS[2], 'IsCompletedReceipt(*old, binding, nullptr, true)',
                    'IsCompletedReceipt(*old, binding, nullptr, false)')

    def test_off_the_record_is_not_an_import_target(self):
        self.mutate(gate.TARGETS[3], '!profile->IsOffTheRecord()', 'true')

    def test_uncertain_or_rolled_back_ledger_is_not_a_completion(self):
        self.mutate(gate.TARGETS[2], 'receipt.FindBool("writeOutcomeUnknown") != false', 'false')

    def test_late_publication_may_not_revive_a_failed_attempt(self):
        self.mutate(gate.TARGETS[3], 'if (success && state_ == State::kFailed)', 'if (false)')

    def test_deadline_is_shared_with_the_staged_request(self):
        self.mutate(gate.TARGETS[3], 'std::min(remaining, base::Seconds(60))', 'base::Seconds(60)')

    def test_unspecified_same_site_is_not_replaced_with_lax(self):
        self.mutate(gate.TARGETS[2], 'net::CookieSameSite same_site = net::CookieSameSite::UNSPECIFIED',
                    'net::CookieSameSite same_site = net::CookieSameSite::LAX_MODE')

    def test_exact_overlay_reverse_and_forward_without_fuzz(self):
        patch = shutil.which('patch') or 'C:/Program Files/Git/usr/bin/patch.exe'
        if not Path(patch).is_file() and not shutil.which(patch): self.skipTest('GNU patch required')
        for reverse in (True, False):
            result = subprocess.run([patch, '-p1', '--batch', '--fuzz=0', '--binary',
                '--no-backup-if-mismatch', '--reverse' if reverse else '--forward',
                '-i', str(ROOT / gate.OVERLAY)], cwd=self.src, capture_output=True,
                text=True, encoding='utf-8', errors='replace', timeout=30)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIsNone(re.search(r'fuzz|offset|FAILED', output, re.I), output)
        for name, expected in self.fixture['effective'].items():
            self.assertEqual((self.src / name).read_text(encoding='utf-8'), expected)


if __name__ == '__main__': unittest.main()
