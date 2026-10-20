"""Metadata timeout recovery must preserve output, objects and owned-tree exit."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from tools import google_services_cache_audit as audit


class MetadataRecoveryTest(unittest.TestCase):
    def fixture(self, previous=None, responses=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        out = root / 'src/out/Default'
        out.mkdir(parents=True)
        (out / '.ninja_log').write_text('# ninja log v6\n0\t1\t2\tobj/cached.obj\tabc\n')
        obj = out / 'obj/cached.obj'
        obj.parent.mkdir()
        obj.write_bytes(b'preserved compiled object fixture')
        before = (obj.read_bytes(), obj.stat().st_mtime_ns)
        (out / 'gn.exe').write_bytes(b'not executed; command is mocked')
        report = root / 'audit.json'
        argv = ['audit', '--ninja', str(root / 'ninja.exe'), '--out', str(out),
                '--report', str(report), '--ensure-manifest',
                '--manifest-timeout-seconds', '1800', '--plan-timeout-seconds', '600',
                '--target', 'chrome', '--target', 'uxr_surface_modes_tests']
        previous_bytes = None
        if previous is not None:
            previous_bytes = json.dumps(previous).encode()
            report.write_bytes(previous_bytes)
            argv += ['--previous-audit', str(report)]
        if responses is None:
            responses = [
                SimpleNamespace(returncode=0, stdout='ninja: no work to do.\n', stderr=''),
                SimpleNamespace(returncode=0, stdout='[1/2] CXX obj/new.obj\n[2/2] LINK chrome.exe\n', stderr='')]
        with mock.patch.object(sys, 'argv', argv), redirect_stdout(io.StringIO()), \
                mock.patch.object(audit, 'run_logged', side_effect=responses) as runner:
            code = audit.main()
        self.assertEqual((obj.read_bytes(), obj.stat().st_mtime_ns), before)
        value = json.loads(report.read_text())
        if previous_bytes is not None:
            saved = Path(value['command_log_directory']) / 'previous-audit.json'
            self.assertEqual(saved.read_bytes(), previous_bytes)
        return code, value, runner.call_args_list

    def test_normal_and_completed_metadata_keep_the_incremental_ninja_path(self):
        for previous in (None, {'status': 'passed', 'manifest_refreshed': True},
                         {'status': 'failed', 'failure_phase': 'plan-1',
                          'manifest_checked': True, 'error': 'unknown target'}):
            with self.subTest(previous=previous):
                code, value, calls = self.fixture(previous)
                self.assertEqual(code, 0)
                self.assertEqual(calls[0].args[0][-1], 'build.ninja')
                self.assertNotIn('manifest_recovery_reason', value)
                self.assertEqual([c.kwargs['timeout'] for c in calls], [1800, 600])

    def test_actual_legacy_timeout_receipt_forces_one_gn_recovery_then_real_plan(self):
        previous = {'schema_version': 1, 'status': 'failed', 'compiler_invoked': False,
                    'error': "Command '['C:\\ninja.exe', '-C', 'C:\\out', 'build.ninja']' timed out after 240 seconds"}
        code, value, calls = self.fixture(previous)
        self.assertEqual(code, 0)
        self.assertEqual(calls[0].args[0][1], 'gen')
        self.assertEqual(calls[0].args[0][-1], '--fail-on-unused-args')
        self.assertIn('-n', calls[1].args[0])
        self.assertEqual(len(calls), 2)
        self.assertTrue(value['manifest_refreshed'])
        self.assertFalse(value['compiler_invoked'])
        self.assertEqual(value['pending_actions'], 2)
        self.assertEqual(value['previous_audit_sha256'], hashlib.sha256(json.dumps(previous).encode()).hexdigest())

    def test_new_failed_metadata_receipt_also_recovers(self):
        code, value, calls = self.fixture({'status': 'failed', 'failure_phase': 'manifest-refresh'})
        self.assertEqual(code, 0)
        self.assertEqual(calls[0].args[0][1], 'gen')
        self.assertEqual(value['manifest_recovery_reason'], 'previous_manifest_command_failed')

    def test_timeout_and_failed_tree_cleanup_cannot_pass_or_silently_plan(self):
        for cleanup, expected_safe in (([], True), (['job still active'], False)):
            with self.subTest(cleanup=cleanup):
                code, value, calls = self.fixture(responses=[SimpleNamespace(
                    returncode=None, stdout='partial GN output', stderr='', timed_out=True,
                    receipt={'timed_out': True, 'exit_code': None, 'cleanup_errors': cleanup})])
                self.assertEqual(code, 1)
                self.assertEqual(value['failure_phase'], 'manifest-refresh')
                self.assertEqual(value['safe_to_snapshot'], expected_safe)
                self.assertEqual(len(calls), 1)
                self.assertNotIn('pending_actions', value)

    def test_real_owned_timeout_keeps_partial_output_and_stops_detached_child(self):
        import psutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child_source = "import time;time.sleep(60)"
            source = """
import subprocess, sys, os, time
from pathlib import Path
options = {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS} if os.name=='nt' else {'start_new_session':True}
child = subprocess.Popen([sys.executable,'-c',sys.argv[1]],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**options)
Path('child.pid').write_text(str(child.pid))
print('partial metadata progress',flush=True)
time.sleep(60)
"""
            result = audit.run_logged(
                [sys.executable, '-X', 'utf8', '-c', source, child_source],
                env=os.environ.copy(), timeout=2, log_path=root / 'metadata.log', cwd=root)
            self.assertTrue(result.timed_out)
            self.assertIn('partial metadata progress', result.stdout)
            self.assertFalse(result.receipt['cleanup_errors'])
            self.assertLess(result.receipt['duration_seconds'], 15)
            child_pid = int((root / 'child.pid').read_text())
            deadline = time.monotonic() + 2
            while True:
                try:
                    if psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE:
                        break
                except psutil.NoSuchProcess:
                    break
                self.assertLess(time.monotonic(), deadline, 'owned child still executing')
                time.sleep(0.02)

    def test_checkpoint_guard_and_new_timeout_budget_are_explicit(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / 'build/windows/build-google-services.ps1').read_text()
        self.assertIn("'--manifest-timeout-seconds', '1800'", script)
        self.assertIn("'--plan-timeout-seconds', '600'", script)
        self.assertIn("refusing a live-tree checkpoint", script)
        self.assertIn('$selectedCheckpoint.stage', script)


if __name__ == '__main__':
    unittest.main()
