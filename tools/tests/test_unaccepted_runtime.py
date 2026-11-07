"""Failed verification can preserve bytes, but cannot grant runtime acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import yaml
from tools import stage_unaccepted_runtime as diagnostic

ROOT = Path(__file__).resolve().parents[2]


class UnacceptedRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='unaccepted-runtime-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle, self.evidence, self.output = [self.root / name for name in ('bundle', 'evidence', 'output')]
        self.bundle.mkdir(); self.evidence.mkdir()
        payload = {'chrome.exe': b'explicitly synthetic, never executed', 'chrome.dll': b'synthetic library',
                   'google-services-source.json': b'{"synthetic_source":true}'}
        for name, data in payload.items():
            (self.bundle / name).write_bytes(data)
        self.record = {'schema_version': 1, 'run_id': 123, 'source_commit': 'a' * 40,
                       'build_variant': 'fingerprint-modes-win157', 'chromium_version': diagnostic.VERSION,
                       'files': {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}}
        (self.bundle / 'google-services-build.json').write_text(json.dumps(self.record))
        self.state = {'schema_version': 2, 'stage': 1, 'run_id': '123', 'source_commit': 'a' * 40,
                      'status': 'failed', 'phase': 'Verify', 'finished': False,
                      'completed': diagnostic.COMPLETED[:],
                      'build_hash': diagnostic.sha256(self.bundle / 'google-services-build.json')}
        self.state_path = self.evidence / 'stage-state.json'
        self.save_state()
        source = {'schema_version': 1, 'run_id': '123', 'source_commit': 'a' * 40,
                  'build_variant': 'security-win157', 'chromium_version': diagnostic.VERSION,
                  'recipe_sha256': 'b' * 64,
                  'source_receipt_sha256': diagnostic.sha256(self.bundle / 'google-services-source.json')}
        (self.evidence / 'build-inputs.json').write_text(json.dumps(source))
        (self.evidence / '001-failed.log').write_text('Synthetic failed check\n')
        profile = self.evidence / 'temporary-profile/Default'
        profile.mkdir(parents=True)
        (profile / 'Cookies').write_bytes(b'private browser data must not enter diagnostics')
        self.patcher = patch.object(diagnostic, 'recipe', return_value='b' * 64)
        self.patcher.start(); self.addCleanup(self.patcher.stop)

    def save_state(self):
        self.state_path.write_text(json.dumps(self.state))

    def stage(self):
        return diagnostic.stage(self.root, self.bundle, self.evidence, self.state_path, self.output,
                                run_id='123', source_commit='a' * 40)

    def test_exact_immutable_runtime_and_receipts_remain_explicitly_unaccepted(self):
        state_before = self.state_path.read_bytes()
        result = self.stage()
        self.assertIs(result['accepted'], False)
        self.assertIs(result['not_for_activation'], True)
        self.assertEqual(self.state_path.read_bytes(), state_before)
        with zipfile.ZipFile(self.output / 'unaccepted-runtime.zip') as archive:
            self.assertNotIn('evidence/temporary-profile/Default/Cookies', archive.namelist())
            for name in [*self.record['files'], 'google-services-build.json']:
                self.assertEqual(archive.read('chromix/' + name), (self.bundle / name).read_bytes())
            self.assertEqual(archive.read('evidence/google-services-source.json'),
                             (self.bundle / 'google-services-source.json').read_bytes())
            self.assertIn('evidence/001-failed.log', archive.namelist())
        self.assertEqual(result['archive']['sha256'], diagnostic.sha256(self.output / 'unaccepted-runtime.zip'))
        self.assertFalse((self.output / 'chromix-win-x64.zip').exists())

    def test_only_bundle_success_and_verify_failure_are_eligible(self):
        for key, value in [('status', 'candidate-created'), ('status', 'budget-stop'),
                           ('phase', 'Compile'), ('finished', True),
                           ('completed', diagnostic.COMPLETED[:-1]), ('run_id', '456'),
                           ('source_commit', 'c' * 40), ('stage', True)]:
            original = copy.deepcopy(self.state)
            self.state[key] = value; self.save_state()
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.stage()
            self.assertFalse(self.output.exists())
            self.state = original; self.save_state()

    def test_changed_inventory_or_record_is_not_rebaselined(self):
        original = (self.bundle / 'chrome.dll').read_bytes()
        (self.bundle / 'chrome.dll').write_bytes(b'changed by a faulty verifier')
        with self.assertRaisesRegex(ValueError, 'not rebaselined'):
            self.stage()
        self.assertFalse(self.output.exists())
        (self.bundle / 'chrome.dll').write_bytes(original)
        (self.bundle / 'extra.txt').write_bytes(b'unexpected')
        with self.assertRaisesRegex(ValueError, 'not rebaselined'):
            self.stage()
        (self.bundle / 'extra.txt').unlink()
        (self.bundle / 'google-services-build.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'build record changed'):
            self.stage()

    def test_source_receipt_mismatch_blocks_diagnostics(self):
        source = json.loads((self.evidence / 'build-inputs.json').read_text())
        source['source_receipt_sha256'] = '0' * 64
        (self.evidence / 'build-inputs.json').write_text(json.dumps(source))
        with self.assertRaisesRegex(ValueError, 'source evidence identity'):
            self.stage()

    def test_during_archive_change_is_rejected_and_no_partial_zip_is_advertised(self):
        original = zipfile.ZipFile.open
        def mutate(archive, name, mode='r', *args, **kwargs):
            if name == 'chromix/chrome.dll' and mode == 'w':
                (self.bundle / 'chrome.exe').write_bytes(b'changed after precheck')
            return original(archive, name, mode, *args, **kwargs)
        with patch.object(zipfile.ZipFile, 'open', mutate):
            with self.assertRaisesRegex(ValueError, 'changed while archiving'):
                self.stage()
        self.assertFalse((self.output / 'unaccepted-runtime.zip').exists())
        self.assertFalse((self.output / 'diagnostic.json').exists())

    def test_duplicate_outer_source_receipt_must_match_and_is_not_duplicated(self):
        (self.evidence / 'google-services-source.json').write_bytes((self.bundle / 'google-services-source.json').read_bytes())
        self.stage()
        with zipfile.ZipFile(self.output / 'unaccepted-runtime.zip') as archive:
            self.assertEqual(archive.namelist().count('evidence/google-services-source.json'), 1)

    def test_workflow_preserves_diagnostic_before_checkpoint_without_using_candidate_name(self):
        worker = yaml.safe_load((ROOT / '.github/workflows/security-win157-worker.yml').read_text())
        steps = worker['jobs']['build']['steps']
        index = next(i for i, step in enumerate(steps) if step.get('id') == 'diagnostic')
        checkpoint = next(i for i, step in enumerate(steps) if step.get('id') == 'checkpoint')
        self.assertLess(index + 1, checkpoint)
        self.assertIn("steps.bundle.outcome == 'success'", steps[index]['if'])
        self.assertIn("steps.verify.outcome == 'failure'", steps[index]['if'])
        self.assertEqual(steps[index + 1]['with']['name'], 'unaccepted-runtime-s${{ inputs.stage }}')
        self.assertIn("steps.diagnostic.outputs.diagnostic == 'true'", steps[index + 1]['if'])
        script = (ROOT / 'build/windows/security-stage.ps1').read_text()
        block = script.split("if ($Phase -eq 'Diagnose') {", 1)[1].split("if ($Phase -eq 'Checkpoint')", 1)[0]
        self.assertNotIn('Save-State', block)
        self.assertNotIn('candidate-created', block)
        self.assertNotIn('$state.finished', block)


if __name__ == '__main__':
    unittest.main()
