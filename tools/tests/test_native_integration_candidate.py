"""Synthetic evidence verification only: never execute these fake browser bytes."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
with mock.patch.object(sys, 'path', [str(ROOT / 'tools'), *sys.path]):
    from tools import verify_google_services_candidate as candidate
from tools import verify_native_browser_tests as browser_tests
from tools import native_compiled_evidence as compiled
from tools.tests.test_native_browser_runtime import good_report as good_browser_report
from tools.tests.test_native_cookie_runtime import good_report as good_cookie_report


def write_json(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def bind_runtime(report, bundle, source, kind):
    report = copy.deepcopy(report)
    report.update(status='passed', source=copy.deepcopy(source))
    report['binding'].update({
        'browser_sha256': candidate.sha256(bundle / 'chrome.exe'),
        'chrome_dll_sha256': candidate.sha256(bundle / 'chrome.dll'),
        'capabilities_sha256': candidate.sha256(ROOT / 'build' / ('native-' + kind + '-capabilities.json')),
        'source_receipt_sha256': source['source_receipt_sha256'],
        'verifier_sha256': candidate.sha256(ROOT / 'tools' / ('verify_native_' + kind + '_runtime.py')),
        'security_helper_sha256': candidate.sha256(ROOT / 'tools/verify_security_runtime.py'),
        'history_helper_sha256': candidate.sha256(ROOT / 'tools/native_history_probe.py'),
        'recipe_tool_sha256': candidate.sha256(ROOT / 'tools/security_build_record.py'),
        'requirements_sha256': candidate.sha256(ROOT / 'tools/google-services-requirements.txt'),
    })
    if kind == 'cookie':
        report['binding']['process_helper_sha256'] = candidate.sha256(ROOT / 'tools/fingerprint_subprocess.py')
    return report


def prepare_fixture_build_record(root, bundle):
    path = bundle / 'google-services-build.json'
    build = json.loads(path.read_text()) if path.exists() else {
        'schema_version': 1, 'build_variant': candidate.SECURITY_VARIANT,
        'chromium_version': '156.0.8078.79', 'source_commit': 'a' * 40,
        'run_id': 50, 'preparation_sha256': 'b' * 64, 'gn_args_sha256': 'c' * 64,
    }
    # A tiny opaque identity fixture, never a real compiled test binary.
    executable = root / 'native_passkey_unittests.exe'
    executable.write_bytes(b'synthetic compiled-test identity, never execute')
    build.update(native_test_executable={'name': executable.name,
        'sha256': candidate.sha256(executable), 'bytes': executable.stat().st_size},
        created_at_utc='2026-11-02T00:00:00Z', schema_version=1)
    build['files'] = {name: value for name, value in candidate.inventory(bundle).items()
                      if name != 'google-services-build.json'}
    write_json(path, build)
    return path, build


def write_compiled_fixture(root, kind, names, pattern, build_path, build):
    summary = root / ('native-' + kind + '-unittests.json')
    launcher = root / ('native-' + kind + '-launcher.json')
    write_json(summary, {'per_iteration_data': [{name: [{'status': 'SUCCESS'}] for name in names}]})
    write_json(launcher, {
        'schema_version': 1, 'status': 'passed', 'test_filter': pattern,
        'executable': r'C:\synthetic-only\native_passkey_unittests.exe',
        'executable_sha256': build['native_test_executable']['sha256'],
        'executable_bytes': build['native_test_executable']['bytes'],
        'summary_sha256': candidate.sha256(summary), 'build_record_sha256': candidate.sha256(build_path),
        **{key: build[key] for key in ('source_commit', 'run_id', 'preparation_sha256', 'gn_args_sha256')},
        'started_at_utc': '2026-11-02T00:00:01Z', 'completed_at_utc': '2026-11-02T00:00:02Z',
        'native_exit_code': 0, 'host_process_id': 1234, 'startup_assertions_unchanged': True,
        'test_host_priority': 'Normal', 'original_host_priority': 'BelowNormal',
        'restored_host_priority': 'BelowNormal',
    })
    return summary, launcher


def write_native_integration_evidence(root, bundle, source):
    """All data is a protocol fixture, not real compiled/runtime acceptance."""
    for kind in ('browser', 'cookie'):
        filename = 'native-' + kind + '-capabilities.json'
        (bundle / filename).write_bytes((ROOT / 'build' / filename).read_bytes())
    write_json(root / 'verification-results.json', {'schema_version': 1, 'status': 'passed',
        'checks': [{'label': name, 'status': 'passed'} for name in sorted(candidate.NATIVE_VERIFICATION_LABELS)]})
    build_path, build = prepare_fixture_build_record(root, bundle)
    for kind in ('passkey', 'browser', 'cookie'):
        write_compiled_fixture(root, kind, compiled.REQUIRED_TESTS[kind], compiled.TEST_FILTERS[kind], build_path, build)
    for kind, value in (('browser', good_browser_report()), ('cookie', good_cookie_report())):
        write_json(root / ('native-' + kind + '-runtime.json'), bind_runtime(value, bundle, source, kind))


class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle'; self.bundle.mkdir()
        (self.bundle / 'chrome.exe').write_bytes(b'Synthetic protocol fixture. Never execute.')
        (self.bundle / 'chrome.dll').write_bytes(b'Synthetic library. Not an accepted kernel.')
        for kind in ('browser', 'cookie'):
            name = 'native-' + kind + '-capabilities.json'
            (self.bundle / name).write_bytes((ROOT / 'build' / name).read_bytes())
        self.source = {key: 'a' * 64 for key in ('record_sha256', 'recipe_sha256', 'source_receipt_sha256', 'gn_sha256')}

    def test_exact_fourteen_checks_and_every_negative_variant(self):
        path = self.root / 'verification-results.json'
        valid = {'schema_version': 1, 'status': 'passed', 'checks': [
            {'label': name, 'status': 'passed'} for name in sorted(candidate.NATIVE_VERIFICATION_LABELS)]}
        write_json(path, valid)
        self.assertEqual(len(candidate.validate_verification_results(path)['labels']), 14)
        bad = [[], valid['checks'][:1], valid['checks'][:-1], valid['checks'] + [valid['checks'][0]],
               [valid['checks'][0]] * 14]
        for rows in bad:
            write_json(path, {**valid, 'checks': rows})
            with self.assertRaises(ValueError): candidate.validate_verification_results(path)
        for status in ('failed', 'skipped', 'budget-stop', True):
            changed = copy.deepcopy(valid); changed['checks'][0]['status'] = status
            write_json(path, changed)
            with self.assertRaises(ValueError): candidate.validate_verification_results(path)


    def test_native_browser_semantics_and_exact_binding_are_recomputed(self):
        report = bind_runtime(good_browser_report(), self.bundle, self.source, 'browser')
        path = self.root / 'native-browser-runtime.json'
        write_json(path, report)
        payload = candidate.inventory(self.bundle)
        verify = lambda: candidate.validate_native_runtime(path, self.bundle, payload, self.source, 'browser')
        self.assertEqual(verify()['status'], 'passed')
        changed = copy.deepcopy(report)
        next(row for row in changed['scenarios'] if row.get('media', {}).get('capture', {}).get('video', {}).get('ok') is False)['media']['capture']['video'] = {'ok': True, 'tracks': 1}
        write_json(path, changed)
        with self.assertRaises(ValueError): verify()
        for field in ('chrome_dll_sha256', 'capabilities_sha256', 'verifier_sha256', 'security_helper_sha256'):
            changed = copy.deepcopy(report); changed['binding'][field] = 'f' * 64
            write_json(path, changed)
            with self.subTest(field=field), self.assertRaises(ValueError): verify()


    def test_complete_evidence_requires_each_new_receipt_and_all_67_passkeys(self):
        write_native_integration_evidence(self.root, self.bundle, self.source)
        payload = candidate.inventory(self.bundle)
        verify = lambda: candidate.validate_native_integration(self.root, self.bundle, payload, self.source)
        result = verify()
        self.assertEqual({kind: result[kind + '_compiled_tests']['required_count']
                          for kind in ('passkey', 'browser', 'cookie')}, {'passkey': 67, 'browser': 9, 'cookie': 10})
        for name in ('native-browser-unittests.json', 'native-browser-launcher.json',
                     'native-browser-runtime.json', 'verification-results.json',
                     'native-cookie-unittests.json', 'native-cookie-launcher.json', 'native-cookie-runtime.json'):
            path = self.root / name; original = path.read_bytes(); path.unlink()
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Mandatory'):
                verify()
            path.write_bytes(original)
        summary = self.root / 'native-passkey-unittests.json'
        write_json(summary, {'per_iteration_data': [{name: [{'status': 'SUCCESS'}] for name in candidate.NATIVE_PASSKEY_TESTS}]})
        launcher_path = self.root / 'native-passkey-launcher.json'
        launcher = json.loads(launcher_path.read_text()); launcher['summary_sha256'] = candidate.sha256(summary)
        write_json(launcher_path, launcher)
        with self.assertRaises(ValueError): verify()


if __name__ == '__main__': unittest.main()
