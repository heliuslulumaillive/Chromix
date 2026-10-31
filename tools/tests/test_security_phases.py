"""Exercise the real PowerShell phase guards without invoking the hosted build."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / 'build/windows/security-stage.ps1'
WORKER = ROOT / '.github/workflows/security-win156-worker.yml'
PWSH = shutil.which('pwsh') or shutil.which('pwsh.exe')
PHASES = ['Acquire', 'Extract', 'Prepare', 'Configure', 'Compile', 'Bundle', 'Verify', 'Archive']
HARNESS = r'''
param([string]$Mode,[string]$Payload)
$ErrorActionPreference = 'Stop'
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($env:PHASE_TEST_SOURCE,[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors.Message -join '; ') }
$allowed = @('Assert-PhasePredecessor','Get-StageContinuation','Get-CheckpointDiskBudget','Invoke-Owned','Read-State')
$definitions = $ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in $allowed}, $false)
if (@($definitions).Count -ne $allowed.Count) { throw 'Guard function inventory mismatch' }
. ([scriptblock]::Create(($definitions.Extent.Text -join "`n")))
$request = $Payload | ConvertFrom-Json -AsHashtable
try {
  switch ($Mode) {
    'order' { Assert-PhasePredecessor $request.state $request.phase; $result=@{accepted=$true} }
    'result' { $result=Get-StageContinuation $request.state $request.candidate $request.manifest $request.retain }
    'budget' {
      $deadline=[DateTimeOffset]::UtcNow.AddSeconds(-1)
      # An expired budget must fail before consulting any tool or writing state.
      $python='nonexistent-must-not-execute'; $repo='nonexistent-must-not-read'
      Invoke-Owned 'not-run' @('nonexistent-must-not-execute')
      $result=@{accepted=$true}
    }
    'identity' {
      $statePath=$request.path; $Stage=$request.stage
      $result=Read-State
    }
    'disk' { $result=Get-CheckpointDiskBudget $request }
    default { throw 'Unknown test mode' }
  }
  $result | ConvertTo-Json -Depth 15 -Compress
} catch {
  @{accepted=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress
}
'''


@unittest.skipUnless(PWSH, 'PowerShell 7 is required to execute phase guard tests')
class PhaseGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.harness = Path(cls.temp.name) / 'guard.ps1'
        cls.harness.write_text(HARNESS, encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_guard(self, mode, payload):
        env = {**os.environ, 'PHASE_TEST_SOURCE': str(STAGE),
               'GITHUB_RUN_ID': '123', 'GITHUB_SHA': 'a' * 40}
        proc = subprocess.run([PWSH, '-NoProfile', '-File', str(self.harness), mode,
                               json.dumps(payload, separators=(',', ':'))],
                              capture_output=True, text=True, encoding='utf-8', env=env, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_all_real_guards_admit_only_exact_next_phase(self):
        for i, phase in enumerate(PHASES):
            with self.subTest(phase=phase):
                result = self.run_guard('order', {'state': {'status': 'running', 'completed': PHASES[:i]}, 'phase': phase})
                self.assertTrue(result['accepted'])

    def test_skipped_repeated_or_reordered_phase_rejected(self):
        for completed, phase in [([], 'Compile'), (['Acquire'], 'Acquire'),
                                 (['Extract', 'Acquire'], 'Prepare'), (PHASES, 'Archive')]:
            with self.subTest(completed=completed, phase=phase):
                result = self.run_guard('order', {'state': {'status': 'running', 'completed': completed}, 'phase': phase})
                self.assertFalse(result['accepted'])

    def test_failed_or_budget_stopped_stage_cannot_continue_work(self):
        for status in ['failed', 'budget-stop', 'candidate-created']:
            result = self.run_guard('order', {'state': {'status': status, 'completed': PHASES[:4]}, 'phase': 'Compile'})
            self.assertFalse(result['accepted'])

    def test_only_budget_stop_with_complete_prepared_checkpoint_auto_resumes(self):
        cases = [('budget-stop', True, True, True), ('budget-stop', False, True, False),
                 ('budget-stop', True, False, False), ('failed', True, True, False),
                 ('running', True, True, False)]
        for status, safe, manifest, expected in cases:
            result = self.run_guard('result', {'state': {'status': status, 'resume_safe': safe, 'completed': PHASES[:4]},
                                               'candidate': False, 'manifest': manifest, 'retain': True})
            self.assertEqual(result['resume_needed'], expected)
            self.assertFalse(result['finished'])

    def test_candidate_requires_completed_archive_and_delivery(self):
        for completed, delivered, expected in [(PHASES, True, True), (PHASES[:-1], True, False), (PHASES, False, False)]:
            result = self.run_guard('result', {'state': {'status': 'candidate-created', 'finished': True, 'completed': completed},
                                               'candidate': delivered, 'manifest': True, 'retain': True})
            self.assertEqual(result['finished'], expected)
            self.assertFalse(result['resume_needed'])

    def test_requested_checkpoint_failure_is_reported_even_after_candidate_delivery(self):
        for retained, expected in [(True, True), (False, False)]:
            result = self.run_guard('result', {'state': {'status': 'candidate-created', 'finished': True, 'completed': PHASES},
                                               'candidate': True, 'manifest': False, 'retain': retained})
            self.assertTrue(result['finished'])
            self.assertFalse(result['checkpoint_ready'])
            self.assertEqual(result['retention_failed'], expected)

    def test_expired_budget_cannot_start_a_command(self):
        self.assertEqual(self.run_guard('budget', {})['error'], 'SECURITY_BUDGET_STOP')

    def test_state_from_another_stage_run_or_checkout_is_rejected(self):
        path = Path(self.temp.name) / 'state.json'
        good = {'schema_version': 2, 'stage': 1, 'run_id': '123', 'source_commit': 'a' * 40}
        path.write_text(json.dumps(good), encoding='utf-8')
        self.assertEqual(self.run_guard('identity', {'path': str(path), 'stage': 1}), good)
        for key, value in [('schema_version', 1), ('stage', 2), ('run_id', '124'), ('source_commit', 'b' * 40)]:
            path.write_text(json.dumps({**good, key: value}), encoding='utf-8')
            result = self.run_guard('identity', {'path': str(path), 'stage': 1})
            self.assertFalse(result['accepted'])

    def test_disk_budget_includes_transfer_tree_and_reserve_without_guessing_old_tree_size(self):
        parts = [{'bytes': 10}, {'bytes': 20}]
        known = self.run_guard('disk', {'parts': parts, 'tree_bytes': 200})
        self.assertEqual(known['required_free_bytes'], 30 + 200 + 24 * 1024**3)
        self.assertTrue(known['tree_size_known'])
        legacy = self.run_guard('disk', {'parts': parts})
        self.assertEqual(legacy['required_free_bytes'], 30 + 24 * 1024**3)
        self.assertFalse(legacy['tree_size_known'])
        self.assertEqual(legacy['uncompressed_bytes'], 0)
        for bad in [{'parts': []}, {'parts': [{'bytes': -1}]}, {'parts': parts, 'tree_bytes': 0}]:
            self.assertFalse(self.run_guard('disk', bad)['accepted'])


class WorkflowPhaseTests(unittest.TestCase):
    def setUp(self):
        self.worker = yaml.load(WORKER.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        self.steps = self.worker['jobs']['build']['steps']

    def test_all_phases_share_one_runner_and_have_sequential_success_gates(self):
        self.assertEqual(list(self.worker['jobs']), ['build'])
        indexed = {row.get('id'): row for row in self.steps if row.get('id')}
        actual = [row['id'] for row in self.steps if row.get('id') in {p.lower() for p in PHASES}]
        self.assertEqual(actual, [p.lower() for p in PHASES])
        for prior, phase in zip(PHASES, PHASES[1:]):
            row = indexed[phase.lower()]
            self.assertEqual(row['if'], "${{ steps." + prior.lower() + ".outputs.proceed == 'true' }}")
            self.assertIn('-Phase ' + phase, row['run'])
        self.assertIn("steps.archive.outputs.finished == 'true'", indexed['candidate']['if'])

    def test_only_acquisition_receives_github_read_token(self):
        for row in self.steps:
            if 'GH_TOKEN' in row.get('env', {}):
                self.assertEqual(row.get('id'), 'acquire')

    def test_checkpoint_is_explicit_and_manifest_requires_uploaded_parts(self):
        indexed = {row.get('id'): row for row in self.steps if row.get('id')}
        self.assertEqual(self.worker['on']['workflow_call']['inputs']['retain_checkpoint']['default'], 'true')
        self.assertIn('inputs.retain_checkpoint', indexed['checkpoint']['if'])
        for part in ['part1', 'part2', 'part3', 'part4']:
            self.assertIn(f"steps.{part}.outcome == 'success'", indexed['manifest']['if'])
        self.assertEqual(indexed['result']['env']['RETAIN_CHECKPOINT'], '${{ inputs.retain_checkpoint }}')
        self.assertIn('-Phase Result', indexed['result']['run'])

    def test_sdk_environment_precedes_cache_inspection_and_installs_only_once(self):
        text = STAGE.read_text(encoding='utf-8')
        prepare = text.split("    'Prepare' {", 1)[1].split("    'Configure' {", 1)[0]
        self.assertLess(prepare.index("Invoke-Owned 'sdk'"), prepare.index("'base-prepare'"))
        self.assertLess(prepare.index('Set-CompilerEnvironment -WithoutSourceTools'),
                        prepare.index("'restored-environment-inspect'"))
        self.assertEqual(text.count("Invoke-Owned 'sdk'"), 1)
        self.assertNotIn("Remove-Item -LiteralPath $out", text)
        self.assertLess(text.index("'checkpoint-preflight'"), text.index("'checkpoint-download'"))
        self.assertLess(text.index("'checkpoint-tree'"), text.index("'migration-source'"))

    def test_checkpoint_disk_admission_precedes_large_operations(self):
        text = STAGE.read_text(encoding='utf-8')
        acquisition = text.split("    'Acquire' {", 1)[1].split("    'Extract' {", 1)[0]
        checkpoint = acquisition.split("if ($Operation -in @('resume','upgrade')) {", 1)[1].split('      } else {', 1)[0]
        self.assertLess(checkpoint.index("'checkpoint-preflight'"), checkpoint.index('Clear-HostedToolSpace'))
        self.assertLess(checkpoint.index('Clear-HostedToolSpace'), checkpoint.index('Get-CheckpointDiskBudget'))
        self.assertLess(checkpoint.index('required_free_bytes'), checkpoint.index("'checkpoint-download'"))
        extraction = text.split("    'Extract' {", 1)[1].split("    'Prepare' {", 1)[0]
        self.assertLess(extraction.index("'checkpoint-archive-list'"), extraction.index('requiredExtractionSpace'))
        self.assertLess(extraction.index('Insufficient free disk space'), extraction.index("'checkpoint-extract'"))
        self.assertIn("'--output',$layoutPath", extraction)
        self.assertIn("'--tree-bytes'", text)


if __name__ == '__main__':
    unittest.main()
