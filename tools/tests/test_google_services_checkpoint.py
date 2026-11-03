import copy
import tempfile
import threading
from pathlib import Path
import unittest

from tools import google_services_checkpoint as checkpoint


def artifacts():
    return [
        {'name': 'google-win155-checkpoint-s1', 'id': 1, 'expired': False},
        {'name': 'google-win155-checkpoint-s2', 'id': 2, 'expired': False},
        {'name': 'google-win155-tree-s2-part1', 'id': 3, 'expired': False},
        {'name': 'google-win155-tree-s2-part2', 'id': 4, 'expired': False},
    ]


def manifest():
    return {'format_version': 1, 'chromium_version': checkpoint.VERSION,
            'run_id': '42', 'stage': 2, 'commit': 'a' * 40,
            'parts': [{'name': 'tree.7z.001', 'bytes': 2},
                      {'name': 'tree.7z.002', 'bytes': 2}]}


class CheckpointSelectionTest(unittest.TestCase):
    def test_latest_committed_stage_but_never_current_or_future_stage(self):
        self.assertEqual(checkpoint.select_manifest(artifacts(), '42', '99', 1)[0], 2)
        self.assertEqual(checkpoint.select_manifest(artifacts(), '42', '42', 2)[0], 1)
        with self.assertRaises(ValueError):
            checkpoint.select_manifest(artifacts(), '42', '42', 1)
        with self.assertRaises(ValueError):
            checkpoint.select_manifest(artifacts()[2:], '42', '99', 1)

    def test_binds_run_commit_version_and_all_volumes_before_setup(self):
        source = {'id': 42, 'head_sha': 'a' * 40}
        parts, hashes = checkpoint.validate_manifest(manifest(), source, artifacts(), 2)
        self.assertEqual([p['artifact_id'] for p in parts], [3, 4])
        self.assertEqual(hashes, {})  # Existing committed format-1 checkpoints.
        for field, value in [('run_id', '43'), ('commit', 'b' * 40),
                             ('chromium_version', '154.0.0.0'), ('stage', 1)]:
            changed = {**manifest(), field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                checkpoint.validate_manifest(changed, source, artifacts(), 2)
        for changed_artifacts in (artifacts()[:-1], artifacts() + [artifacts()[-1]],
                                  [*artifacts()[:-1], {**artifacts()[-1], 'expired': True}]):
            with self.assertRaises(ValueError):
                checkpoint.validate_manifest(manifest(), source, changed_artifacts, 2)

    def test_rejects_paths_bad_sizes_and_malformed_identity_hashes(self):
        source = {'id': 42, 'head_sha': 'a' * 40}
        for field, value in [('name', '../escape'), ('bytes', True), ('bytes', 0),
                             ('bytes', 8 * 1024**3 + 1)]:
            changed = copy.deepcopy(manifest())
            changed['parts'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                checkpoint.validate_manifest(changed, source, artifacts(), 2)
        changed = {**manifest(), 'gn_args_sha256': 'bad'}
        with self.assertRaises(ValueError):
            checkpoint.validate_manifest(changed, source, artifacts(), 2)


class CheckpointTransferTest(unittest.TestCase):
    def test_independent_volumes_download_in_parallel_and_are_verified(self):
        barrier = threading.Barrier(2)
        parts, _ = checkpoint.validate_manifest(manifest(), {'id': 42, 'head_sha': 'a' * 40}, artifacts(), 2)
        selection = {'repository': 'owner/repo', 'source_run': '42', 'parts': parts}

        def download(arguments, **kwargs):
            barrier.wait(timeout=5)
            directory = Path(arguments[arguments.index('--dir') + 1])
            name = arguments[arguments.index('--name') + 1]
            part = next(p for p in parts if p['artifact_name'] == name)
            (directory / part['name']).write_bytes(b'ok')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = checkpoint.download_parts(selection, root, download)
            self.assertEqual(report['volumes'], 2)
            self.assertEqual(report['bytes'], 4)
            self.assertEqual((root / 'tree.7z.001').read_bytes(), b'ok')
            self.assertEqual((root / 'tree.7z.002').read_bytes(), b'ok')

    def test_wrong_volume_never_becomes_the_extraction_input(self):
        parts, _ = checkpoint.validate_manifest(manifest(), {'id': 42, 'head_sha': 'a' * 40}, artifacts(), 2)
        selection = {'repository': 'owner/repo', 'source_run': '42', 'parts': parts[:1]}

        def download(arguments, **kwargs):
            directory = Path(arguments[arguments.index('--dir') + 1])
            (directory / 'tree.7z.001').write_bytes(b'wrong-size')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                checkpoint.download_parts(selection, root, download)
            self.assertFalse((root / 'tree.7z.001').exists())


class ContinuationTest(unittest.TestCase):
    def test_continues_only_budget_or_delivery_with_a_valid_recovery_source(self):
        cases = [
            ('success', False, 'skipped', True, 'success', True, '99'),
            ('success', False, 'skipped', True, 'failure', False, ''),
            ('success', False, 'skipped', False, 'skipped', True, '42'),
            ('success', True, 'failure', True, 'success', True, '99'),
            ('success', True, 'failure', False, 'skipped', True, '42'),
            ('success', True, 'success', True, 'skipped', False, ''),
            ('failure', False, 'skipped', True, 'success', False, ''),
            ('cancelled', False, 'skipped', True, 'success', False, ''),
            ('skipped', False, 'skipped', False, 'skipped', False, ''),
        ]
        for outcome, ready, uploaded, changed, saved, retry, source in cases:
            with self.subTest(case=(outcome, ready, uploaded, changed, saved)):
                plan = checkpoint.continuation_plan(outcome, ready, uploaded, changed,
                                                    saved, '42', '99')
                self.assertEqual(plan['resume_needed'], retry)
                self.assertEqual(plan['resume_source'], source)
                self.assertEqual(plan['recovery_source'], '99' if saved == 'success' else '42')


if __name__ == '__main__':
    unittest.main()
