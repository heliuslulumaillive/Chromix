"""Contract tests do not launch browsers or open real profiles."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from tools import verify_security_runtime as runtime


def page_fixture():
    return {name: {'url': url, 'ready': True, 'safeBrowsingSectionVisible': True}
            for name, url in runtime.URLS.items()}


def pref_fixture():
    return {'available': True, 'values': {
        key: {'error': None, 'pref': {'key': key, 'type': 'BOOLEAN', 'value': value}}
        for key, value in runtime.EXPECTED_PREFS.items()}}


def source_fixture():
    return {'schema_version': 1, 'build_variant': 'security-win156',
            'chromium_version': runtime.VERSION, 'recipe_sha256': 'a' * 64,
            'source_receipt_sha256': 'b' * 64,
            'gn': {'sha256': 'c' * 64, 'assignments': dict(runtime.build_record.REQUIRED_FLAGS)}}


class RuntimeContractTests(unittest.TestCase):
    def test_exact_underlying_defaults_not_generated_or_ui_labels(self):
        self.assertEqual(runtime.EXPECTED_PREFS, {
            'safebrowsing.enabled': True, 'safebrowsing.enhanced': False,
            'safebrowsing.scout_reporting_enabled': False,
            'profile.password_manager_leak_detection': True})
        self.assertTrue(all(row['passed'] for row in runtime.assess_pages(page_fixture(), pref_fixture())))
        self.assertNotIn('setPref(', runtime.READ_PREFS)

    def test_every_missing_changed_or_mistyped_preference_fails(self):
        for key in runtime.EXPECTED_PREFS:
            for change in ('missing', 'inverted', 'integer', 'string', 'error', 'managed'):
                values = pref_fixture()
                if change == 'missing':
                    del values['values'][key]
                else:
                    row = values['values'][key]
                    if change == 'inverted':
                        row['pref']['value'] = not runtime.EXPECTED_PREFS[key]
                    elif change == 'integer':
                        row['pref']['value'] = int(runtime.EXPECTED_PREFS[key])
                    elif change == 'string':
                        row['pref']['value'] = str(runtime.EXPECTED_PREFS[key])
                    elif change == 'error':
                        row['error'] = 'Pref does not exist'
                    elif change == 'managed':
                        row['pref']['enforcement'] = 'ENFORCED'
                result = runtime.assess_pages(page_fixture(), values)
                with self.subTest(key=key, change=change):
                    self.assertFalse(next(row['passed'] for row in result if row['name'] == 'default_pref/' + key))

    def test_pages_and_backend_api_are_independent_requirements(self):
        for name in runtime.URLS:
            pages = page_fixture()
            pages[name] = {'url': 'chrome-error://chromewebdata/', 'ready': True,
                           'title': 'Safe Browsing', 'safeBrowsingSectionVisible': True}
            self.assertFalse(all(row['passed'] for row in runtime.assess_pages(pages, pref_fixture())))
        pages = page_fixture()
        pages['settings_security']['safeBrowsingSectionVisible'] = False
        self.assertFalse(all(row['passed'] for row in runtime.assess_pages(pages, pref_fixture())))
        self.assertFalse(all(row['passed'] for row in runtime.assess_pages(
            page_fixture(), {'available': False, 'values': pref_fixture()['values']})))

    def test_complete_unique_mandatory_checks_required(self):
        good = {'checks': [{'name': name, 'passed': True} for name in runtime.REQUIRED_CHECKS]}
        self.assertTrue(runtime.report_passed(good))
        for name in runtime.REQUIRED_CHECKS:
            self.assertFalse(runtime.report_passed({'checks': [r for r in good['checks'] if r['name'] != name]}))
        for changed in ({'checks': good['checks'] + [good['checks'][0]]},
                        {'checks': good['checks'], 'error': 'probe interrupted'},
                        {'checks': [{'name': r['name'], 'passed': 1} for r in good['checks']]}):
            self.assertFalse(runtime.report_passed(changed))

    def test_minimal_launch_and_extra_library_flags_fail_closed(self):
        browser, profile = Path('chrome.exe').absolute(), Path('owned-empty-profile').absolute()
        options = runtime.launch_options(browser, profile, 12345, {})
        self.assertIs(options['ignore_default_args'], True)
        self.assertIs(options['chromium_sandbox'], True)
        self.assertIs(options['ignore_https_errors'], False)
        self.assertIs(options['bypass_csp'], False)
        args = options['args']
        self.assertIn('--remote-debugging-pipe', args)
        self.assertFalse(any(flag.startswith(('--disable-', '--ignore-', '--no-sandbox', '--password-store',
                                             '--remote-debugging-port', '--use-mock-keychain')) for flag in args))
        self.assertTrue(runtime.arguments_match([str(browser), *args], args, browser))
        internal = ['--noerrdialogs', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                    '--flag-switches-begin', '--flag-switches-end']
        self.assertTrue(runtime.arguments_match([str(browser), *args, *internal], args, browser))
        self.assertFalse(runtime.arguments_match([str(browser), *args, '--flag-switches-begin'], args, browser))
        self.assertFalse(runtime.arguments_match([str(browser), *args, '--flag-switches-begin',
            '--noerrdialogs', '--flag-switches-end'], args, browser))
        for extra in ('--no-sandbox', '--ignore-certificate-errors=false',
                      '--disable-background-networking', '--disable-component-update',
                      '--disable-features=HttpsUpgrades', '--password-store=basic',
                      '--remote-debugging-port=9222'):
            self.assertFalse(runtime.arguments_match([str(browser), *args, extra], args, browser))
        self.assertFalse(runtime.arguments_match([str(browser), *args, args[0]], args, browser))

    def test_source_record_wrong_recipe_variant_or_gn_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source.json'
            source.write_text(json.dumps(source_fixture()))
            self.assertEqual(runtime.source_identity(source, 'a' * 64)['recipe_sha256'], 'a' * 64)
            for key, value in [('build_variant', 'fingerprint-modes-win155'),
                               ('chromium_version', '155.0.8059.39'), ('recipe_sha256', 'd' * 64)]:
                changed = source_fixture()
                changed[key] = value
                source.write_text(json.dumps(changed))
                with self.subTest(key=key), self.assertRaises(ValueError):
                    runtime.source_identity(source, 'a' * 64)
            changed = source_fixture()
            changed['gn']['assignments']['safe_browsing_mode'] = '0'
            source.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                runtime.source_identity(source, 'a' * 64)

    def test_wrong_installed_playwright_pin_is_rejected(self):
        class Distribution:
            version = '1.47.0'
        with patch.object(runtime.importlib.metadata, 'distribution', return_value=Distribution()):
            with self.assertRaisesRegex(ValueError, 'locked runtime dependency'):
                runtime.dependency_identity()

    def test_scoped_cleanup_requires_parent_name_and_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / 'security-runtime-fixture'
            root.mkdir()
            (root / '.security-runtime-owner').write_text('owned')
            (root / 'nonsecret').write_text('fixture')
            for wrong_parent, token in ((parent.parent, 'owned'), (parent, 'foreign')):
                with self.assertRaises(ValueError):
                    runtime.remove_owned(root, wrong_parent, token)
                self.assertTrue((root / 'nonsecret').exists())
            runtime.remove_owned(root, parent, 'owned')
            self.assertFalse(root.exists())

    def test_deny_proxy_rejects_connect_and_never_logs_query_credentials(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), runtime.DenyProxy)
        server.daemon_threads = True
        server.denied = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for method, target in [('CONNECT', 'example.invalid:443'),
                                   ('GET', 'http://example.invalid/?key=synthetic-not-log')]:
                connection = HTTPConnection('127.0.0.1', server.server_port, timeout=2)
                connection.request(method, target)
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()
            self.assertEqual(len(server.denied), 2)
            self.assertNotIn('synthetic-not-log', json.dumps(server.denied))
            self.assertTrue(all(row['host'] == 'example.invalid' for row in server.denied))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    @unittest.skipUnless(os.name == 'nt', 'Windows lane CLI')
    def test_cli_failure_is_nonzero_and_retained_with_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            browser = root / 'chrome.exe'
            browser.write_bytes(b'executable fixture')
            browser.with_name('chrome.dll').write_bytes(b'dll fixture')
            source, output = root / 'source.json', root / 'report.json'
            source.write_text(json.dumps(source_fixture()))
            def probe(_browser, _root, report):
                report['checks'] = [{'name': name, 'passed': name != 'safe_browsing_page_available'}
                    for name in runtime.REQUIRED_CHECKS - {'bindings_unchanged', 'owned_profile_removed'}]
            with patch.object(runtime.build_record, 'recipe', return_value='a' * 64), \
                    patch.object(runtime, 'dependency_identity', return_value={'fixture': True}), \
                    patch.object(runtime, 'probe', side_effect=probe), redirect_stdout(io.StringIO()):
                self.assertEqual(runtime.main(['--browser', str(browser), '--source-record', str(source),
                                               '--report', str(output)]), 1)
            report = json.loads(output.read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertFalse(Path(report['temporary_root']).exists())
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                runtime.main(['--browser', str(browser), '--source-record', str(source), '--report', str(output)])


if __name__ == '__main__':
    unittest.main()
