"""Synthetic report/fixture tests only; these do not execute or accept a browser."""
from __future__ import annotations
import copy
from http.client import HTTPConnection
import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import verify_native_cookie_runtime as runtime


def good_report():
    """An in-memory structural fixture, never delivered as runtime evidence."""
    root = Path('security-runtime-native-cookie-fixture').absolute()
    browser = Path('synthetic-not-executable-chrome.exe').absolute()
    hashes = ('browser_sha256', 'chrome_dll_sha256', 'capabilities_sha256',
              'source_receipt_sha256', 'verifier_sha256', 'security_helper_sha256',
              'process_helper_sha256', 'recipe_tool_sha256', 'requirements_sha256', 'python_sha256')
    binding = {key: 'a' * 64 for key in hashes}
    binding['dependencies'] = {
        'playwright': {'version': '1.53.0', 'files': 1, 'inventory_sha256': 'b' * 64},
        'psutil': {'version': '7.2.2', 'files': 1, 'inventory_sha256': 'b' * 64}}
    report = {'schema_version': 1, 'contract': runtime.CONTRACT, 'status': 'passed',
              'browser': str(browser), 'temporary_root': str(root), 'fixture_nonce': 'c' * 48,
              'fixture_preflight': True, 'binding': binding,
              'source': {key: 'a' * 64 for key in ('record_sha256', 'recipe_sha256', 'source_receipt_sha256', 'gn_sha256')},
              'network': {'origin': 'http://127.0.0.1:55001', 'proxy_port': 55002, 'external_forwarded_requests': 0},
              'cleanup': {key: True for key in ('owned_processes_closed', 'fixture_servers_closed',
                                               'bindings_unchanged', 'owned_profile_removed')},
              'cases': [], 'checks': []}
    first_receipt = None
    for index, name in enumerate(runtime.CASE_NAMES):
        data = root / ('lifecycle' if name in ('first_http', 'restart_pref5', 'completed_token', 'expired_delete') else name)
        pid = 1000 + index
        created = 1900000000.0 + index
        count = 200 if name == 'quota_readback' else 1 if name in ('expired_delete', 'backend_rejection', 'unsupported_partition') else 2
        request_id = f'00000000-0000-4000-8000-{index:012d}'
        token = 'f' * 64
        request = None if name == 'restart_pref5' else {
            'schemaVersion': 1, 'contract': runtime.CONTRACT, 'environmentId': runtime.ENVIRONMENT_ID,
            'profileDirectory': 'Default', 'requestId': request_id, 'token': token,
            'sourceSha256': 'd' * 64, 'envelopeSha256': 'e' * 64, 'requestFileSha256': 'e' * 64,
            'cookieCount': count, 'deadlineUnixMs': 1000 if name == 'deadline_expired' else 2000000000000,
            'requestPath': str(data / 'ShardX Cookie Imports/requests' / (token + '.' + request_id + '.json')),
            'receiptPath': str(data / 'ShardX Cookie Imports/receipts' / (token + '.json')),
            'sourcePath': str(root / 'inputs' / (name + '.json'))}
        paths = (['/case/restored_tabs/0', '/case/restored_tabs/1', '/case/restored_tabs/extra-start']
                 if name == 'restored_tabs' else ['/case/' + name])
        if name in ('about_blank', 'no_startup_window'):
            paths = []
        urls = [] if name == 'no_startup_window' else (
            [report['network']['origin'] + paths[-1]] if name == 'restored_tabs' else
            ['about:blank'] if name == 'about_blank' else [report['network']['origin'] + path for path in paths])
        args = runtime.launch_args(data, 55002, request, urls, name == 'no_startup_window', name == 'restored_tabs')
        row = {'name': name, 'request': request, 'expected_paths': paths, 'completed': True,
               'browser_pid': pid, 'process_created': created, 'owned_exact_browser': True,
               'requested_arguments': args, 'actual_arguments': [str(browser), *args],
               'browser_version': 'Chrome/' + runtime.security.VERSION,
               'close': {'normal': True, 'forced_browser_stop': False, 'exit_code': 0, 'cleanup_errors': []},
               'startup_pref_after': 1 if name == 'restored_tabs' else 5,
               'startup_pref_observed': 1 if name == 'restored_tabs' else 5,
               'original_sha256_after': 'd' * 64, 'receipt_before_cdp': True,
               'receipt_read_unix_ms': 1900000100000, 'receipt_sha256': '1' * 64,
               'http_events': [], 'receipt_unwritable': name == 'receipt_unwritable'}
        if name in runtime.SUCCESS:
            unique, duplicates, expired = runtime.SUCCESS[name]
            receipt = {key: request[key] for key in ('schemaVersion', 'contract', 'environmentId', 'profileDirectory',
                       'requestId', 'token', 'sourceSha256', 'envelopeSha256', 'deadlineUnixMs', 'cookieCount')}
            receipt.update(browserPid=pid, status='completed', uniqueCookieCount=unique,
                attemptedCookieCount=unique, acceptedCookieCount=unique, verifiedCookieCount=unique-expired,
                expiredCookieCount=expired, duplicateCookieCount=duplicates,
                partialApplied=False, writeOutcomeUnknown=False, rollbackPerformed=False)
            row['receipt'] = receipt
            if name == 'first_http':
                first_receipt = copy.deepcopy(receipt)
                row['cookie_attributes'] = {
                    'SX_SESSION': {'session': True, 'httpOnly': True, 'secure': False, 'path': '/', 'priority': 'Medium'},
                    'SX_PERSIST': {'session': False, 'httpOnly': False, 'secure': False, 'path': '/', 'priority': 'High'}}
        elif name in runtime.FAILURES:
            receipt = {key: request[key] for key in ('schemaVersion', 'contract', 'environmentId', 'profileDirectory',
                       'requestId', 'token', 'envelopeSha256')}
            receipt.update(browserPid=pid, status='failed', errorCode=runtime.FAILURES[name],
                attemptedCookieCount=0, acceptedCookieCount=0, verifiedCookieCount=0, expiredCookieCount=0,
                partialApplied=False, writeOutcomeUnknown=False, rollbackPerformed=False)
            if name == 'quota_readback':
                receipt.update(cookieCount=200, uniqueCookieCount=200, attemptedCookieCount=200,
                               acceptedCookieCount=200, verifiedCookieCount=150, partialApplied=True)
            elif name == 'backend_rejection':
                receipt['attemptedCookieCount'] = 1
            row.update(receipt=receipt, navigation_error='ERR_BLOCKED_BY_CLIENT',
                       navigation_timed_out=False, stored_fixture_cookie_names=[])
        elif name == 'receipt_unwritable':
            row.update(navigation_error='ERR_BLOCKED_BY_CLIENT', navigation_timed_out=False,
                       completed_receipt_exists=False)
        elif name == 'completed_token':
            row['receipt'] = copy.deepcopy(first_receipt)
            row['stored_fixture_cookie_names'] = ['SX_PERSIST']
        elif name == 'restart_pref5':
            row['stored_fixture_cookie_names'] = ['SX_PERSIST']
        if name == 'expired_delete':
            row['stored_fixture_cookie_names'] = []
        if paths and name not in runtime.FAILURES and name != 'receipt_unwritable':
            names = ['SX_SESSION'] if name == 'duplicate_identical' else [] if name == 'expired_delete' else (
                ['SX_PERSIST'] if name in ('restart_pref5', 'completed_token') else ['SX_PERSIST', 'SX_SESSION'])
            keys = ['SX_SESSION'] if name == 'duplicate_identical' else ['SX_SESSION', 'SX_PERSIST']
            row['http_events'] = [{'case': name, 'path': path, 'nonce': report['fixture_nonce'],
                                   'cookie_names': names, 'cookie_matches': {key: True for key in keys}}
                                  for path in paths]
        report['cases'].append(row)
    restored = next(row for row in report['cases'] if row['name'] == 'restored_tabs')
    report['restored_seed'] = {'completed': True, 'request': None, 'expected_paths': restored['expected_paths'][:2],
                              'http_events': restored['http_events'][:2], 'close': {'normal': True},
                              'startup_pref_observed': 5}
    report['checks'] = runtime.assess(report)
    return report


class NativeCookieReportTest(unittest.TestCase):
    def test_declared_checks_must_be_complete_unique_successful_and_not_foreign(self):
        for replacement in ([], [{'name': 'failed-fixture-check', 'passed': False}],
                            [{'name': 'foreign-fixture-check', 'passed': True}]):
            report = good_report(); report['checks'] = replacement
            with self.subTest(replacement=replacement):
                self.assertFalse(runtime.report_passed(report))
        report = good_report(); report['checks'].append(copy.deepcopy(report['checks'][0]))
        self.assertFalse(runtime.report_passed(report))
        report = good_report(); report['checks'][0]['passed'] = False
        self.assertFalse(runtime.report_passed(report))
        report = good_report(); report['checks'][0]['passed'] = 1
        self.assertFalse(runtime.report_passed(report))

    def test_valid_in_memory_shape_and_no_missing_or_duplicate_case_can_pass(self):
        report = good_report()
        self.assertTrue(runtime.report_passed(report), [x for x in runtime.assess(report) if not x['passed']])
        missing = copy.deepcopy(report); missing['cases'].pop()
        self.assertFalse(runtime.report_passed(missing))
        duplicate = copy.deepcopy(report); duplicate['cases'].append(copy.deepcopy(duplicate['cases'][0]))
        self.assertFalse(runtime.report_passed(duplicate))
        report['error'] = {'type': 'RuntimeError'}
        self.assertFalse(runtime.report_passed(report))

    def test_first_real_http_request_needs_both_native_cookie_values(self):
        for field, value in [('cookie_names', ['SX_PERSIST']),
                             ('cookie_matches', {'SX_PERSIST': True}),
                             ('cookie_matches', {'SX_SESSION': False, 'SX_PERSIST': True}),
                             ('nonce', 'wrong')]:
            report = good_report()
            report['cases'][0]['http_events'][0][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(runtime.report_passed(report))

    def test_current_completion_must_match_this_pid_attempt_and_shared_deadline(self):
        for key, value in [('browserPid', 999), ('requestId', 'wrong'), ('envelopeSha256', '0'*64),
                           ('sourceSha256', '0'*64), ('attemptedCookieCount', 1),
                           ('partialApplied', True), ('writeOutcomeUnknown', True),
                           ('rollbackPerformed', True)]:
            report = good_report()
            report['cases'][0]['receipt'][key] = value
            with self.subTest(key=key):
                self.assertFalse(runtime.report_passed(report))
        report = good_report()
        report['cases'][0]['receipt_read_unix_ms'] = report['cases'][0]['request']['deadlineUnixMs'] + 1
        self.assertFalse(runtime.report_passed(report))

    def test_boolean_counts_and_inconsistent_deduplication_cannot_pass(self):
        report = good_report()
        row = next(x for x in report['cases'] if x['name'] == 'duplicate_identical')
        row['receipt']['acceptedCookieCount'] = True
        self.assertFalse(runtime.report_passed(report))
        report = good_report()
        row = next(x for x in report['cases'] if x['name'] == 'expired_delete')
        row['request']['cookieCount'] = row['receipt']['cookieCount'] = 2
        self.assertFalse(runtime.report_passed(report))

    def test_session_cannot_be_promoted_or_replayed_from_old_completion(self):
        for name in ('restart_pref5', 'completed_token'):
            report = good_report()
            row = next(x for x in report['cases'] if x['name'] == name)
            row['stored_fixture_cookie_names'] = ['SX_PERSIST', 'SX_SESSION']
            self.assertFalse(runtime.report_passed(report))
        report = good_report()
        row = next(x for x in report['cases'] if x['name'] == 'completed_token')
        row['receipt']['browserPid'] = row['browser_pid']
        self.assertFalse(runtime.report_passed(report))

    def test_hash_format_conflict_and_quota_failure_are_not_completion(self):
        for name in runtime.FAILURES:
            report = good_report()
            row = next(x for x in report['cases'] if x['name'] == name)
            row['receipt']['status'] = 'completed'
            with self.subTest(name=name):
                self.assertFalse(runtime.report_passed(report))
        report = good_report()
        row = next(x for x in report['cases'] if x['name'] == 'quota_readback')
        row['receipt']['acceptedCookieCount'] = 0
        self.assertFalse(runtime.report_passed(report))

    def test_backend_prevalidation_is_not_misreported_as_native_set_rejection(self):
        report = good_report()
        row = next(x for x in report['cases'] if x['name'] == 'backend_rejection')
        row['receipt']['errorCode'] = 'invalid_cookie'
        row['receipt']['attemptedCookieCount'] = 0
        self.assertFalse(runtime.report_passed(report))

    def test_no_receipt_timeout_is_not_a_proven_terminal_cancellation(self):
        report = good_report()
        row = next(x for x in report['cases'] if x['name'] == 'receipt_unwritable')
        row['navigation_timed_out'] = True
        row['navigation_error'] = None
        self.assertFalse(runtime.report_passed(report))

    def test_no_startup_window_and_about_blank_need_eager_completion_before_cdp(self):
        for name in ('about_blank', 'no_startup_window'):
            report = good_report()
            row = next(x for x in report['cases'] if x['name'] == name)
            row['receipt_before_cdp'] = False
            with self.subTest(name=name):
                self.assertFalse(runtime.report_passed(report))

    def test_restored_tabs_and_extra_start_url_must_receive_the_import(self):
        for index in range(3):
            report = good_report()
            row = next(x for x in report['cases'] if x['name'] == 'restored_tabs')
            row['http_events'].pop(index)
            with self.subTest(index=index):
                self.assertFalse(runtime.report_passed(report))
        report = good_report(); report['restored_seed']['close']['normal'] = False
        self.assertFalse(runtime.report_passed(report))

    def test_restore_uses_native_global_flag_and_original_start_url(self):
        report = good_report()
        for row in report['cases']:
            expected = '--shardx-restore-previous-session=' + str(row['name'] == 'restored_tabs').lower()
            self.assertIn(expected, row['requested_arguments'])
            self.assertIn('--shardx-history-retention-days=0', row['requested_arguments'])
        restored = next(row for row in report['cases'] if row['name'] == 'restored_tabs')
        self.assertEqual([arg for arg in restored['requested_arguments'] if arg.startswith('http://')],
                         [report['network']['origin'] + '/case/restored_tabs/extra-start'])
        report = good_report()
        restored = next(row for row in report['cases'] if row['name'] == 'restored_tabs')
        for key in ('requested_arguments', 'actual_arguments'):
            restored[key] = [arg.replace('--shardx-restore-previous-session=true',
                                       '--shardx-restore-previous-session=false') for arg in restored[key]]
        self.assertFalse(runtime.report_passed(report))

    def test_browser_override_or_production_path_cannot_hide_in_requested_arguments(self):
        report = good_report()
        row = report['cases'][0]
        row['requested_arguments'].append('--no-sandbox')
        row['actual_arguments'].append('--no-sandbox')
        self.assertFalse(runtime.report_passed(report))
        report = good_report()
        report['cases'][0]['request']['requestPath'] = str(Path('outside.json').absolute())
        self.assertFalse(runtime.report_passed(report))

    def test_cleanup_original_input_and_immutable_binding_are_required(self):
        for name in ('owned_processes_closed', 'fixture_servers_closed', 'bindings_unchanged', 'owned_profile_removed'):
            report = good_report(); report['cleanup'][name] = False
            with self.subTest(name=name):
                self.assertFalse(runtime.report_passed(report))
        report = good_report(); report['cases'][0]['original_sha256_after'] = '0' * 64
        self.assertFalse(runtime.report_passed(report))


class NativeCookieFixtureTest(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows owned-process gate')
    def test_assignment_failure_closes_the_unreleased_supervisor_input(self):
        with tempfile.TemporaryDirectory(prefix='cookie-owned-gate-') as directory:
            root = Path(directory); data = root / 'data'; runtime.make_profile(data)
            gate = Mock()
            gate.stdin = io.BytesIO()
            gate.wait.return_value = 125
            job = Mock()
            job.assign.side_effect = OSError('synthetic assignment failure')
            with patch.object(runtime.process_helper, 'WindowsJob', return_value=job), \
                    patch.object(runtime.subprocess, 'Popen', return_value=gate):
                with self.assertRaises(OSError):
                    runtime.NativeProcess(Path('synthetic.exe').absolute(),
                        runtime.launch_args(data, 55002, None, ['about:blank']), root, 'failure', {}, None)
            self.assertTrue(gate.stdin.closed)
            job.close.assert_called_once()
            gate.wait.assert_called()

    def test_driver_loopback_bypass_and_credential_filter_do_not_mutate_caller_environment(self):
        original = {'HTTP_PROXY': 'http://proxy.invalid:8080', 'NO_PROXY': 'example.test',
                    'GOOGLE_API_KEY': 'synthetic-private', 'GITHUB_TOKEN': 'synthetic-private'}
        before = dict(original)
        result = runtime.audit_environment(original)
        self.assertEqual(original, before)
        self.assertNotIn('GOOGLE_API_KEY', result)
        self.assertNotIn('GITHUB_TOKEN', result)
        self.assertTrue({'127.0.0.1', 'localhost', '::1', 'example.test'} <= set(result['NO_PROXY'].split(',')))
        self.assertEqual(result['NO_PROXY'], result['no_proxy'])

    def test_staging_binds_shared_deadline_without_modifying_original(self):
        with tempfile.TemporaryDirectory(prefix='cookie-request-unit-') as directory:
            root = Path(directory); data = root / 'data'; runtime.make_profile(data)
            cookies = [{'name': 'SX_SESSION', 'value': 'synthetic', 'url': 'http://127.0.0.1/', 'httpOnly': True}]
            stage = runtime.make_stage(root, data, 'fixture', cookies)
            raw = Path(stage['sourcePath']).read_bytes()
            envelope = json.loads(Path(stage['requestPath']).read_bytes())
            self.assertEqual(envelope['cookies'], cookies)
            self.assertGreater(envelope['deadlineUnixMs'], runtime.now_ms())
            self.assertEqual(runtime.security.digest(Path(stage['requestPath'])), stage['envelopeSha256'])
            self.assertEqual(runtime.security.digest(Path(stage['sourcePath'])), stage['sourceSha256'])
            again = runtime.make_stage(root, data, 'fixture', cookies, token=stage['token'])
            self.assertNotEqual(again['requestId'], stage['requestId'])
            self.assertEqual(Path(stage['sourcePath']).read_bytes(), raw)

    def test_loopback_observer_never_sets_cookies_or_echoes_their_values(self):
        fixture = runtime.Fixtures()
        try:
            self.assertTrue(fixture.preflight())
            fixture.select('observed', {'SX_SESSION': 'private-synthetic-value'})
            connection = HTTPConnection('127.0.0.1', fixture.server.server_port, timeout=5)
            connection.request('GET', '/case/observed', headers={'Cookie': 'SX_SESSION=private-synthetic-value'})
            response = connection.getresponse(); response.read()
            self.assertIsNone(response.getheader('Set-Cookie')); connection.close()
            events = fixture.events('observed')
            self.assertTrue(events[0]['cookie_matches']['SX_SESSION'])
            self.assertNotIn('private-synthetic-value', json.dumps(events))
        finally:
            self.assertTrue(fixture.close())

    def test_probe_contains_no_devtools_cookie_writer_or_legacy_importer(self):
        source = Path(runtime.__file__).read_text(encoding='utf-8')
        for forbidden in ('Network.setCookie', 'Storage.setCookies', '.add_cookies(', 'importProfileCookies',
                          'SetForceKeepSessionState', 'launch_persistent_context(', 'settingsPrivate.setPref'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
