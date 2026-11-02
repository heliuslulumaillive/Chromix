"""Runtime-report negative controls; no browser or real profile is opened."""
from __future__ import annotations

import copy
from http.client import HTTPConnection
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tools import verify_native_browser_runtime as runtime


def good_report():
    root = Path('security-runtime-native-browser-fixture').absolute()
    browser = Path('chrome.exe').absolute()
    binding = {key: 'a' * 64 for key in ('browser_sha256', 'chrome_dll_sha256',
        'capabilities_sha256', 'source_receipt_sha256', 'verifier_sha256',
        'security_helper_sha256', 'recipe_tool_sha256', 'python_sha256', 'requirements_sha256')}
    binding['dependencies'] = {package: {'inventory_sha256': 'b' * 64, 'files': 1,
                                       'version': runtime.LOCKED_DEPENDENCIES[package]}
                              for package in ('playwright', 'psutil')}
    report = {'schema_version': 1, 'contract': runtime.CONTRACT,
        'browser': str(browser), 'temporary_root': str(root), 'binding': binding,
        'source': {key: 'a' * 64 for key in ('record_sha256', 'recipe_sha256', 'source_receipt_sha256', 'gn_sha256')},
        'network': {'proxy_port': 55003, 'blocked_port': 55001, 'allowed_port': 55002,
            'nonces': {'blocked': 'c' * 48, 'allowed': 'd' * 48}, 'external_forwarded_requests': 0},
        'fixture_preflight': [{'target': key, 'passed': True} for key in (
            'blocked_ipv4', 'blocked_ipv6', 'allowed_ipv4', 'allowed_ipv6')],
        'scenarios': []}
    for index, (name, profile, blocked, dnt) in enumerate(runtime.SCENARIOS):
        options = runtime.launch_options(browser, root / name, profile, 55003, 55001, blocked, dnt, {})
        row = {'name': name, 'completed': True, 'browser_version': 'Chrome/' + runtime.VERSION,
            'process_identity': [100 + index, 1234567.5 + index],
            'launch_policy': {key: value for key, value in options.items() if key != 'env'},
            'actual_arguments': [str(browser), *options['args']],
            'actual_profile_path': str(root / name / profile)}
        if name == 'defaults':
            row['preferences'] = {'available': True, 'values': {
                key: {'error': None, 'pref': {'key': key, 'type': kind, 'value': wanted}}
                for key, (kind, wanted) in runtime.PREFS.items()}}
            row['search'] = {'available': True, 'engines': [
                {'name': 'Google', 'keyword': 'google.com', 'url': 'https://www.google.com/search?q=%s', 'default': False},
                {'name': 'Native regional default', 'keyword': 'regional.test', 'url': 'https://regional.test/?q=%s', 'default': True}]}
            row['saved_labs_experiments'] = []
        if blocked is not None:
            row['media'] = {'secure': True, 'available': True,
                'permissions': {name: 'denied' if blocked else 'granted' for name in ('camera', 'microphone')},
                'capture': {name: {'ok': False, 'error': 'NotAllowedError'} if blocked
                            else {'ok': True, 'tracks': 1} for name in ('video', 'audio')}}
            row['saved_media_grants'] = {'media_stream_camera': 1, 'media_stream_mic': 1}
            row['media_seed'] = {'preferences_sha256': 'e' * 64}
        if name == 'defaults' or blocked:
            row['fetches'] = {}
            for key in ('blocked_ipv4', 'blocked_ipv6', 'allowed_ipv4', 'allowed_ipv6'):
                kind, family = key.split('_')
                port = report['network'][kind + '_port']
                host = '127.0.0.1' if family == 'ipv4' else '[::1]'
                path = '/fetch/' + name + '/' + key
                denied = bool(blocked and kind == 'blocked')
                result = {'url': f'http://{host}:{port}' + path, 'ok': not denied,
                    'failure': 'net::ERR_UNSAFE_PORT' if denied else None,
                    'server_hits': 0 if denied else 1}
                if not denied:
                    result.update(status=200, payload={'nonce': report['network']['nonces'][kind],
                        'path': path, 'dnt': '1' if dnt else None})
                row['fetches'][key] = result
        if blocked:
            row['navigator_dnt'] = '1' if dnt else None
        report['scenarios'].append(row)
    report['checks'] = runtime.assess(report) + [
        {'name': name, 'passed': True} for name in runtime.CLEANUP_CHECKS]
    return report


def scenario(report, name):
    return next(row for row in report['scenarios'] if row['name'] == name)


class AssessmentTests(unittest.TestCase):
    def test_complete_report_and_native_regional_search_precedence(self):
        report = good_report()
        self.assertTrue(runtime.report_passed(report), [row for row in runtime.assess(report) if not row['passed']])
        # Google need not be the user's/region's selected default.
        self.assertFalse(scenario(report, 'defaults')['search']['engines'][0]['default'])

    def test_missing_scenario_or_any_declared_check_is_rejected(self):
        for name, *_ in runtime.SCENARIOS:
            report = good_report()
            report['scenarios'] = [row for row in report['scenarios'] if row['name'] != name]
            self.assertFalse(runtime.report_passed(report), name)
        report = good_report()
        for index in range(len(report['checks'])):
            changed = copy.deepcopy(report)
            changed['checks'].pop(index)
            self.assertFalse(runtime.report_passed(changed))

    def test_boolean_statuses_empty_results_duplicates_and_interruption_fail(self):
        for value in ({}, {'checks': []}, {'scenarios': [None]}, {'scenarios': [{'name': []}]},
                      {'checks': [{'name': []}]}, {'fixture_preflight': [{'target': []}]}):
            self.assertFalse(runtime.report_passed(value))
        for change in ('duplicate_check', 'duplicate_scenario', 'integer_pass', 'error', 'process_reuse'):
            report = good_report()
            if change == 'duplicate_check': report['checks'].append(report['checks'][0])
            elif change == 'duplicate_scenario': report['scenarios'].append(report['scenarios'][0])
            elif change == 'integer_pass': report['checks'][0]['passed'] = 1
            elif change == 'error': report['error'] = 'interrupted'
            else: report['scenarios'][1]['process_identity'] = report['scenarios'][0]['process_identity']
            self.assertFalse(runtime.report_passed(report), change)

    def test_every_native_default_requires_value_type_and_api_success(self):
        for key in runtime.PREFS:
            for change in ('missing', 'error', 'type', 'controlled'):
                report = good_report()
                values = scenario(report, 'defaults')['preferences']['values']
                if change == 'missing': del values[key]
                elif change == 'error': values[key]['error'] = 'API failed'
                elif change == 'type': values[key]['pref']['value'] = str(values[key]['pref']['value'])
                else: values[key]['pref']['controlledBy'] = 'DEVICE_POLICY'
                self.assertFalse(runtime.report_passed(report), (key, change))
        for change in ('empty', 'nosearch', 'history', 'forced_session'):
            report = good_report(); row = scenario(report, 'defaults')
            if change == 'empty': row['search']['engines'] = []
            elif change == 'nosearch': row['search']['engines'].append({'name': 'No Search', 'keyword': 'nosearch'})
            elif change == 'history': row['saved_labs_experiments'] = ['keep-old-history']
            else: row['actual_arguments'].append('--restore-last-session')
            self.assertFalse(runtime.report_passed(report), change)

    def test_wrong_profile_or_missing_fake_device_positive_control_fails(self):
        report = good_report(); row = scenario(report, 'blocked_true_profile1')
        row['actual_profile_path'] = str(Path(row['actual_profile_path']).parent / 'Default')
        self.assertFalse(runtime.report_passed(report))
        for name in ('allow_default', 'allow_profile1'):
            report = good_report(); scenario(report, name)['media']['capture']['audio'] = {'ok': False, 'error': 'NotFoundError'}
            self.assertFalse(runtime.report_passed(report))

    def test_media_error_is_not_accepted_as_policy_denial(self):
        for error in ('NotFoundError', 'NotReadableError', 'ProbeTimeout', 'TypeError', None):
            report = good_report(); scenario(report, 'blocked_true_default')['media']['capture']['video'] = {'ok': False, 'error': error}
            self.assertFalse(runtime.report_passed(report), error)
        for field in ('media', 'saved_media_grants', 'media_seed'):
            report = good_report(); del scenario(report, 'blocked_false_profile1')[field]
            self.assertFalse(runtime.report_passed(report), field)

    def test_connection_failure_ipv6_alias_wrong_nonce_and_real_hit_fail(self):
        for change in ('timeout', 'ipv4_alias', 'nonce', 'unexpected_hit', 'missing'):
            report = good_report(); fetches = scenario(report, 'blocked_true_profile1')['fetches']
            if change == 'timeout': fetches['blocked_ipv6']['failure'] = 'net::ERR_CONNECTION_REFUSED'
            elif change == 'ipv4_alias': fetches['blocked_ipv6']['url'] = fetches['blocked_ipv6']['url'].replace('[::1]', '127.0.0.1')
            elif change == 'nonce': fetches['allowed_ipv4']['payload']['nonce'] = 'f' * 48
            elif change == 'unexpected_hit': fetches['blocked_ipv4']['server_hits'] = 1
            else: del fetches['allowed_ipv6']
            self.assertFalse(runtime.report_passed(report), change)

    def test_dnt_requires_both_headers_and_navigator_in_both_profiles(self):
        for name, *_ in runtime.SCENARIOS:
            if not name.startswith('blocked_'): continue
            for change in ('navigator', 'header', 'missing_header'):
                report = good_report(); row = scenario(report, name)
                if change == 'navigator': row['navigator_dnt'] = '0'
                elif change == 'header': row['fetches']['allowed_ipv6']['payload']['dnt'] = '0'
                else: del row['fetches']['allowed_ipv4']['payload']['dnt']
                self.assertFalse(runtime.report_passed(report), (name, change))

    def test_binding_missing_fields_and_sandbox_or_network_overrides_fail(self):
        report = good_report()
        for key in report['binding']:
            changed = copy.deepcopy(report); del changed['binding'][key]
            self.assertFalse(runtime.report_passed(changed), key)
        for flag in ('--no-sandbox', '--ignore-certificate-errors', '--disable-web-security', '--no-proxy-server'):
            changed = good_report(); scenario(changed, 'defaults')['actual_arguments'].append(flag)
            self.assertFalse(runtime.report_passed(changed), flag)


class OwnedFixtureTests(unittest.TestCase):
    def test_media_setup_refuses_any_existing_data_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / 'new-data'
            seed = runtime.seed_media_preferences(data, 'Profile 1', 'http://127.0.0.1:55002')
            self.assertEqual(len(seed['preferences_sha256']), 64)
            self.assertEqual(runtime.stored_media_grants(data, 'Profile 1', 'http://127.0.0.1:55002'),
                             {'media_stream_camera': 1, 'media_stream_mic': 1})
            with self.assertRaises(FileExistsError):
                runtime.seed_media_preferences(data, 'Default', 'http://127.0.0.1:55002')
            self.assertFalse((data / 'Default').exists())

    def test_loopback_ipv4_ipv6_echo_and_external_deny(self):
        try:
            fixtures = runtime.Fixtures()
        except OSError as error:
            self.skipTest('The host lacks an available IPv6 loopback listener: ' + str(error))
        try:
            self.assertEqual(len(fixtures.preflight()), 4)
            self.assertTrue(all(row['passed'] for row in fixtures.preflight()))
            connection = HTTPConnection('::1', fixtures.allowed_port, timeout=5)
            connection.request('GET', '/echo', headers={'DNT': '1'})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())['dnt'], '1')
            connection.close()
            connection = HTTPConnection('127.0.0.1', fixtures.proxy.server_port, timeout=5)
            connection.request('GET', 'https://outside.invalid/private?not-for-log=secret')
            self.assertEqual(connection.getresponse().status, 403)
            connection.close()
            self.assertEqual(fixtures.proxy.denied[-1]['host'], 'outside.invalid')
            self.assertNotIn('secret', json.dumps(fixtures.proxy.denied))
        finally:
            self.assertTrue(fixtures.close())


if __name__ == '__main__':
    unittest.main()
