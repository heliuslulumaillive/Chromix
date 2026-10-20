"""Regression tests for verification-only routing and retained build identity."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout
from types import SimpleNamespace

import yaml

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
with mock.patch.object(sys, 'path', [str(TOOLS), *sys.path]):
    import google_services_cache_audit as audit
    import verify_google_services_candidate as candidate


def source_run():
    return {'repository': 'fixture/chromix', 'run_id': 123, 'run_attempt': 1,
            'head_sha': 'a' * 40, 'artifact_id': 456, 'artifact_name': 'google-services-win155-x64'}


def source_recipe():
    return {'variant': 'google-services-self-use', 'chromium_version': candidate.VERSION,
            'patch_sha256': candidate.sha256(REPO / 'patches/google-services/0001-restore-google-services.patch'),
            'preparation_inputs': {name: candidate.sha256(REPO / name) for name in (
                'build/google-services-win155.json',
                'patches/google-services/0001-restore-google-services.json',
                'patches/google-services/0002-windows-native-passkey-discovery.patch',
                'patches/chromium155-windows/0233-renderer-timezone-webstring-api.patch')}}


class CandidateIdentityTest(unittest.TestCase):
    def test_requires_source_run_and_named_artifact(self):
        candidate.validate_source_run(source_run())
        for field, value in [('run_id', True), ('artifact_id', -1), ('head_sha', 'main'),
                             ('artifact_name', 'google-win155-tree-s1-part1')]:
            changed = {**source_run(), field: value}
            with self.assertRaises(ValueError):
                candidate.validate_source_run(changed)

    def test_rejects_wrong_variant_patch_and_mixed_receipts(self):
        receipt = source_recipe()
        candidate.validate_source_recipe(receipt, copy.deepcopy(receipt))
        with self.assertRaises(ValueError):
            candidate.validate_source_recipe(receipt, {**receipt, 'extra': True})
        for field, value in [('variant', 'unrelated'), ('chromium_version', '154.0.0.0'),
                             ('patch_sha256', '0' * 64), ('preparation_inputs', {})]:
            changed = {**receipt, field: value}
            with self.assertRaises(ValueError):
                candidate.validate_source_recipe(changed, copy.deepcopy(changed))

    def test_rejects_changed_restore_configuration(self):
        receipt = source_recipe()
        receipt['preparation_inputs']['build/google-services-win155.json'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'source inputs'):
            candidate.validate_source_recipe(receipt, copy.deepcopy(receipt))

    def exercise_verification(self, mutation=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact, reports, bundle = root / 'artifact', root / 'reports', root / 'browser/chromix'
            for path in (artifact, reports, bundle):
                path.mkdir(parents=True)
            archive = artifact / 'chromix-win-x64.zip'
            archive.write_bytes(b'original archive')
            (artifact / 'native-passkey-unittests.json').write_text(json.dumps({
                'per_iteration_data': [{name: [{'status': 'SUCCESS'}]
                                        for name in candidate.NATIVE_PASSKEY_TESTS}]}))
            receipt = source_recipe()
            for path in (artifact, bundle):
                (path / 'google-services-source.json').write_text(json.dumps(receipt))
            (bundle / 'chrome.exe').write_bytes(b'original exe')
            (bundle / 'chrome.dll').write_bytes(b'original dll')
            static = {'bundle_dir': str(bundle), 'arch': 'x64', 'archive': {'sha256': candidate.sha256(archive)}}
            before = candidate.inventory(bundle)

            def probe(command, **_kwargs):
                self.assertIn('verify_google_services_runtime.py', str(command[4]))
                evidence = {'status': 'passed', 'browser_sha256': before['chrome.exe'],
                            'chrome_dll_sha256': before['chrome.dll'],
                            'checks': [{'name': name, 'passed': True} for name in candidate.REQUIRED_CHECKS]}
                if mutation == 'missing_check':
                    evidence['checks'] = []
                if mutation == 'wrong_binary':
                    evidence['chrome_dll_sha256'] = '0' * 64
                if mutation == 'changed_file':
                    (bundle / 'chrome.dll').write_bytes(b'modified by test')
                (reports / 'google-webauthn.json').write_text(json.dumps(evidence))
                return {'exit_code': 0, 'timed_out': mutation == 'timeout', 'cleanup_errors': []}

            result = {'status': 'failed'}
            with mock.patch.object(candidate, 'extract_archive', return_value=static), \
                    mock.patch.object(candidate, 'runtime_smoke', return_value={'runtime': {'status': 'passed'}}), \
                    mock.patch.object(candidate, 'run_command', side_effect=probe), \
                    mock.patch.dict(os.environ, {'GITHUB_SHA': 'b' * 40}):
                candidate.verify_candidate(artifact, source_run(), reports, result)
            self.assertEqual(archive.read_bytes(), b'original archive')
            return result

    def test_verification_does_not_relabel_the_build_commit(self):
        result = self.exercise_verification()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['built_source_commit'], 'a' * 40)
        self.assertEqual(result['verification_commit'], 'b' * 40)
        self.assertFalse(result['compiler_invoked'])
        self.assertFalse(result['source_checkpoint_restored'])
        self.assertEqual(result['google_account_and_cloud_access'], 'not_tested')

    def test_incomplete_wrong_binary_and_mutated_results_cannot_pass(self):
        for mutation in ('missing_check', 'wrong_binary', 'changed_file', 'timeout'):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.exercise_verification(mutation)


class NativePasskeyEvidenceTest(unittest.TestCase):
    def test_requires_executed_regressions_and_rejects_failed_retries(self):
        results = {name: [{'status': 'SUCCESS'}] for name in candidate.NATIVE_PASSKEY_TESTS}
        self.assertEqual(set(candidate.validate_native_passkey_tests(
            {'per_iteration_data': [results]})), candidate.NATIVE_PASSKEY_TESTS)
        missing = copy.deepcopy(results)
        del missing[next(iter(missing))]
        failed = copy.deepcopy(results)
        failed[next(iter(failed))].insert(0, {'status': 'FAILURE'})
        for report in ({}, {'per_iteration_data': []},
                       {'per_iteration_data': [missing]}, {'per_iteration_data': [failed]}):
            with self.subTest(report=report), self.assertRaises(ValueError):
                candidate.validate_native_passkey_tests(report)


class CacheAuditTest(unittest.TestCase):
    def test_log_counts_unique_outputs_without_calling_compiler(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.ninja_log'
            content = b'# ninja log v6\n0\t10\t100\tobj/a.obj\tabc\n11\t20\t200\tobj/a.obj\tdef\n21\t30\t300\tobj/b.obj\t123\n'
            path.write_bytes(content)
            summary = audit.summarize_log(path)
            self.assertEqual(summary['cached_log_outputs'], 2)
            self.assertEqual(summary['log_sha256'], hashlib.sha256(content).hexdigest())
            self.assertEqual(summary['unparsed_log_entries'], 0)

    def test_dry_run_distinguishes_real_work_from_regeneration_only(self):
        self.assertEqual(audit.pending_count('[0/1] Regenerating ninja files\n[29/26521] CXX obj/a.obj\n'), 26521)
        self.assertIsNone(audit.pending_count('[0/1] Regenerating ninja files\n'))
        self.assertEqual(audit.pending_count('ninja: no work to do.\n'), 0)
        with self.assertRaises(ValueError):
            audit.pending_count('unexpected output')

    def test_regeneration_only_does_not_claim_one_remaining_compile_or_block_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.ninja_log').write_text('# ninja log v6\n0\t1\t2\tobj/a.obj\tabc\n')
            report = root / 'evidence/cache.json'
            command = ['audit', '--ninja', str(root / 'ninja.exe'), '--out', str(root), '--report', str(report)]
            with mock.patch.object(sys, 'argv', command), redirect_stdout(io.StringIO()), \
                    mock.patch.object(audit.subprocess, 'run', return_value=SimpleNamespace(returncode=0,
                        stdout='[0/1] Regenerating ninja files\n', stderr='')) as process:
                self.assertEqual(audit.main(), 0)
            self.assertIn('-n', process.call_args.args[0])
            value = json.loads(report.read_text())
            self.assertEqual(value['status'], 'manifest_regeneration_required')
            self.assertIsNone(value['pending_actions'])


class WorkflowRoutingTest(unittest.TestCase):
    def test_checkpoint_survives_build_or_upload_failure(self):
        workflow = yaml.load((REPO / '.github/workflows/google-services-win155-worker.yml').read_text(), Loader=yaml.BaseLoader)
        steps = workflow['jobs']['build']['steps']
        by_id = {step['id']: step for step in steps if 'id' in step}
        candidate = by_id['candidate']
        self.assertEqual(candidate['uses'], 'actions/upload-artifact@v4')
        self.assertEqual(candidate['with']['if-no-files-found'], 'error')
        self.assertLess(steps.index(candidate), steps.index(by_id['checkpoint']))
        condition = by_id['checkpoint']['if'].removeprefix('${{').removesuffix('}}').strip()
        # Evaluate the actual workflow guard across upload failures and skips.
        # A finished compilation alone must never discard its only new checkpoint.
        cases = (
            ('candidate delivered', False, 'true', 'success', False),
            ('compilation unfinished or failed', False, 'true', 'skipped', True),
            ('candidate upload failed', False, 'true', 'failure', True),
            ('candidate upload not attempted', False, 'true', '', True),
            ('source preparation incomplete', False, 'false', 'skipped', False),
            ('run cancelled', True, 'true', 'skipped', False),
        )
        for reason, cancelled, prepared, upload, expected in cases:
            with self.subTest(reason=reason):
                expression = (condition.replace('!cancelled()', repr(not cancelled))
                              .replace('steps.restore.outputs.checkpoint_ready', repr(prepared))
                              .replace('steps.candidate.outcome', repr(upload))
                              .replace('&&', 'and'))
                self.assertEqual(eval(expression, {'__builtins__': {}}, {}), expected)

    def test_push_only_verifies_and_never_cancels_active_run(self):
        workflow = yaml.load((REPO / '.github/workflows/build-google-services-win155.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow['concurrency']['cancel-in-progress'], 'false')
        self.assertEqual(workflow['concurrency']['group'], 'google-services-win155-${{ github.ref }}')
        self.assertEqual(workflow['on']['workflow_dispatch']['inputs']['operation']['default'], 'verify')
        for name in ('build-1', 'build-2'):
            self.assertIn("github.event_name == 'workflow_dispatch'", workflow['jobs'][name]['if'])
            self.assertIn("inputs.operation == 'resume'", workflow['jobs'][name]['if'])
        verify = json.dumps(workflow['jobs']['verify-candidate'])
        self.assertNotIn('build-google-services.ps1', verify)
        self.assertNotIn('ensure-windows-sdk', verify)
        self.assertNotIn('google-win155-tree', verify)
        self.assertIn('verify_google_services_candidate.py', verify)

    def test_resume_cannot_silently_fall_back_to_upstream(self):
        script = (REPO / 'build/windows/build-google-services.ps1').read_text()
        self.assertIn("if (-not $ResumeRun) { throw", script)
        self.assertIn('no complete checkpoint manifest', script)
        self.assertNotIn('38387174079', script)
        self.assertNotIn("$sourceRepo = 'xiaozhou26/Chromix'", script)


class BuildBudgetTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('pwsh') or shutil.which('powershell'), 'PowerShell is required')
    def test_exhausted_budget_returns_normally_before_and_after_preparation(self):
        # Execute just the real parameter/budget/guard AST nodes. Never source the
        # build script, install tools, restore a checkpoint or launch a compiler.
        command = r'''
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:GOOGLE_BUILD_SCRIPT, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Invalid build script' }
$budgets = @($ast.FindAll({ param($node)
  $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
  $node.Right.Extent.Text -match 'Math.*Floor\(355 - \$elapsed'
}, $true))
if ($budgets.Count -ne 2) { throw 'Expected both budget checkpoints' }
$guards = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.IfStatementAst] }, $true))
foreach ($budget in $budgets) {
  $guard = $guards | Where-Object { $_.Extent.StartOffset -gt $budget.Extent.EndOffset } | Sort-Object { $_.Extent.StartOffset } | Select-Object -First 1
  $block = [scriptblock]::Create($ast.ParamBlock.Extent.Text + "`n" + $budget.Extent.Text + "`n" + $guard.Extent.Text + "`n'compile-ready'")
  foreach ($elapsed in @(10, 302, 400)) {
    $result = @(& $block -Phase Build -CompileMinutes 300 -WarningAction SilentlyContinue)
    if ($elapsed -eq 10) {
      if ($result.Count -ne 1 -or $result[0] -ne 'compile-ready') { throw 'Valid compile budget was rejected' }
    } elseif ($result.Count -ne 0) { throw 'Exhausted budget reached compilation' }
  }
}
'''
        result = subprocess.run([shutil.which('pwsh') or shutil.which('powershell'),
                                 '-NoProfile', '-NonInteractive', '-Command', command],
                                env={**os.environ, 'GOOGLE_BUILD_SCRIPT': str(REPO / 'build/windows/build-google-services.ps1')},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
