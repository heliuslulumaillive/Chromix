"""A checkpoint handoff never cancels an unfinished producer or dispatches twice."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import security_handoff as h


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'handoff.json'
        self.plan = {'schema_version': 1, 'repository': 'owner/repo', 'donor_run': '123',
                     'producer_job': 456, 'donor_stage': 1, 'donor_commit': 'a' * 40,
                     'target_branch': 'codex/lite-win157-continuation', 'target_commit': 'b' * 40,
                     'handoff_id': 'test-handoff-123',
                     'workflow': 'build-google-services-win155.yml', 'max_stages': 4,
                     'retire_remaining_donor_stages': True}
        self.run = {'id': 789, 'head_sha': 'b' * 40, 'html_url': 'https://github.com/owner/repo/actions/runs/789'}
        self.ready = {'status': 'ready', 'preflight': {'manifest_artifact': {'id': 1}, 'volume_artifacts': [{'id': 2}], 'run_attempt': 1}}
        self.donor = {'status': 'in_progress', 'id': 123, 'head_sha': 'a' * 40, 'run_attempt': 1}

    def test_waiting_producer_never_dispatches_or_cancels(self):
        with patch.object(h, 'readiness', return_value={'status': 'waiting-for-donor-job'}), \
                patch.object(h.checkpoint, 'gh') as gh:
            self.assertEqual(h.handoff(self.root, self.plan, self.state)['status'], 'waiting-for-donor-job')
        gh.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_one_dispatch_precedes_cancel_and_repeated_call_is_noop(self):
        with patch.object(h, 'readiness', return_value=self.ready), \
                patch.object(h, 'find_dispatch', return_value=self.run), \
                patch.object(h, 'gh_json', return_value=self.donor), \
                patch.object(h.checkpoint, 'gh', return_value='') as gh:
            result = h.handoff(self.root, self.plan, self.state)
            h.handoff(self.root, self.plan, self.state)
        self.assertEqual([call.args[0][:2] for call in gh.call_args_list], [['workflow', 'run'], ['run', 'cancel']])
        self.assertEqual(result['continuation_run'], 789)
        self.assertTrue(result['producer_job_completed_before_dispatch'])

    def test_unknown_dispatch_response_is_reconciled_not_repeated(self):
        with patch.object(h, 'readiness', return_value=self.ready), \
                patch.object(h.checkpoint, 'gh', side_effect=RuntimeError('response lost')):
            with self.assertRaises(RuntimeError):
                h.handoff(self.root, self.plan, self.state)
        self.assertEqual(json.loads(self.state.read_text())['status'], 'dispatch-response-unknown')
        with patch.object(h, 'find_dispatch', return_value=None), patch.object(h.checkpoint, 'gh') as gh:
            self.assertEqual(h.handoff(self.root, self.plan, self.state)['status'], 'dispatch-response-unknown')
        gh.assert_not_called()

    def test_failed_cancel_can_be_retried_without_another_dispatch(self):
        with patch.object(h, 'readiness', return_value=self.ready), \
                patch.object(h, 'find_dispatch', return_value=self.run), \
                patch.object(h, 'gh_json', return_value=self.donor), \
                patch.object(h.checkpoint, 'gh', side_effect=['', RuntimeError('cancel response lost')]):
            with self.assertRaises(RuntimeError):
                h.handoff(self.root, self.plan, self.state)
        with patch.object(h, 'gh_json', side_effect=[self.run, self.donor]), \
                patch.object(h.checkpoint, 'gh', return_value='') as gh:
            h.handoff(self.root, self.plan, self.state)
        self.assertEqual(gh.call_args.args[0][:2], ['run', 'cancel'])
        self.assertEqual(gh.call_count, 1)

    def test_no_checkpoint_requires_review_not_cold_fallback(self):
        with patch.object(h, 'readiness', return_value={'status': 'review-required', 'cold_fallback_started': False}), \
                patch.object(h.checkpoint, 'gh') as gh:
            result = h.handoff(self.root, self.plan, self.state)
        self.assertFalse(result['cold_fallback_started'])
        gh.assert_not_called()

    def test_job_identity_checked_before_status(self):
        with patch.object(h, 'gh_json', return_value={'run_id': 124, 'head_sha': 'a' * 40, 'status': 'in_progress'}):
            with self.assertRaisesRegex(ValueError, 'different source/run'):
                h.readiness(self.root, self.plan)

    def test_same_commit_other_inputs_are_not_this_dispatch(self):
        run = {**self.run, 'display_title': 'Windows 157 / fresh / manual',
               'created_at': '2026-10-31T12:00:00Z'}
        with patch.object(h, 'gh_json', return_value={'workflow_runs': [run]}):
            self.assertIsNone(h.find_dispatch(self.plan, {'requested_at': '2026-10-31T11:59:59+00:00'}))

    def test_current157_dispatch_is_reconciled_and156_is_not_a_match(self):
        state = {'requested_at': '2026-11-07T20:00:00+00:00'}
        current = {**self.run, 'display_title':'Windows 157 / upgrade / '+self.plan['handoff_id'],
                   'created_at':'2026-11-07T20:00:01Z'}
        with patch.object(h, 'gh_json', return_value={'workflow_runs':[current]}):
            self.assertEqual(h.find_dispatch(self.plan, state)['id'], self.run['id'])
        old = {**current, 'display_title':'Windows 156 / upgrade / '+self.plan['handoff_id']}
        with patch.object(h, 'gh_json', return_value={'workflow_runs':[old]}):
            self.assertIsNone(h.find_dispatch(self.plan, state))

    def test_dispatch_choice_validation_and_route_agree_on157(self):
        import yaml
        root = Path(__file__).resolve().parents[2]
        workflow = yaml.load((root/'.github/workflows/build-google-services-win155.yml').read_text(), Loader=yaml.BaseLoader)
        choices = workflow['on']['workflow_dispatch']['inputs']['build_variant']['options']
        self.assertIn('fingerprint-modes-win157', choices)
        self.assertNotIn('fingerprint-modes-win156', choices)
        self.assertEqual(workflow['jobs']['security-win157']['uses'], './.github/workflows/security-win157.yml')
        block = workflow['jobs']['validate-inputs']['steps'][0]['run']
        script = block.split('\n',1)[1].rsplit('\nPY',1)[0]
        for variant in ['fingerprint-modes-win157','fingerprint-modes-win156']:
            for operation in ['fresh','upstream','resume','upgrade']:
                env = {**os.environ, 'BUILD_VARIANT':variant, 'OPERATION':operation,
                       'SOURCE_RUN':'123', 'SOURCE_STAGE':'1', 'HANDOFF_ID':'test-handoff-123'}
                with self.subTest(variant=variant, operation=operation):
                    run = subprocess.run([sys.executable,'-c',script], env=env, capture_output=True, text=True)
                    self.assertEqual(run.returncode==0, variant=='fingerprint-modes-win157', run.stderr)

    def test_new_donor_attempt_is_never_cancelled(self):
        with patch.object(h, 'readiness', return_value=self.ready), \
                patch.object(h, 'find_dispatch', return_value=self.run), \
                patch.object(h, 'gh_json', return_value={**self.donor, 'run_attempt': 2}), \
                patch.object(h.checkpoint, 'gh', return_value='') as gh:
            result = h.handoff(self.root, self.plan, self.state)
        self.assertEqual(gh.call_count, 1)
        self.assertEqual(gh.call_args.args[0][:2], ['workflow', 'run'])
        self.assertFalse(result['remaining_donor_stages_cancel_requested'])

    def test_leftover_lock_file_does_not_block_recovery(self):
        self.state.with_name(self.state.name + '.lock').write_bytes(b'\0')
        with patch.object(h, 'readiness', return_value={'status': 'waiting-for-donor-job'}):
            result = h.handoff(self.root, self.plan, self.state)
        self.assertEqual(result['status'], 'waiting-for-donor-job')

    def test_unknown_persisted_status_cannot_dispatch_again(self):
        self.state.write_text(json.dumps({'plan': self.plan, 'status': 'unexpected'}))
        with patch.object(h.checkpoint, 'gh') as gh, self.assertRaisesRegex(ValueError, 'Unknown handoff state'):
            h.handoff(self.root, self.plan, self.state)
        gh.assert_not_called()

    def test_failed_donor_still_requires_review_and_cannot_start_transfer(self):
        job = {'run_id': 123, 'head_sha': 'a' * 40, 'status': 'completed', 'conclusion': 'failure'}
        with patch.object(h, 'gh_json', return_value=job), patch.object(h.checkpoint, 'preflight') as preflight:
            result = h.readiness(self.root, self.plan)
        self.assertEqual(result['status'], 'review-required')
        preflight.assert_not_called()

    def test_explicit_budget_review_runs_before_readiness(self):
        from tools import security_budget_review as budget
        plan = {**self.plan, 'reviewed_budget_stop': {'version': 1, 'test': 'validated separately'}}
        job = {'run_id': 123, 'head_sha': 'a' * 40, 'status': 'completed', 'conclusion': 'failure',
               'run_attempt': 1, 'started_at': '2026-10-31T06:00:00Z', 'completed_at': '2026-10-31T12:00:00Z'}
        pre = {'run_attempt': 1, 'manifest_artifact': {'created_at': '2026-10-31T11:59:00Z'}}
        with patch.object(h, 'gh_json', side_effect=[job, {'object': {'sha': 'b' * 40}}]), \
                patch.object(h.subprocess, 'check_output', side_effect=['b' * 40, '']), \
                patch.object(h.checkpoint, 'preflight', return_value=pre), \
                patch.object(budget, 'verify', return_value={'status': 'reviewed-budget-stop'}) as proof:
            result = h.readiness(self.root, plan)
        proof.assert_called_once_with('owner/repo', '123', job, pre, plan['reviewed_budget_stop'])
        self.assertEqual(result['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
