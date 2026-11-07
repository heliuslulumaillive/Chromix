"""Acceptance failures must never turn the shared session suite into a skip."""
import copy
import ast
import contextlib
import fnmatch
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import verify_native_session_runtime as session


def good_execution():
    private = 'ShardXSessionAcceptance-' + 'a'*32
    return {'exit_code': 0, 'timed_out': False, 'processes_before_cleanup': 0,
            'cleanup_errors': [], 'switch_desktop_called': False,
            'private_desktop': private, 'private_desktop_observed': private,
            'startup_desktop': 'WinSta0\\' + private,
            'session_id_before': 1, 'session_id_after': 1,
            'window_station_before': 'WinSta0', 'window_station_after': 'WinSta0',
            'window_station_visible_before': True, 'window_station_visible_after': True,
            'input_desktop_check': 'observed', 'input_desktop_before': 'Default',
            'input_desktop_after': 'Default', 'input_desktop_unchanged': True}


class SessionAcceptanceTests(unittest.TestCase):
    def runtime_fixture(self, root):
        bundle = root / 'bundle'; bundle.mkdir()
        (bundle / 'chrome.exe').write_bytes(b'synthetic payload; never execute')
        (bundle / 'locales').mkdir()
        (bundle / 'locales/en.pak').write_bytes(b'synthetic resource')
        files = session.bundle_inventory(bundle)['files']
        (bundle / 'google-services-build.json').write_text(json.dumps({'files': files}), encoding='utf-8')
        original = session.bundle_inventory(bundle)
        runtime = root / 'runtime'
        isolation = session.prepare_runtime_copy(bundle, runtime, original)
        return bundle, runtime, original, isolation

    def test_runtime_copy_keeps_original_and_payload_immutable_and_retains_only_bounded_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, runtime, original, isolation = self.runtime_fixture(root)
            report = root / 'native-session-runtime.json'
            log = '[1104/103342.802:ERROR:third_party\\crashpad\\crashpad\\client\\crashpad_client_win.cc:829] not connected\n'
            (runtime / 'debug.log').write_text(log, encoding='utf-8')
            session.finish_runtime_copy(bundle, runtime, original, report, isolation)
            self.assertEqual(session.bundle_inventory(bundle), original)
            self.assertFalse((bundle / 'debug.log').exists())
            self.assertEqual(report.with_suffix('.debug.log').read_text(encoding='utf-8'), log)
            self.assertEqual(session.validate_runtime_isolation(report, bundle, isolation)['name'], 'native-session-runtime.debug.log')
            report.with_suffix('.debug.log').write_text(log.replace('not connected', 'different error'), encoding='utf-8')
            with self.assertRaises(ValueError):
                session.validate_runtime_isolation(report, bundle, isolation)

    def test_runtime_copy_rejects_modified_payload_original_and_unexpected_generated_files(self):
        for fault in ('copy_changed', 'copy_missing', 'original_changed', 'original_extra', 'extra_file', 'wrong_debug', 'large_debug'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle, runtime, original, isolation = self.runtime_fixture(root)
                if fault == 'copy_changed': (runtime / 'chrome.exe').write_bytes(b'changed')
                elif fault == 'copy_missing': (runtime / 'chrome.exe').unlink()
                elif fault == 'original_changed': (bundle / 'chrome.exe').write_bytes(b'changed')
                elif fault == 'original_extra': (bundle / 'debug.log').write_bytes(b'original must never be repaired or cleaned')
                elif fault == 'extra_file': (runtime / 'unexpected.log').write_bytes(b'unexpected')
                elif fault == 'wrong_debug': (runtime / 'debug.log').write_bytes(b'unreviewed error')
                elif fault == 'large_debug': (runtime / 'debug.log').write_bytes(b'x' * (session.MAX_DEBUG_LOG_BYTES + 1))
                with self.assertRaises(ValueError):
                    session.finish_runtime_copy(bundle, runtime, original, root / 'native-session-runtime.json', isolation)
                if fault == 'original_extra':
                    self.assertTrue((bundle / 'debug.log').exists())

    def test_existing_original_extra_file_is_rejected_without_removal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, _, _, _ = self.runtime_fixture(root)
            added = bundle / 'debug.log'; added.write_bytes(b'preserve old evidence')
            with self.assertRaises(ValueError):
                session.prepare_runtime_copy(bundle, root / 'another-copy', session.bundle_inventory(bundle))
            self.assertEqual(added.read_bytes(), b'preserve old evidence')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction regression')
    def test_host_go_junction_is_canonicalized_without_relaxing_payload_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / 'real-tool'; real.mkdir()
            # A genuine x64 PE fixture for path validation only; never execute
            # it as Go. The host preflight separately requires the pinned version.
            shutil.copyfile(sys.executable, real / 'go.exe')
            alias = root / 'hosted-tool-cache'
            created = subprocess.run([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c',
                'mklink', '/J', str(alias), str(real)], capture_output=True, text=True, timeout=10)
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            try:
                with self.assertRaises(ValueError):
                    session.security.plain(alias / 'go.exe')
                diagnostic = {}
                resolved = session.resolve_go_tool(str(alias / 'go.exe'), diagnostic)
                self.assertEqual(resolved, (real / 'go.exe').resolve())
                self.assertEqual(diagnostic['go_canonical_path'], str(resolved))
                self.assertTrue(diagnostic['go_alias_resolved'])
                with self.assertRaises(ValueError):
                    session.security.plain(alias / 'go.exe')
            finally:
                # Remove only the junction itself; do not recurse into its target.
                os.rmdir(alias)

    def test_host_tool_rejects_missing_directories_shims_and_non_pe_files(self):
        with mock.patch.object(session.shutil, 'which', return_value=None):
            with self.assertRaises(session.GoToolResolutionError) as error:
                session.resolve_go_tool('missing-go', {})
            self.assertEqual(error.exception.reason, 'go_not_found')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'directory.exe'; directory.mkdir()
            shim = root / 'go.cmd'; shim.write_text('@echo off\n')
            invalid = root / 'go.exe'; invalid.write_bytes(b'not an executable')
            for path, reason in ((directory, 'go_not_native_executable'),
                                 (shim, 'go_not_native_executable'), (invalid, 'go_invalid_pe')):
                with self.subTest(path=path.name), mock.patch.object(session.shutil, 'which', return_value=str(path)):
                    with self.assertRaises(session.GoToolResolutionError) as error:
                        session.resolve_go_tool(str(path), {})
                    self.assertEqual(error.exception.reason, reason)

    def test_tool_failure_diagnostic_is_bounded_and_does_not_echo_error_payloads(self):
        report = {}
        session.record_failure(report, session.GoToolResolutionError('go_not_found'), 'resolve_go_tool')
        self.assertEqual(report['failure_reason'], 'go_not_found')
        self.assertEqual(report['failure_phase'], 'resolve_go_tool')
        session.record_failure(report, ValueError('sensitive arbitrary error payload'), 'candidate_input_identity')
        self.assertEqual(report['failure_reason'], 'operation_failed')
        self.assertNotIn('sensitive', json.dumps(report))

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
        passed = good_execution()
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

    def test_no_input_desktop_requires_session_zero_and_noninteractive_service_station(self):
        service = {**good_execution(), 'session_id_before': 0, 'session_id_after': 0,
            'window_station_before': 'Service-0x0-3e7$', 'window_station_after': 'Service-0x0-3e7$',
            'window_station_visible_before': False, 'window_station_visible_after': False,
            'input_desktop_check': 'service_session0_no_input', 'input_desktop_before': None,
            'input_desktop_after': None, 'input_desktop_unchanged': None,
            'input_desktop_before_error': 5, 'input_desktop_after_error': 5}
        service['startup_desktop'] = service['window_station_before'] + '\\' + service['private_desktop']
        self.assertTrue(session.execution_passed(service))
        for key, value in (('session_id_before', 1), ('window_station_before', 'WinSta0'),
                           ('window_station_visible_before', True), ('input_desktop_after_error', 1),
                           ('input_desktop_before_error', 0), ('private_desktop_observed', 'Default'),
                           ('startup_desktop', 'WinSta0\\Default'), ('error_type', 'OSError')):
            with self.subTest(key=key):
                self.assertFalse(session.execution_passed({**service, key: value}))
        denied = {**good_execution(), 'input_desktop_before': None, 'input_desktop_after': None,
                  'input_desktop_before_error': 5, 'input_desktop_after_error': 5,
                  'input_desktop_unchanged': None}
        for check in ('observed', 'service_session0_no_input'):
            self.assertFalse(session.execution_passed({**denied, 'input_desktop_check': check}))

    def test_build_links_payload_to_source_before_launch(self):
        source = {'source_commit': 'a'*40, 'run_id': '123', 'gn': {'sha256': 'b'*64}}
        receipt = {'patch_sha256': 'c'*64, 'preparation_sha256': 'd'*64}
        binding = {key: str(index)*64 for index, key in enumerate((
            'browser_sha256', 'chrome_dll_sha256', 'capabilities_sha256', 'source_receipt_sha256'), 1)}
        build = {'schema_version': 1, 'build_variant': 'fingerprint-modes-win157',
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
        worker = (root / '.github/workflows/security-win157-worker.yml').read_text(encoding='utf-8')
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
        worker = (root / '.github/workflows/security-win157-worker.yml').read_text(encoding='utf-8')
        upload = worker.split('      - name: Deliver the accepted candidate', 1)[1].split('      - name:', 1)[0]
        patterns = re.findall(r'^\s+(C:/c/dist/[^\s]+)\s*$', upload, re.M)
        missing = [name for name in required
                   if not any(fnmatch.fnmatchcase('C:/c/dist/' + name, pattern) for pattern in patterns)]
        self.assertEqual(missing, [], 'Independent candidate acceptance must receive every mandatory native receipt')
        archive = (root / 'build/windows/security-stage.ps1').read_text(encoding='utf-8').split("    'Archive' {", 1)[1]
        for name in required:
            self.assertIn("'" + name + "'", archive)
        self.assertTrue(any(fnmatch.fnmatchcase('C:/c/dist/native-session-runtime.debug.log', pattern) for pattern in patterns))
        self.assertIn("Test-Path -LiteralPath $sessionDiagnostic -PathType Leaf", archive)

    def test_early_failure_upload_precedes_checkpoint_and_preserves_final_evidence(self):
        root = Path(__file__).resolve().parents[2]
        worker = (root / '.github/workflows/security-win157-worker.yml').read_text(encoding='utf-8')
        early = worker.split('      - name: Upload early failure evidence before checkpoint compression', 1)[1].split('      - name:', 1)[0]
        self.assertIn('!cancelled() && failure()', early)
        self.assertIn('name: security-win157-early-evidence-s${{ inputs.stage }}', early)
        self.assertIn('path: C:/security-evidence', early)
        self.assertLess(worker.index('Upload early failure evidence before checkpoint compression'), worker.index('Preserve one complete resumable checkpoint'))
        self.assertIn('name: security-win157-evidence-s${{ inputs.stage }}', worker)


if __name__ == '__main__':
    unittest.main()
