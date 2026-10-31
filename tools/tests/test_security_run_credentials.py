"""The acquisition allowlist cannot forward credentials to arbitrary commands."""
from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import security_run as runner

ROOT = Path(__file__).resolve().parents[2]


def acquisition(script, *args):
    return [sys.executable, '-B', '-X', 'utf8', str(ROOT / 'tools' / script), *args]


class ReadTokenAllowlistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='source-acquisition-token-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cli(self, command, *, token=True):
        report = self.root / 'result.json'
        args = ['security_run.py', '--cwd', str(self.root), '--timeout', '5',
                '--log', str(self.root / 'owned.log'), '--report', str(report)]
        if token:
            args.append('--github-read-token')
        args += ['--', *command]
        outcome = {'timed_out': False, 'exit_code': 0, 'cleanup_errors': [], 'seconds': 0.1}
        with patch.object(sys, 'argv', args), patch.object(runner, 'run', return_value=outcome) as run, \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
            runner.main()
        return result.exception.code, run, report

    def test_real_cli_accepts_only_supported_acquisition_helpers(self):
        commands = [acquisition('fetch_upstream_cache.py', '--platform', 'windows'),
                    acquisition('security_checkpoint.py', 'preflight', '--run', '123'),
                    acquisition('security_checkpoint.py', 'restore', '--run', '123')]
        for command in commands:
            with self.subTest(command=command):
                code, run, report = self.cli(command)
                self.assertEqual(code, 0)
                self.assertEqual(run.call_args.args[0], command)
                self.assertTrue(run.call_args.args[-1])
                self.assertEqual(json.loads(report.read_text())['exit_code'], 0)

    def test_cli_rejects_other_checkpoint_operations_and_external_same_names(self):
        commands = [acquisition('security_checkpoint.py', operation)
                    for operation in ('create', 'verify-tree', 'verify-archive', 'dispatch', '--help')]
        commands += [acquisition('security_checkpoint.py')]
        for name in ('security_checkpoint.py', 'fetch_upstream_cache.py'):
            external = self.root / name
            external.write_text('# synthetic external script; never executed\n')
            commands.append([sys.executable, '-B', '-X', 'utf8', str(external), 'restore'])
        for command in commands:
            with self.subTest(command=command):
                code, run, _ = self.cli(command)
                self.assertEqual(code, 2)
                run.assert_not_called()

    def test_a_permitted_script_argument_does_not_authorize_an_arbitrary_interpreter(self):
        allowed = acquisition('security_checkpoint.py', 'restore')
        commands = [['chrome.exe', *allowed[1:]], ['ninja.exe', *allowed[1:]],
                    [sys.executable, '-c', 'raise SystemExit(0)', 'utf8', *allowed[4:]],
                    [sys.executable, '-B', '-X', 'utf8', '-c', 'print(1)']]
        for command in commands:
            with self.subTest(command=command):
                code, run, _ = self.cli(command)
                self.assertEqual(code, 2)
                run.assert_not_called()

    def test_normal_compilation_without_token_forwarding_is_unchanged(self):
        code, run, _ = self.cli(['ninja.exe', '-C', 'out/Default', 'chrome'], token=False)
        self.assertEqual(code, 0)
        self.assertFalse(run.call_args.args[-1])


@unittest.skipUnless(os.name == 'nt', 'The owned-command runner targets Windows')
class ReadTokenEnvironmentTests(unittest.TestCase):
    class FakeJob:
        def assign(self, process):
            pass

        def terminate(self):
            pass

        def close(self):
            pass

    class FakeProcess:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    def exercise(self, command, token):
        with tempfile.TemporaryDirectory(prefix='owned-command-env-') as temporary:
            root = Path(temporary)
            environment = {'PATH': 'synthetic-path', 'GH_TOKEN': 'synthetic-read-token',
                           'GITHUB_TOKEN': 'synthetic-other-token', 'API_KEY': 'synthetic-api-key',
                           'SOME_SECRET': 'synthetic-secret', 'PASSWORD': 'synthetic-password'}
            with patch.dict(os.environ, environment, clear=True), \
                    patch.object(runner, 'WindowsJob', self.FakeJob), \
                    patch.object(runner.subprocess, 'Popen', return_value=self.FakeProcess()) as process:
                result = runner.run(command, root, 5, root / 'command.log', token)
                self.assertEqual(result['exit_code'], 0)
                return process.call_args.kwargs['env']

    def test_only_gh_read_token_reaches_the_three_allowed_acquisition_forms(self):
        for command in [acquisition('fetch_upstream_cache.py', '--platform', 'windows'),
                        acquisition('security_checkpoint.py', 'preflight', '--run', '123'),
                        acquisition('security_checkpoint.py', 'restore', '--run', '123')]:
            with self.subTest(command=command):
                self.assertEqual(self.exercise(command, True),
                                 {'PATH': 'synthetic-path', 'GH_TOKEN': 'synthetic-read-token'})

    def test_compiler_and_browser_receive_no_sensitive_environment(self):
        for command in [['ninja.exe', 'chrome'], ['chrome.exe', 'about:blank']]:
            self.assertEqual(self.exercise(command, False), {'PATH': 'synthetic-path'})

    def test_direct_runner_api_cannot_bypass_the_credential_allowlist(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(runner.subprocess, 'Popen') as process, \
                self.assertRaisesRegex(ValueError, 'restricted'):
            runner.run(['chrome.exe'], temporary, 5, Path(temporary) / 'never.log', True)
        process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
