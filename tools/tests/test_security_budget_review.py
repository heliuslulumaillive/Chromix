"""A caught budget timeout is not a compiler failure or an unrestricted retry."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import hashlib

from tools.security_budget_review import assess, verify, FAILED_STEP

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which('pwsh') or shutil.which('pwsh.exe')


class BudgetEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {'run_id': '123', 'run_attempt': '1', 'source_commit': 'a' * 40,
                         'stage': 1, 'resume_safe': True, 'chromium_version': '156.0.8078.79',
                         'core_commit': 'b' * 40, 'windows_commit': 'c' * 40,
                         'recipe_sha256': 'd' * 64, 'source_receipt_sha256': 'e' * 64,
                         'gn_args_sha256': 'f' * 64}
        self.job = {'status': 'completed', 'conclusion': 'failure', 'run_id': 123,
                    'run_attempt': 1, 'head_sha': 'a' * 40,
                    'steps': [{'name': FAILED_STEP, 'conclusion': 'failure'}]}
        self.state = {'stage': 1, 'status': 'budget-stop', 'error': 'SECURITY_BUDGET_STOP',
                      'resume_safe': True, 'finished': False}
        self.command = {'timed_out': True, 'exit_code': None, 'cleanup_errors': [], 'seconds': 100}
        self.build = {k: self.manifest[k] for k in ('run_id', 'source_commit', 'chromium_version',
                     'core_commit', 'windows_commit', 'recipe_sha256', 'source_receipt_sha256')}
        self.build['gn'] = {'sha256': 'f' * 64}
        self.log = '[20/100] CXX file.obj\n[21/100] CXX next.obj\n'

    def files(self):
        return {'stage-state.json': json.dumps(self.state).encode(),
                '014-ninja.json': json.dumps(self.command).encode(),
                '014-ninja.log': self.log.encode(), 'build-inputs.json': json.dumps(self.build).encode()}

    def test_reviewed_timeout_with_matching_complete_checkpoint_is_not_acceptance(self):
        result = assess(self.job, self.manifest, self.files())
        self.assertEqual(result['completed_actions'], 21)
        self.assertFalse(result['native_build_accepted'])

    def test_other_failure_steps_and_attempts_are_not_eligible(self):
        for update in ({'run_attempt': 2}, {'conclusion': 'cancelled'},
                       {'steps': [{'name': 'Upload checkpoint', 'conclusion': 'failure'}]}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                assess({**self.job, **update}, self.manifest, self.files())

    def test_unsafe_or_non_budget_state_cannot_resume(self):
        for key, value in [('status', 'failed'), ('resume_safe', False), ('finished', True), ('stage', 2)]:
            before = copy.deepcopy(self.state)
            self.state[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                assess(self.job, self.manifest, self.files())
            self.state = before

    def test_failed_cleanup_or_real_compiler_exit_cannot_resume(self):
        for update in ({'timed_out': False, 'exit_code': 1}, {'cleanup_errors': ['remaining process']},
                       {'seconds': 0}, {'seconds': 19000}):
            before = copy.deepcopy(self.command)
            self.command.update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                assess(self.job, self.manifest, self.files())
            self.command = before

    def test_build_inputs_must_match_the_actual_checkpoint(self):
        for key in ('source_commit', 'run_id', 'recipe_sha256', 'source_receipt_sha256'):
            before = self.build[key]
            self.build[key] = 'different'
            with self.subTest(key=key), self.assertRaises(ValueError):
                assess(self.job, self.manifest, self.files())
            self.build[key] = before
        self.build['gn']['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            assess(self.job, self.manifest, self.files())

    def test_progress_does_not_hide_compile_errors(self):
        for error in ('FAILED: file.obj', 'file.cc: error: unknown type', 'fatal error C1083',
                      'fatal error LNK1000', 'ninja: build stopped: subcommand failed.'):
            self.log = error + '\n[21/100] CXX next.obj\n'
            with self.subTest(error=error), self.assertRaises(ValueError):
                assess(self.job, self.manifest, self.files())

    def test_reviewed_artifact_and_each_payload_hash_are_bound(self):
        from tools import security_budget_review as module
        artifact = {'id': 7, 'name': 'security-win156-evidence-s1', 'expired': False,
                    'size_in_bytes': 500, 'digest': 'sha256:' + 'a' * 64, 'created_at': '2026-10-31T11:38:09Z'}
        identity = module.checkpoint.artifact_identity(artifact)
        data = self.files()
        review = {'version': 1, 'artifact': identity,
                  'files_sha256': {name: hashlib.sha256(value).hexdigest() for name, value in data.items()}}
        def download(args):
            root = Path(args[args.index('--dir') + 1])
            for name, value in data.items():
                (root / name).write_bytes(value)
            return ''
        with patch.object(module.checkpoint, 'artifact_inventory', return_value=[artifact]), \
                patch.object(module.checkpoint, 'gh', side_effect=download):
            result = verify('owner/repo', '123', self.job, {'stage': 1, 'manifest': self.manifest}, review)
            self.assertEqual(result['evidence_artifact_id'], 7)
            data['014-ninja.log'] += b'changed bytes'
            with self.assertRaisesRegex(ValueError, 'content changed'):
                verify('owner/repo', '123', self.job, {'stage': 1, 'manifest': self.manifest}, review)
        with patch.object(module.checkpoint, 'artifact_inventory', return_value=[{**artifact, 'id': 8}]), \
                patch.object(module.checkpoint, 'gh') as gh:
            with self.assertRaisesRegex(ValueError, 'changed or expired'):
                verify('owner/repo', '123', self.job, {'stage': 1, 'manifest': self.manifest}, review)
            gh.assert_not_called()


@unittest.skipUnless(PWSH, 'PowerShell is required for the actual Actions wrapper regression')
class PowerShellExitTests(unittest.TestCase):
    def exercise(self, *, budget=True, remove_fix=False):
        # Parse only the real final catch body. The hosted build entry point is
        # never invoked and its filesystem/state functions are temporary stubs.
        harness = r'''
$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$false
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:STAGE_SOURCE,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'source parse failed'}
$outer=@($ast.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.TryStatementAst]})[-1]
$body=$outer.CatchClauses[0].Body.Extent.Text
if($env:REMOVE_FIX -eq 'true'){$body=$body.Replace('$global:LASTEXITCODE = 0','')}
function Save-State {}
function Json {}
function Output {}
$evidence=$env:TEMP
$state=@{phase='Compile';resume_safe=$true}
$global:LASTEXITCODE=124
$code='try { throw '+"'"+$env:THROWN_MESSAGE+"'"+' } catch '+$body
. ([scriptblock]::Create($code))
# Match Actions' treatment of a native command's surviving exit status.
if(Test-Path -LiteralPath variable:LASTEXITCODE){exit $LASTEXITCODE}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'actions-wrapper.ps1'
            path.write_text(harness, encoding='utf-8')
            env = {**os.environ, 'STAGE_SOURCE': str(ROOT / 'build/windows/security-stage.ps1'),
                   'REMOVE_FIX': 'true' if remove_fix else 'false',
                   'THROWN_MESSAGE': 'SECURITY_BUDGET_STOP' if budget else 'real compiler failure'}
            return subprocess.run([PWSH, '-NoProfile', '-Command', ". '" + str(path).replace("'", "''") + "'"],
                                  capture_output=True, text=True, encoding='utf-8', env=env, timeout=30)

    def test_actual_catch_clears_only_a_handled_budget_exit(self):
        fixed = self.exercise()
        self.assertEqual(fixed.returncode, 0, fixed.stdout + fixed.stderr)
        self.assertIn('Stage budget reached', fixed.stdout)
        self.assertNotEqual(self.exercise(remove_fix=True).returncode, 0)
        self.assertNotEqual(self.exercise(budget=False).returncode, 0)


if __name__ == '__main__':
    unittest.main()
