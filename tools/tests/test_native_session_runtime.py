"""Acceptance failures must never turn the shared session suite into a skip."""
import copy
import ast
import contextlib
import fnmatch
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from tools import verify_native_session_runtime as session


class SessionAcceptanceTests(unittest.TestCase):
    def events(self):
        rows = []
        for name in sorted(session.REQUIRED):
            rows.extend([{'Package': session.PACKAGE, 'Test': name, 'Action': action}
                         for action in ('run', 'pass')])
        return rows + [{'Package': session.PACKAGE, 'Action': 'pass'}]

    def parse(self, rows):
        return session.parse_results('\n'.join(json.dumps(row) for row in rows))

    def test_all_five_must_run_and_pass_once(self):
        self.assertEqual(sorted(session.REQUIRED), self.parse(self.events()))
        for mutation in ('missing', 'skip', 'fail', 'repeated', 'unknown', 'package', 'no_start'):
            rows = self.events()
            if mutation == 'missing':
                rows = rows[2:]
            elif mutation in ('skip', 'fail'):
                rows[1]['Action'] = mutation
            elif mutation == 'repeated':
                rows[2:2] = copy.deepcopy(rows[:2])
            elif mutation == 'unknown':
                rows[0]['Test'] = 'TestUnreviewedScenario'
            elif mutation == 'package':
                rows[-1]['Package'] = 'unrelated/package'
            elif mutation == 'no_start':
                rows.pop(0)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.parse(rows)

    def test_timeout_force_cleanup_and_desktop_switch_cannot_pass(self):
        passed = {'exit_code': 0, 'timed_out': False, 'processes_before_cleanup': 0,
                  'cleanup_errors': [], 'switch_desktop_called': False,
                  'input_desktop_unchanged': True}
        self.assertTrue(session.execution_passed(passed))
        for key, value in [('exit_code', 1), ('timed_out', True),
                           ('processes_before_cleanup', 1), ('cleanup_errors', ['error']),
                           ('switch_desktop_called', True), ('input_desktop_unchanged', False)]:
            with self.subTest(key=key):
                failed = {**passed, key: value}
                self.assertFalse(session.execution_passed(failed))
                missing = passed.copy()
                del missing[key]
                self.assertFalse(session.execution_passed(missing))

    def test_build_links_payload_to_source_before_launch(self):
        source = {'source_commit': 'a'*40, 'run_id': '123', 'gn': {'sha256': 'b'*64}}
        receipt = {'patch_sha256': 'c'*64, 'preparation_sha256': 'd'*64}
        binding = {key: str(index)*64 for index, key in enumerate((
            'browser_sha256', 'chrome_dll_sha256', 'capabilities_sha256', 'source_receipt_sha256'), 1)}
        build = {'schema_version': 1, 'build_variant': 'fingerprint-modes-win156',
            'chromium_version': session.security.VERSION, 'source_commit': source['source_commit'],
            'run_id': 123, 'gn_args_sha256': source['gn']['sha256'], **receipt,
            'files': dict(zip(('chrome.exe', 'chrome.dll', 'native-browser-capabilities.json',
                              'google-services-source.json'), binding.values()))}
        session.validate_build_linkage(build, source, receipt, binding)
        for field in ('schema_version', 'source_commit', 'run_id', 'gn_args_sha256', 'preparation_sha256', 'patch_sha256', 'files'):
            changed = copy.deepcopy(build)
            changed[field] = {} if field == 'files' else (True if field == 'schema_version' else 'f'*64)
            with self.subTest(field=field), self.assertRaises(ValueError):
                session.validate_build_linkage(changed, source, receipt, binding)

    def test_ci_runs_and_archives_session_acceptance(self):
        root = Path(__file__).resolve().parents[2]
        stage = (root / 'build/windows/security-stage.ps1').read_text(encoding='utf-8')
        self.assertIn("label='native-session-runtime'", stage)
        self.assertIn("'tools\\verify_native_session_runtime.py'", stage)
        self.assertIn("'native-session-runtime.json'", stage)
        worker = (root / '.github/workflows/security-win156-worker.yml').read_text(encoding='utf-8')
        self.assertIn('actions/setup-go@', worker)
        self.assertIn('tools.tests.test_native_session_runtime', worker)
        self.assertLess(worker.index('Install hash-locked Python tools'), worker.index('--host-preflight'))
        self.assertLess(worker.index('--host-preflight'), worker.index('Admit the donor and download verified source volumes'))

    def test_host_preflight_cannot_replace_candidate_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = str(Path(temporary) / 'report.json')
            for arguments in ([], ['--browser', 'candidate.exe'],
                              ['--source-record', 'source.json'],
                              ['--host-preflight', '--browser', 'candidate.exe']):
                with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    session.main(['--report', report, *arguments])
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(Path(report).exists())
            with mock.patch.object(session, 'host_preflight', return_value=0) as preflight:
                self.assertEqual(session.main(['--host-preflight', '--report', report]), 0)
                preflight.assert_called_once_with('go', Path(report))

    def test_candidate_upload_contains_every_required_native_receipt(self):
        root = Path(__file__).resolve().parents[2]
        validator = ast.parse((root / 'tools/verify_google_services_candidate.py').read_text(encoding='utf-8'))
        function = next(node for node in validator.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'validate_native_integration')
        assignment = next(node for node in ast.walk(function) if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'names' for target in node.targets))
        required = ast.literal_eval(assignment.value)
        worker = (root / '.github/workflows/security-win156-worker.yml').read_text(encoding='utf-8')
        upload = worker.split('      - name: Deliver the accepted candidate', 1)[1].split('      - name:', 1)[0]
        patterns = re.findall(r'^\s+(C:/c/dist/[^\s]+)\s*$', upload, re.M)
        missing = [name for name in required
                   if not any(fnmatch.fnmatchcase('C:/c/dist/' + name, pattern) for pattern in patterns)]
        self.assertEqual(missing, [], 'Independent candidate acceptance must receive every mandatory native receipt')
        archive = (root / 'build/windows/security-stage.ps1').read_text(encoding='utf-8').split("    'Archive' {", 1)[1]
        for name in required:
            self.assertIn("'" + name + "'", archive)


if __name__ == '__main__':
    unittest.main()
