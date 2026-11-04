"""Small authenticated fixtures exercise transfer reuse without network or Chromium."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import security_checkpoint as c


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.parts = [b'first volume', b'second volume']
        self.manifest = {**c.IDENTITY, 'repository': 'owner/repo', 'run_id': '123',
                         'run_attempt': '1', 'source_commit': 'a' * 40, 'stage': 1,
                         'recipe_sha256': 'b' * 64, 'resume_safe': True,
                         'gn_args_sha256': 'c' * 64, 'source_receipt_sha256': 'd' * 64,
                         'parts': [{'name': f'tree.7z.{i:03d}', 'bytes': len(v),
                                    'sha256': hashlib.sha256(v).hexdigest()}
                                   for i, v in enumerate(self.parts, 1)]}
        self.run = {'id': 123, 'head_sha': 'a' * 40, 'run_attempt': 1}
        names = ['security-win156-checkpoint-s1', 'security-win156-s1-part1',
                 'security-win156-s1-part2']
        self.artifacts = [{'id': i + 1, 'name': n, 'size_in_bytes': 100, 'expired': False,
                           'digest': 'sha256:' + str(i) * 64} for i, n in enumerate(names)]
        self.downloaded = []
        self.fail_part = None
        self.tamper_download = None
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_RUN_ID': '456'}).start()
        patch.object(c, 'recipe', return_value='b' * 64).start()
        patch.object(c, 'gh', side_effect=self.gh).start()

    def gh(self, args):
        if args[0] == 'api':
            return json.dumps({'artifacts': self.artifacts} if '/artifacts?' in args[1] else self.run)
        name = args[args.index('--name') + 1]
        destination = Path(args[args.index('--dir') + 1])
        self.downloaded.append(name)
        if name.endswith('checkpoint-s1'):
            (destination / 'checkpoint.json').write_text(json.dumps(self.manifest))
        else:
            index = int(name[-1])
            if self.fail_part == index:
                raise RuntimeError('Synthetic network failure')
            content = self.parts[index - 1]
            if self.tamper_download == index:
                content = b'X' * len(content)
            (destination / f'tree.7z.{index:03d}').write_bytes(content)
        return ''

    def plan(self):
        return c.preflight(self.root, '123', 1, 1)

    def test_preflight_downloads_only_small_manifest(self):
        plan = self.plan()
        self.assertEqual(plan['stage'], 1)
        self.assertEqual(len(plan['volume_artifacts']), 2)
        self.assertEqual(self.downloaded, ['security-win156-checkpoint-s1'])

    def test_missing_volume_fails_before_large_download(self):
        self.artifacts.pop()
        with self.assertRaisesRegex(ValueError, 'Missing or ambiguous'):
            self.plan()
        self.assertFalse(any('part' in name for name in self.downloaded))

    def test_changed_recipe_cannot_use_plain_resume(self):
        self.manifest['recipe_sha256'] = 'e' * 64
        with self.assertRaisesRegex(ValueError, 'explicitly reviewed'):
            self.plan()
        self.assertFalse(any('part' in name for name in self.downloaded))

    def test_same_recipe_upgrade_is_rejected_before_volume_transfer(self):
        with self.assertRaisesRegex(ValueError, 'use resume'):
            c.preflight(self.root, '123', 1, 1, True)
        self.assertFalse(any('part' in name for name in self.downloaded))

    def test_replaced_artifact_rejected_before_large_download(self):
        plan = self.plan()
        self.artifacts[1]['id'] = 99
        with self.assertRaisesRegex(ValueError, 'replaced/removed'):
            c.validate_preflight(self.root, plan, '123', 1, 1, False)
        self.assertFalse(any('part' in name for name in self.downloaded))

    def test_local_preflight_cannot_rewrite_authenticated_manifest(self):
        plan = self.plan()
        plan['manifest']['parts'][0]['sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'authenticated donor'):
            c.validate_preflight(self.root, plan, '123', 1, 1, False)

    def test_run_attempt_and_same_run_future_stage_rejected(self):
        self.manifest['run_attempt'] = '2'
        with self.assertRaisesRegex(ValueError, 'different run attempt'):
            self.plan()
        self.manifest['run_attempt'] = '1'
        with patch.dict(os.environ, {'GITHUB_RUN_ID': '123'}):
            with self.assertRaisesRegex(ValueError, 'No complete preceding'):
                self.plan()

    def test_verified_completed_volume_reused_after_transfer_failure(self):
        plan = self.plan()
        plan_file = self.root / 'plan.json'
        plan_file.write_text(json.dumps(plan))
        dest = self.root / 'transfer'
        self.fail_part = 2
        with self.assertRaisesRegex(RuntimeError, 'Synthetic'):
            c.restore(self.root, dest, '123', 1, 1, False, plan_file)
        self.assertEqual((dest / 'tree.7z.001').read_bytes(), self.parts[0])
        self.fail_part = None
        c.restore(self.root, dest, '123', 1, 1, False, plan_file)
        self.assertEqual(self.downloaded.count('security-win156-s1-part1'), 1)
        self.assertEqual((dest / 'tree.7z.002').read_bytes(), self.parts[1])
        self.assertTrue((dest / 'checkpoint.json').is_file())

    def test_completed_volume_tamper_does_not_get_silently_redownloaded(self):
        dest = self.root / 'transfer'
        c.restore(self.root, dest, '123', 1, 1)
        (dest / 'tree.7z.001').write_bytes(b'X' * len(self.parts[0]))
        before = self.downloaded.count('security-win156-s1-part1')
        with self.assertRaisesRegex(ValueError, 'content mismatch'):
            c.restore(self.root, dest, '123', 1, 1)
        self.assertEqual(self.downloaded.count('security-win156-s1-part1'), before)

    def test_transient_download_retry_uses_a_fresh_directory_and_reuses_other_volume(self):
        directories = []
        original = self.gh

        def download(args):
            if args[0] == 'run' and args[args.index('--name') + 1].endswith('part2'):
                directory = Path(args[args.index('--dir') + 1])
                directories.append(directory)
                if len(directories) == 1:
                    (directory / 'partial').write_bytes(b'incomplete transfer')
                    raise c.GitHubOperationError(retryable=True)
            return original(args)

        with patch.object(c, 'gh', side_effect=download), patch.object(c.time, 'sleep'):
            destination = self.root / 'retry-transfer'
            c.restore(self.root, destination, '123', 1, 1)
        self.assertEqual(len(directories), 2)
        self.assertNotEqual(directories[0], directories[1])
        self.assertTrue(all(not p.exists() for p in directories))
        self.assertEqual(self.downloaded.count('security-win156-s1-part1'), 1)
        self.assertEqual((destination / 'tree.7z.002').read_bytes(), self.parts[1])

    def test_transport_retry_is_bounded_and_permission_failures_are_not_retried(self):
        for retryable, expected in ((True, 3), (False, 1)):
            calls = []
            original = self.gh

            def download(args):
                if args[0] == 'run' and args[args.index('--name') + 1].endswith('part2'):
                    calls.append(args)
                    raise c.GitHubOperationError(retryable=retryable)
                return original(args)

            with self.subTest(retryable=retryable), patch.object(c, 'gh', side_effect=download), patch.object(c.time, 'sleep'):
                with self.assertRaises(c.GitHubOperationError):
                    c.restore(self.root, self.root / ('transfer-' + str(retryable)), '123', 1, 1)
                self.assertEqual(len(calls), expected)

    def test_network_classification_does_not_retry_auth_or_integrity_failures(self):
        for message in ('HTTP 503 Service Unavailable', 'TLS handshake timeout', 'connection reset by peer'):
            self.assertTrue(c.transient_transfer_error(message))
        for message in ('HTTP 401 Bad credentials', 'HTTP 403 Forbidden', 'HTTP 404 Not Found', 'SHA256 mismatch'):
            self.assertFalse(c.transient_transfer_error(message))

    def test_wrong_payload_and_unowned_destination_rejected(self):
        self.tamper_download = 1
        with self.assertRaisesRegex(ValueError, 'content mismatch'):
            c.restore(self.root, self.root / 'bad-transfer', '123', 1, 1)
        dest = self.root / 'unowned'
        dest.mkdir()
        (dest / 'keep').write_text('unrelated')
        with self.assertRaisesRegex(ValueError, 'ownership'):
            c.restore(self.root, dest, '123', 1, 1)
        self.assertEqual((dest / 'keep').read_text(), 'unrelated')

    def test_archive_size_uses_directory_headers_without_decompression(self):
        summary = c.inspect_archive_listing([
            'Path = archive.7z', 'Size = 999999', '----------',
            'Path = chromix/src/one.cc', 'Size = 120',
            'Path = chromix/src/two.cc', 'Size = 80'])
        self.assertEqual(summary['entries'], 2)
        self.assertEqual(summary['uncompressed_bytes'], 200)
        for size in ['-1', 'not-a-size', str(1024**4 + 1)]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                c.inspect_archive_listing(['----------', 'Path = chromix/source', 'Size = ' + size])


if __name__ == '__main__':
    unittest.main()
