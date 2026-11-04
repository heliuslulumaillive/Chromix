"""Planning must never claim an unported version was built or mutate pins."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import plan_kernel_upgrade as upgrade


class UpgradePlanTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.pin = self.repo / 'CHROMIUM_VERSION'
        self.pin.write_text('153.0.8010.36\n')
        self.before = (self.pin.read_bytes(), self.pin.stat().st_mtime_ns)
        self.pins = {'ChromiumVersion': '156.0.8078.79', 'UngoogledCommit': 'a' * 40,
                     'UngoogledWindowsCommit': 'b' * 40}
        self.pinning = patch.object(upgrade, 'load_pins', return_value=self.pins)
        self.pinning.start()
        self.addCleanup(self.pinning.stop)

    def tearDown(self):
        self.assertEqual((self.pin.read_bytes(), self.pin.stat().st_mtime_ns), self.before)
        self.assertEqual(list(self.repo.iterdir()), [self.pin])

    def test_new_version_refused_before_selecting_or_editing_any_recipe(self):
        with patch.object(upgrade, 'select') as selection:
            result = upgrade.plan(self.repo, '157.0.8100.1')
        selection.assert_not_called()
        self.assertEqual(result['status'], 'port-required')
        self.assertFalse(result['compiled'])

    def test_current_version_selects_the_product_profile_and_reports_only_recipe(self):
        with patch.object(upgrade, 'select', return_value=({'series_sha256': 'c' * 64}, [('theme', b'diff')])) as selection:
            result = upgrade.plan(self.repo, '156.0.8078.79')
        selection.assert_called_once_with(self.repo, 'windows', src=None, core=None, profile='lite')
        self.assertEqual(result['status'], 'recipe-verified')
        self.assertTrue(result['read_only'])
        self.assertFalse(result['runtime_verified'])
        self.assertEqual(result['patch_count'], 1)

    def test_current_version_with_stale_manifest_is_not_accepted(self):
        with patch.object(upgrade, 'select', side_effect=ValueError('stale manifest')):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(upgrade.main(['--repo', str(self.repo), '--version', '156.0.8078.79']), 1)

    def test_cli_has_nonzero_exit_for_unported_version(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = upgrade.main(['--repo', str(self.repo), '--version', '157.0.8100.1'])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stream.getvalue())['status'], 'port-required')

    def test_unpinned_latest_and_unsupported_product_platform_fail(self):
        for version in ('latest', '156', '156.0.8078.79\n', '../pins'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                upgrade.plan(self.repo, version)
        with self.assertRaisesRegex(ValueError, 'Windows x64'):
            upgrade.plan(self.repo, '156.0.8078.79', 'linux')

    def test_legacy_wrapper_cannot_write_version_or_start_a_build(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / 'build/rebase-monthly.sh').read_text()
        self.assertIn('plan_kernel_upgrade.py', script)
        self.assertNotIn('build/build.sh', script)
        self.assertNotIn('> "$REPO/CHROMIUM_VERSION"', script)
        self.assertNotIn('Rebased to', script)


if __name__ == '__main__':
    unittest.main()
