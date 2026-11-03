"""Gate/launcher fixtures only. Synthetic exe results are never C++ acceptance."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import native_compiled_evidence as gate
from tools import write_google_services_build_record as writer

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data), encoding='utf-8')


def build_fixture(executable=None):
    return {'schema_version': 1, 'build_variant': gate.VARIANT, 'chromium_version': gate.VERSION,
        'source_commit': 'a' * 40, 'run_id': 42, 'preparation_sha256': 'b' * 64, 'gn_args_sha256': 'c' * 64,
        'created_at_utc': (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
        'native_test_executable': {'name': gate.TEST_EXECUTABLE,
            'sha256': digest(executable) if executable else 'd' * 64,
            'bytes': executable.stat().st_size if executable else 1234}}


def suite_fixture(root, kind):
    summary, launcher, build = (root / name for name in ('summary.json', 'launcher.json', 'google-services-build.json'))
    write(build, build_fixture())
    write(summary, {'per_iteration_data': [{name: [{'status': 'SUCCESS'}] for name in gate.REQUIRED_TESTS[kind]}]})
    source = json.loads(build.read_text())
    receipt = {'schema_version': 1, 'status': 'passed', 'test_filter': gate.TEST_FILTERS[kind],
        'executable': 'C:/synthetic-only/' + gate.TEST_EXECUTABLE,
        'executable_sha256': source['native_test_executable']['sha256'], 'executable_bytes': source['native_test_executable']['bytes'],
        'build_record_sha256': digest(build), 'summary_sha256': digest(summary),
        'native_exit_code': 0, 'host_process_id': 1234, 'startup_assertions_unchanged': True,
        'test_host_priority': 'Normal', 'original_host_priority': 'BelowNormal', 'restored_host_priority': 'BelowNormal',
        'started_at_utc': datetime.now(timezone.utc).isoformat(), 'completed_at_utc': datetime.now(timezone.utc).isoformat()}
    receipt.update({key: source[key] for key in ('source_commit', 'run_id', 'preparation_sha256', 'gn_args_sha256')})
    write(launcher, receipt)
    return summary, launcher, build


class ExactCompiledGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='compiled-gate-unit-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_exact_frozen_67_9_10_sets_pass(self):
        self.assertEqual({key: len(value) for key, value in gate.REQUIRED_TESTS.items()}, {'passkey': 67, 'browser': 9, 'cookie': 10})
        for kind in gate.REQUIRED_TESTS:
            with self.subTest(kind=kind):
                paths = suite_fixture(self.root, kind)
                result = gate.validate_compiled_suite(*paths, kind)
                self.assertEqual(set(result['tests']), gate.REQUIRED_TESTS[kind])

    def test_cli_uses_same_gate_and_prints_only_bounded_result(self):
        paths = suite_fixture(self.root, 'passkey')
        command = [sys.executable, '-B', str(ROOT / 'tools/native_compiled_evidence.py'), '--summary', str(paths[0]),
                   '--launcher', str(paths[1]), '--build-record', str(paths[2]), '--kind', 'passkey']
        passed = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(json.loads(passed.stdout), {'status': 'passed', 'kind': 'passkey', 'required_count': 67})
        receipt = json.loads(paths[1].read_text()); receipt['source_commit'] = 'e' * 40; write(paths[1], receipt)
        refused = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(refused.stdout, '')

    def test_standalone_new_suite_verifiers_reject_extra_success(self):
        from tools import verify_native_browser_tests
        for verifier in (verify_native_browser_tests,):
            tests = {name: [{'status': 'SUCCESS'}] for name in verifier.REQUIRED}
            verifier.verify({'per_iteration_data': [tests]})
            tests['ForeignSuite.ExtraSuccess'] = [{'status': 'SUCCESS'}]
            with self.assertRaises(ValueError): verifier.verify({'per_iteration_data': [tests]})

    def test_missing_extra_skipped_retried_and_multiple_iterations_rejected(self):
        for kind in gate.REQUIRED_TESTS:
            for fault in ('missing', 'extra', 'skip', 'retry', 'iteration', 'eight_only'):
                if fault == 'eight_only' and kind != 'passkey': continue
                summary, launcher, build = suite_fixture(self.root, kind)
                data = json.loads(summary.read_text()); tests = data['per_iteration_data'][0]; name = next(iter(tests))
                if fault == 'missing': del tests[name]
                elif fault == 'extra': tests['ForeignSuite.CannotMatchFilter'] = [{'status': 'SUCCESS'}]
                elif fault == 'skip': tests[name][0]['status'] = 'SKIPPED'
                elif fault == 'retry': tests[name].append({'status': 'SUCCESS'})
                elif fault == 'iteration': data['per_iteration_data'].append(copy.deepcopy(tests))
                else: data['per_iteration_data'][0] = dict(list(tests.items())[:8])
                write(summary, data)
                receipt = json.loads(launcher.read_text()); receipt['summary_sha256'] = digest(summary); write(launcher, receipt)
                with self.subTest(kind=kind, fault=fault), self.assertRaises(ValueError):
                    gate.validate_compiled_suite(summary, launcher, build, kind)

    def test_old_or_unbound_launcher_and_wrong_byte_identity_rejected(self):
        changes = {'executable_sha256': 'e' * 64, 'executable_bytes': 1235, 'build_record_sha256': 'f' * 64,
            'source_commit': 'e' * 40, 'run_id': True, 'preparation_sha256': 'e' * 64, 'gn_args_sha256': 'f' * 64,
            'native_exit_code': False, 'host_process_id': True, 'summary_sha256': 'a' * 64,
            'test_filter': '*', 'startup_assertions_unchanged': 1, 'restored_host_priority': 'Normal',
            'error': 'synthetic failure'}
        for key, value in changes.items():
            paths = suite_fixture(self.root, 'browser'); receipt = json.loads(paths[1].read_text()); receipt[key] = value; write(paths[1], receipt)
            with self.subTest(key=key), self.assertRaises(ValueError): gate.validate_compiled_suite(*paths, 'browser')
        paths = suite_fixture(self.root, 'browser'); receipt = json.loads(paths[1].read_text())
        for key in ('build_record_sha256', 'started_at_utc', 'completed_at_utc'):
            changed = dict(receipt); del changed[key]; write(paths[1], changed)
            with self.subTest(missing=key), self.assertRaises(ValueError): gate.validate_compiled_suite(*paths, 'browser')

    def test_timestamp_requires_explicit_utc_and_ordered_pretest_execution(self):
        for key, value in [('started_at_utc', '2000-01-01T00:00:00Z'), ('completed_at_utc', '2000-01-01T00:00:00Z'),
                           ('started_at_utc', '2090-01-01T00:00:00Z'), ('started_at_utc', '2026-11-02T00:00:00'),
                           ('started_at_utc', '2026-11-02T00:00:00+01:00')]:
            paths = suite_fixture(self.root, 'browser'); receipt = json.loads(paths[1].read_text()); receipt[key] = value; write(paths[1], receipt)
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): gate.validate_compiled_suite(*paths, 'browser')

    def test_duplicate_json_property_and_mid_read_mutation_are_rejected(self):
        paths = suite_fixture(self.root, 'browser')
        raw = paths[0].read_text().replace('"status": "SUCCESS"', '"status":"FAILURE","status":"SUCCESS"', 1)
        paths[0].write_text(raw); receipt = json.loads(paths[1].read_text()); receipt['summary_sha256'] = digest(paths[0]); write(paths[1], receipt)
        with self.assertRaisesRegex(ValueError, 'Duplicate'): gate.validate_compiled_suite(*paths, 'browser')
        paths = suite_fixture(self.root, 'browser'); original = gate._read
        def change_after_read(path):
            value = original(path)
            if path == paths[2]: paths[0].write_bytes(paths[0].read_bytes() + b' ')
            return value
        with mock.patch.object(gate, '_read', side_effect=change_after_read), self.assertRaisesRegex(ValueError, 'changed'):
            gate.validate_compiled_suite(*paths, 'browser')


class WriterNativeBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-build-record-unit-'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_selected_out_executable_required_and_bound_exactly(self):
        selected = self.root / 'Selected'; selected.mkdir(); args = selected / 'args.gn'; args.write_text('is_debug=false')
        wrong = self.root / 'Default'; wrong.mkdir(); (wrong / gate.TEST_EXECUTABLE).write_bytes(b'wrong output')
        with self.assertRaises(ValueError): writer.native_test_identity(args)
        executable = selected / gate.TEST_EXECUTABLE; executable.write_bytes(b'Synthetic data; never execute')
        self.assertEqual(writer.native_test_identity(args), {'name': gate.TEST_EXECUTABLE, 'sha256': digest(executable), 'bytes': executable.stat().st_size})
        executable.write_bytes(b'')
        with self.assertRaises(ValueError): writer.native_test_identity(args)

    def test_writer_cli_requires_native_executable_only_for_156(self):
        work, bundle = self.root / 'work', self.root / 'bundle'; work.mkdir(); bundle.mkdir()
        (bundle / 'chrome.exe').write_bytes(b'Synthetic packaging bytes, not executable')
        args = self.root / 'args.gn'; args.write_text('is_debug=false')
        env = {**os.environ, 'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '42', 'GITHUB_REPOSITORY': 'fixture/only'}
        command = [sys.executable, '-B', str(ROOT / 'tools/write_google_services_build_record.py'), '--bundle', str(bundle), '--work', str(work), '--gn-args', str(args)]
        source = {'variant': 'google-services-self-use', 'chromium_version': '155.0.8059.39', 'patch_sha256': 'b' * 64, 'preparation_sha256': 'c' * 64}
        write(work / 'google-services-source.json', source)
        legacy = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertNotIn('native_test_executable', json.loads((bundle / 'google-services-build.json').read_text()))
        old = (bundle / 'google-services-build.json').read_bytes()
        source.update(build_variant=gate.VARIANT, chromium_version=gate.VERSION); write(work / 'google-services-source.json', source)
        missing = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual((bundle / 'google-services-build.json').read_bytes(), old)
        executable = self.root / gate.TEST_EXECUTABLE; executable.write_bytes(b'Synthetic native test bytes; never execute')
        accepted = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        record = json.loads((bundle / 'google-services-build.json').read_text())
        self.assertEqual(record['native_test_executable']['sha256'], digest(executable))
        self.assertEqual(record['files'], {'chrome.exe': digest(bundle / 'chrome.exe')})
        gate._utc(record['created_at_utc'])


@unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), 'Windows PowerShell 7 launcher fixture')
class WindowsNativeLauncherFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='native-launcher-code-fixture-'); cls.root = Path(cls.temp.name)
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.is_file(): cls.temp.cleanup(); raise unittest.SkipTest('Windows C# fixture compiler unavailable')
        source = cls.root / 'SyntheticLauncherFixture.cs'
        source.write_text('''using System; using System.IO;
class SyntheticLauncherFixture {
 static int Main(string[] args) {
  File.WriteAllText(Environment.GetEnvironmentVariable("NATIVE_FIXTURE_MARKER"), "synthetic fixture executed; not Chromium tests");
  foreach (string arg in args) if(arg.StartsWith("--test-launcher-summary-output=") && Environment.GetEnvironmentVariable("NATIVE_FIXTURE_NO_SUMMARY")!="1")
   File.WriteAllText(arg.Substring("--test-launcher-summary-output=".Length), Environment.GetEnvironmentVariable("NATIVE_FIXTURE_SUMMARY"));
  string mutate=Environment.GetEnvironmentVariable("NATIVE_FIXTURE_MUTATE_RECORD"); if(!String.IsNullOrEmpty(mutate))File.AppendAllText(mutate," ");
  return Environment.GetEnvironmentVariable("NATIVE_FIXTURE_FAILURE")=="1" ? 7 : 0;
 }
}''', encoding='utf-8')
        cls.executable = cls.root / gate.TEST_EXECUTABLE
        result = subprocess.run([str(compiler), '/nologo', '/target:exe', '/out:' + str(cls.executable), str(source)], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
        if result.returncode: cls.temp.cleanup(); raise RuntimeError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(prefix='native-launcher-evidence-fixture-'); self.addCleanup(self.case.cleanup)
        self.directory = Path(self.case.name)
        self.build = self.directory / 'google-services-build.json'; write(self.build, build_fixture(self.executable))
        self.summary = self.directory / 'summary.json'; self.receipt = self.directory / 'launcher.json'; self.marker = self.directory / 'executed.txt'
        self.env = {**os.environ, 'NATIVE_FIXTURE_MARKER': str(self.marker), 'NATIVE_FIXTURE_SUMMARY': json.dumps({'per_iteration_data': [{name: [{'status': 'SUCCESS'}] for name in gate.REQUIRED_TESTS['browser']}]})}

    def run_fixture(self, bound=True):
        command = [shutil.which('pwsh'), '-NoProfile', '-File', str(ROOT / 'build/windows/run-native-passkey-tests.ps1'),
            '-Executable', str(self.executable), '-Summary', str(self.summary), '-Receipt', str(self.receipt),
            '-Log', str(self.directory / 'launcher.log'), '-WorkingDirectory', str(self.directory), '-TestFilter', gate.TEST_FILTERS['browser']]
        if bound: command += ['-BuildRecord', str(self.build)]
        # PowerShell may emit localized error decorations using the console's
        # codepage. Receipt JSON is UTF-8; diagnostic bytes are never parsed.
        return subprocess.run(command, env=self.env, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)

    def test_bound_wrapper_executes_fresh_fixture_and_exact_gate_accepts(self):
        result = self.run_fixture(); self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.is_file())
        gate.validate_compiled_suite(self.summary, self.receipt, self.build, 'browser')

    def test_wrong_build_binary_fails_before_child_execution(self):
        record = json.loads(self.build.read_text()); record['native_test_executable']['sha256'] = 'f' * 64; write(self.build, record)
        result = self.run_fixture(); self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertEqual(json.loads(self.receipt.read_text(encoding='utf-8-sig'))['status'], 'failed')

    def test_stale_summary_nonzero_exit_and_changed_pretest_record_cannot_pass(self):
        for kind, variable in [('missing-summary', 'NATIVE_FIXTURE_NO_SUMMARY'), ('nonzero', 'NATIVE_FIXTURE_FAILURE'), ('changed-record', 'NATIVE_FIXTURE_MUTATE_RECORD')]:
            write(self.build, build_fixture(self.executable)); self.summary.write_text('{"stale":"prior execution"}')
            self.env[variable] = str(self.build) if kind == 'changed-record' else '1'
            result = self.run_fixture(); self.env.pop(variable)
            self.assertNotEqual(result.returncode, 0, kind)
            self.assertEqual(json.loads(self.receipt.read_text(encoding='utf-8-sig'))['status'], 'failed', kind)

    def test_unbound_wrapper_preserves_old_lane_execution_without_156_acceptance(self):
        result = self.run_fixture(bound=False); self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(self.receipt.read_text(encoding='utf-8-sig'))
        self.assertNotIn('build_record_sha256', receipt)
        with self.assertRaises(ValueError): gate.validate_compiled_suite(self.summary, self.receipt, self.build, 'browser')


if __name__ == '__main__': unittest.main()
