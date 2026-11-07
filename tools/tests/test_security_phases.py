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
WORKER = ROOT / '.github/workflows/security-win157-worker.yml'
PWSH = shutil.which('pwsh') or shutil.which('pwsh.exe')
PHASES = ['Acquire', 'Extract', 'Prepare', 'Configure', 'Compile', 'Bundle', 'Verify', 'Archive']
HARNESS = r'''
param([string]$Mode,[string]$Payload)
$ErrorActionPreference = 'Stop'
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($env:PHASE_TEST_SOURCE,[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors.Message -join '; ') }
$allowed = @('Assert-PhasePredecessor','Get-StageContinuation','Get-CheckpointDiskBudget','Get-OwnedCommandStatus','Get-NinjaFailureSummary','Get-OwnedFailureSummary','Write-OwnedFailureAnnotation','Invoke-Owned','Invoke-VerificationPlan','Read-State')
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
    'command-status' { $result=@{status=(Get-OwnedCommandStatus $request.exit_code $request.receipt)} }
    'ninja-failures' { $result=Get-NinjaFailureSummary $request.path }
    'owned-failure' { $result=Get-OwnedFailureSummary $request.label $request.exit_code $request.receipt $request.path }
    'owned-annotation' { $result=@{message=(Write-OwnedFailureAnnotation $request.label 'failed' $request.exit_code $request.receipt $request.path)} }
    'native-test-filter' {
      $nativeAST=[Management.Automation.Language.Parser]::ParseFile($env:NATIVE_TEST_SOURCE,[ref]$tokens,[ref]$parseErrors)
      if ($parseErrors.Count) { throw 'Native test launcher parse failed' }
      # Bind the production ParamBlock without executing a browser or the body.
      $binder=[scriptblock]::Create($nativeAST.ParamBlock.Extent.Text + "`n`$true")
      & $binder -Executable 'not-executed' -Summary 'not-written' -Log 'not-written' -Receipt 'not-written' -WorkingDirectory 'not-read' -TestFilter $request.filter | Out-Null
      $result=@{accepted=$true}
    }
    'verification-plan' {
      $evidence='synthetic-not-written'; $state=@{resume_safe=$true}
      $called=[Collections.Generic.List[string]]::new()
      function Json([string]$Path,$Data) { $script:verificationReport=$Data }
      function Invoke-Owned([string]$Label,[string[]]$Command) {
        $called.Add($Label)
        if ($Label -eq $request.unsafe) { $state.resume_safe=$false; throw 'synthetic-cleanup-failure' }
        if ($Label -eq $request.timeout) { throw 'SECURITY_BUDGET_STOP' }
        if ($Label -in $request.fail) { throw 'synthetic-test-failure' }
      }
      try { Invoke-VerificationPlan $request.steps; $errorName=$null }
      catch { $errorName=$_.Exception.Message }
      $result=@{called=@($called.ToArray());report=$script:verificationReport;error=$errorName;resume_safe=$state.resume_safe}
    }
    'ninja-timeout' {
      $repo=$request.repo; $evidence=$request.repo; $python=$request.python
      $deadline=[DateTimeOffset]::UtcNow.AddMinutes(1)
      $state=@{command_number=0;resume_safe=$true}
      $env:SYNTHETIC_NINJA_LOG=$request.log
      function Save-State {}
      function Json([string]$Path,$Data) { [IO.File]::WriteAllText($Path,($Data|ConvertTo-Json -Depth 10)) }
      try { Invoke-Owned 'ninja' @('synthetic-not-executed') | Out-Null; $errorName='unexpected-success' }
      catch { $errorName=$_.Exception.Message }
      $result=@{error=$errorName;resume_safe=$state.resume_safe;needs_source_fix=($state.needs_source_fix -eq $true)}
      $global:LASTEXITCODE=0
    }
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
               'NATIVE_TEST_SOURCE': str(ROOT / 'build/windows/run-native-passkey-tests.ps1'),
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

    def test_native_launcher_actually_binds_each_required_filter(self):
        for value in ('WinAuthenticatorTest.*:AuthenticatorRequestDialogControllerTest.*',
                      'ShardXNativeBrowserTest.*'):
            self.assertTrue(self.run_guard('native-test-filter', {'filter': value})['accepted'])
        self.assertFalse(self.run_guard('native-test-filter', {'filter': '*'})['accepted'])

    def verification_plan(self, **extra):
        return self.run_guard('verification-plan', {'steps': [
            {'label': 'first', 'command': ['synthetic']},
            {'label': 'accept-first', 'command': ['synthetic'], 'depends': ['first']},
            {'label': 'second', 'command': ['synthetic']},
            {'label': 'third', 'command': ['synthetic']},
        ], **extra})

    def test_verification_plan_runs_all_required_checks_when_successful(self):
        result = self.verification_plan()
        self.assertIsNone(result['error'])
        self.assertEqual(result['called'], ['first', 'accept-first', 'second', 'third'])
        self.assertEqual(result['report']['status'], 'passed')
        self.assertTrue(all(row['status'] == 'passed' for row in result['report']['checks']))

    def test_independent_failures_are_collected_and_dependents_not_accepted(self):
        result = self.verification_plan(fail=['first', 'third'])
        self.assertEqual(result['called'], ['first', 'second', 'third'])
        self.assertEqual(result['report']['status'], 'failed')
        self.assertEqual(result['report']['checks'][1]['status'], 'skipped')
        self.assertEqual(result['error'], 'SECURITY_VERIFICATION_FAILED: first, third')

    def test_prior_failure_cannot_become_a_budget_continuation(self):
        result = self.verification_plan(fail=['first'], timeout='second')
        self.assertEqual(result['called'], ['first', 'second'])
        self.assertEqual(result['report']['status'], 'failed')
        self.assertEqual(result['error'], 'SECURITY_VERIFICATION_FAILED: first')
        self.assertEqual(result['report']['checks'][-1]['reason'], 'budget-exhausted')

    def test_clean_budget_stop_preserves_existing_continuation_semantics(self):
        result = self.verification_plan(timeout='second')
        self.assertEqual(result['called'], ['first', 'accept-first', 'second'])
        self.assertEqual(result['report']['status'], 'budget-stop')
        self.assertEqual(result['error'], 'SECURITY_BUDGET_STOP')

    def test_unverified_process_cleanup_stops_further_independent_commands(self):
        result = self.verification_plan(unsafe='first')
        self.assertEqual(result['called'], ['first'])
        self.assertFalse(result['resume_safe'])
        self.assertEqual(result['report']['status'], 'failed')
        self.assertTrue(all(row['reason'] == 'cleanup-unverified' for row in result['report']['checks'][1:]))

    def test_missing_dependency_is_not_a_passing_verification_plan(self):
        result = self.verification_plan(steps=[{'label': 'accept-missing', 'command': ['synthetic'], 'depends': ['missing']}])
        self.assertEqual(result['called'], [])
        self.assertEqual(result['report']['status'], 'failed')
        self.assertEqual(result['error'], 'SECURITY_VERIFICATION_INCOMPLETE')

    def test_failed_ninja_edges_are_preserved_even_when_other_targets_continue(self):
        path = Path(self.temp.name) / 'ninja.log'
        path.write_text('[1/9] CXX good.obj\nFAILED: obj/broken.obj\n'
                        'file.cc: error: missing declaration\n[3/9] CXX useful.obj\n', encoding='utf-8')
        result = self.run_guard('ninja-failures', {'path': str(path)})
        self.assertTrue(result['needs_source_fix'])
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['failures'][0], {'line': 2, 'target': 'obj/broken.obj'})
        self.assertEqual(result['diagnostics'], [{'line': 3, 'message': 'file.cc: error: missing declaration'}])
        self.assertRegex(result['log_sha256'], r'^[a-f0-9]{64}$')
        path.write_text('\x1b[31mFAILED:\x1b[0m obj/color.obj\n[2/9] CXX useful.obj\n', encoding='utf-8')
        self.assertEqual(self.run_guard('ninja-failures', {'path': str(path)})['count'], 1)
        path.write_text('[1/9] CXX good.obj\nwarning: example contains FAILED: as text\n', encoding='utf-8')
        self.assertFalse(self.run_guard('ninja-failures', {'path': str(path)})['needs_source_fix'])

    def test_compiler_diagnostics_are_bounded_and_survive_later_successful_work(self):
        path = Path(self.temp.name) / 'ninja-diagnostics.log'
        errors = ['file.cc(39,14): \x1b[31merror:\x1b[0m ' + 'x' * 2000 for _ in range(20)]
        path.write_text('FAILED: obj/broken.obj\n' + '\n'.join(errors) +
                        '\n' + '[next] CXX useful.obj\n' * 100, encoding='utf-8')
        result = self.run_guard('ninja-failures', {'path': str(path)})
        self.assertEqual(len(result['diagnostics']), 8)
        self.assertTrue(all(len(row['message']) == 1200 and '\x1b' not in row['message']
                            for row in result['diagnostics']))
        self.assertEqual(result['diagnostics'][0]['line'], 2)

    def test_generic_failure_annotations_do_not_echo_cookie_values_or_exception_messages(self):
        path = Path(self.temp.name) / '019-native-cookie-runtime.log'
        secret = 'DO-NOT-ECHO-RAW-COOKIE-VALUE'
        path.write_text(json.dumps({'status': 'failed', 'failed': ['first_request/cookies', 'receipt/status'],
                                    'error': secret, 'cookie': secret}) + '\nValueError: ' + secret,
                        encoding='utf-8')
        payload = {'label': 'native-cookie-runtime', 'exit_code': 1, 'path': str(path),
                   'receipt': {'timed_out': False, 'cleanup_errors': []}}
        result = self.run_guard('owned-failure', payload)
        self.assertEqual(result['failed_checks'], ['first_request/cookies', 'receipt/status'])
        self.assertEqual(result['exception_type'], 'ValueError')
        annotation = self.run_guard('owned-annotation', payload)['message']
        self.assertTrue(annotation.startswith('::error title=Owned verification command::'))
        self.assertIn('first_request/cookies', annotation)
        self.assertNotIn(secret, annotation)
        self.assertNotIn(str(path.parent), annotation)
        self.assertLessEqual(len(annotation), 1260)

    def test_generic_failure_summary_only_accepts_bounded_identifiers_from_known_verifiers(self):
        path = Path(self.temp.name) / '020-native-cookie-runtime.log'
        path.write_text(json.dumps({'failed': ['check/' + str(n) for n in range(20)] +
                                    ['https://private.test/?token=secret', '\n::error::injected']}), encoding='utf-8')
        payload = {'label': 'native-cookie-runtime', 'exit_code': 5, 'path': str(path),
                   'receipt': {'timed_out': True, 'cleanup_errors': ['hidden detail']}}
        result = self.run_guard('owned-failure', payload)
        self.assertEqual(len(result['failed_checks']), 8)
        self.assertEqual(result['cleanup_error_count'], 1)
        self.assertTrue(result['timed_out'])
        payload['label'] = 'unreviewed-helper'
        self.assertEqual(self.run_guard('owned-failure', payload)['failed_checks'], [])

    def test_actual_invoker_prioritizes_recorded_failure_over_later_timeout(self):
        import sys
        for name, log, expected in [('failed', 'FAILED: obj/broken.obj\n[3/9] CXX useful.obj\n', True),
                                    ('budget', '[3/9] CXX useful.obj\n', False)]:
            repo = Path(self.temp.name) / ('invoker-' + name)
            (repo / 'tools').mkdir(parents=True)
            (repo / 'tools/security_run.py').write_text(
                "import json,os,sys\nfrom pathlib import Path\na=sys.argv\n"
                "Path(a[a.index('--log')+1]).write_text(os.environ['SYNTHETIC_NINJA_LOG'])\n"
                "Path(a[a.index('--report')+1]).write_text(json.dumps({'timed_out':True,'exit_code':None,'cleanup_errors':[]}))\n"
                "raise SystemExit(124)\n", encoding='utf-8')
            result = self.run_guard('ninja-timeout', {'repo': str(repo), 'python': sys.executable, 'log': log})
            self.assertEqual(result['needs_source_fix'], expected)
            self.assertTrue(result['resume_safe'])
            if expected:
                self.assertIn('Security stage command failed: ninja', result['error'])
                self.assertNotEqual(result['error'], 'SECURITY_BUDGET_STOP')
            else:
                self.assertEqual(result['error'], 'SECURITY_BUDGET_STOP')

    def test_native_exit_124_is_not_a_timeout_and_cleanup_failure_is_unsafe(self):
        cases = [
            (124, {'timed_out': True, 'exit_code': None, 'cleanup_errors': []}, 'budget-stop'),
            (124, {'timed_out': False, 'exit_code': 124, 'cleanup_errors': []}, 'failed'),
            (124, {'timed_out': True, 'exit_code': None, 'cleanup_errors': ['still running']}, 'unsafe'),
            (0, {'timed_out': False, 'exit_code': 0, 'cleanup_errors': []}, 'success'),
            (1, {'timed_out': False, 'exit_code': 1, 'cleanup_errors': []}, 'failed'),
            (0, {'timed_out': True, 'exit_code': None, 'cleanup_errors': []}, 'unsafe'),
            (124, {}, 'unsafe'),
        ]
        for rc, receipt, expected in cases:
            with self.subTest(rc=rc, receipt=receipt):
                result = self.run_guard('command-status', {'exit_code': rc, 'receipt': receipt})
                self.assertEqual(result['status'], expected)

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

    def test_host_cpp_prerequisites_are_checked_before_large_transfers(self):
        text = STAGE.read_text(encoding='utf-8')
        acquisition = text.split("    'Acquire' {", 1)[1].split("    'Extract' {", 1)[0]
        checkpoint, other = acquisition.split('      } else {', 1)
        for branch, transfer in ((checkpoint, "'checkpoint-download'"), (other, "'exact-upstream-download'")):
            self.assertLess(branch.index('Clear-HostedToolSpace'), branch.index('Confirm-HostedCompilerPrerequisites'))
            self.assertLess(branch.index('Confirm-HostedCompilerPrerequisites'), branch.index(transfer))
        self.assertLess(checkpoint.index('Confirm-HostedCompilerPrerequisites'), checkpoint.index('Get-CheckpointDiskBudget'))
        helper = text.split('function Confirm-HostedCompilerPrerequisites {', 1)[1].split('function Set-CompilerEnvironment', 1)[0]
        self.assertIn('Microsoft.VisualStudio.Component.VC.Tools.x86.x64', helper)
        self.assertIn("'-ChromiumVersion',$version,'-Install'", helper)


if __name__ == '__main__':
    unittest.main()
