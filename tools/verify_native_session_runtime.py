"""Run the shared five native session scenarios against the exact CI candidate.

The headed browser lives on a private Windows desktop. No input-desktop switch,
existing profile, product admission update, or browser compilation is performed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

try:
    from . import verify_security_runtime as security
    from .fingerprint_subprocess import WindowsJob
    from .verify_native_browser_runtime import safe_environment
except ImportError:
    import verify_security_runtime as security
    from fingerprint_subprocess import WindowsJob
    from verify_native_browser_runtime import safe_environment

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / 'tools/native-session'
PACKAGE = 'shardx-lite/native-session'
CONTRACT = 'shardx-native-session-runtime-v1'
REQUIRED = {
    'TestCandidateNativeSessionLifecycle',
    'TestCandidateNativeExitRespectsBeforeUnloadCancellation',
    'TestCandidateNativeExitWithoutVisibleWindows',
    'TestCandidateNativePendingNewTabSurvivesNormalExit',
    'TestCandidateNativePendingTabSurvivesLastWindowClose',
}


def parse_results(text):
    """A zero exit alone cannot accept skipped, missing, or repeated scenarios."""
    started, passed, package_passes = Counter(), Counter(), 0
    for line in text.splitlines():
        event = json.loads(line)
        if not isinstance(event, dict) or event.get('Package') != PACKAGE:
            raise ValueError('Unexpected native-session event')
        action, name = event.get('Action'), event.get('Test')
        if action in ('skip', 'fail'):
            raise ValueError('Native-session scenarios must pass without skips or retries')
        if name:
            if name not in REQUIRED:
                raise ValueError('Unexpected native-session scenario')
            if package_passes:
                raise ValueError('Native-session event arrived after package completion')
            if action == 'run':
                started[name] += 1
            elif action == 'pass':
                if started[name] != 1:
                    raise ValueError('Native-session completion has no unique start')
                passed[name] += 1
        elif action == 'pass':
            expected = Counter({name: 1 for name in REQUIRED})
            if started != expected or passed != expected:
                raise ValueError('Native-session package completed before its scenarios')
            package_passes += 1
    expected = Counter({name: 1 for name in REQUIRED})
    if started != expected or passed != expected or package_passes != 1:
        raise ValueError('All five native-session scenarios must execute exactly once')
    return sorted(passed)


def verifier_inputs():
    inputs = [*MODULE.rglob('*.go'), MODULE / 'go.mod', MODULE / 'go.sum',
              Path(__file__), Path(security.__file__), REPO / 'tools/fingerprint_subprocess.py',
              REPO / 'tools/verify_native_browser_runtime.py', REPO / 'tools/security_build_record.py']
    return {path.relative_to(REPO).as_posix(): security.digest(security.plain(path)) for path in sorted(inputs)}


def validate_build_linkage(build, source_record, receipt, binding):
    """Reject borrowed metadata before launching an otherwise functional DLL."""
    if not all(isinstance(value, dict) for value in (build, source_record, receipt)):
        raise ValueError('Native-session build records must be objects')
    if (type(build.get('schema_version')) is not int or build['schema_version'] != 1
            or build.get('build_variant') != 'fingerprint-modes-win156'
            or build.get('chromium_version') != security.VERSION
            or not re.fullmatch('[a-f0-9]{40}', str(source_record.get('source_commit', '')))
            or source_record['source_commit'] != build.get('source_commit')
            or not re.fullmatch('[1-9][0-9]*', str(source_record.get('run_id', '')))
            or str(source_record['run_id']) != str(build.get('run_id'))
            or not isinstance(source_record.get('gn'), dict)
            or source_record['gn'].get('sha256') != build.get('gn_args_sha256')
            or not re.fullmatch('[a-f0-9]{64}', str(build.get('gn_args_sha256', '')))
            or any(not re.fullmatch('[a-f0-9]{64}', str(receipt.get(key, '')))
                   or receipt[key] != build.get(key) for key in ('patch_sha256', 'preparation_sha256'))):
        raise ValueError('Native-session candidate source/build records differ')
    files = build.get('files')
    required = {'chrome.exe': 'browser_sha256', 'chrome.dll': 'chrome_dll_sha256',
                'native-browser-capabilities.json': 'capabilities_sha256',
                'google-services-source.json': 'source_receipt_sha256'}
    if not isinstance(files, dict) or any(files.get(name) != binding[key] for name, key in required.items()):
        raise ValueError('Native-session payload differs from its immutable build inventory')


def input_identity(browser, source_record):
    browser = security.plain(browser)
    files = {'browser_sha256': browser,
             'chrome_dll_sha256': browser.with_name('chrome.dll'),
             'capabilities_sha256': browser.with_name('native-browser-capabilities.json'),
             'source_receipt_sha256': browser.with_name('google-services-source.json'),
             'build_record_sha256': browser.with_name('google-services-build.json')}
    binding = {key: security.digest(security.plain(path)) for key, path in files.items()}
    source = security.source_identity(source_record, security.build_record.recipe(REPO))
    if binding['source_receipt_sha256'] != source['source_receipt_sha256']:
        raise ValueError('Candidate receipt differs from the exact build source record')
    if binding['capabilities_sha256'] != security.digest(REPO / 'build/native-browser-capabilities.json'):
        raise ValueError('Candidate session contract differs from this checkout')
    validate_build_linkage(
        json.loads(browser.with_name('google-services-build.json').read_text(encoding='utf-8-sig')),
        json.loads(source_record.read_text(encoding='utf-8-sig')),
        json.loads(browser.with_name('google-services-source.json').read_text(encoding='utf-8-sig')), binding)
    binding['verifier_inputs'] = verifier_inputs()
    return source, binding


def run_private_desktop(command, *, environment, cwd, log, timeout=180):
    """Gate the runner into an owned job before it can create browser children."""
    import ctypes as c
    from ctypes import wintypes as w
    user = c.WinDLL('user32', use_last_error=True)
    for name, args, result in (
        ('CreateDesktopW', [w.LPCWSTR, w.LPCWSTR, c.c_void_p, w.DWORD, w.DWORD, c.c_void_p], w.HANDLE),
        ('OpenInputDesktop', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ('CloseDesktop', [w.HANDLE], w.BOOL),
        ('GetUserObjectInformationW', [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
    ):
        function = getattr(user, name)
        function.argtypes, function.restype = args, result

    def input_name():
        handle = user.OpenInputDesktop(0, False, 1)
        if not handle:
            raise c.WinError(c.get_last_error())
        try:
            value, length = c.create_unicode_buffer(256), w.DWORD()
            if not user.GetUserObjectInformationW(handle, 2, value, c.sizeof(value), c.byref(length)):
                raise c.WinError(c.get_last_error())
            return value.value
        finally:
            user.CloseDesktop(handle)

    name = 'ShardXSessionAcceptance-' + uuid.uuid4().hex
    desktop = user.CreateDesktopW(name, None, None, 0, 0x10000000, None)
    if not desktop:
        raise c.WinError(c.get_last_error())
    job, process = None, None
    result = {'private_desktop': name, 'switch_desktop_called': False,
              'timed_out': False, 'exit_code': None, 'cleanup_errors': []}
    try:
        result['input_desktop_before'] = input_name()
        job = WindowsJob()
        startup = subprocess.STARTUPINFO()
        startup.lpDesktop = 'WinSta0\\' + name
        gate = ('import subprocess,sys;sys.exit(subprocess.call(sys.argv[1:],'
                'stdin=subprocess.DEVNULL) if sys.stdin.buffer.read(1)==b"1" else 125)')
        with log.open('xb') as output:
            process = subprocess.Popen([sys.executable, '-c', gate, *command],
                cwd=cwd, env=environment, startupinfo=startup,
                creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.PIPE,
                stdout=output, stderr=subprocess.STDOUT)
            job.assign(process)
            process.stdin.write(b'1')
            process.stdin.close()
            try:
                result['exit_code'] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result['timed_out'] = True
        if process.poll() is not None:
            deadline = time.monotonic() + 5
            while True:
                info = job.Accounting()
                if not job.kernel.QueryInformationJobObject(job.handle, 1, job.c.byref(info), job.c.sizeof(info), None):
                    raise job.c.WinError(job.c.get_last_error())
                result['processes_before_cleanup'] = info.active
                if info.active == 0 or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
    finally:
        if job:
            try:
                job.terminate()
            except OSError as error:
                result['cleanup_errors'].append(type(error).__name__)
            try:
                job.close()
            except OSError as error:
                result['cleanup_errors'].append(type(error).__name__)
        if process:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.wait(timeout=15)
        result['input_desktop_after'] = input_name()
        result['input_desktop_unchanged'] = (result.get('input_desktop_before') ==
                                           result['input_desktop_after'] != name)
        if not user.CloseDesktop(desktop):
            result['cleanup_errors'].append('CloseDesktopError')
    return result


def execution_passed(result):
    return (type(result.get('exit_code')) is int and result['exit_code'] == 0
            and result.get('timed_out') is False
            and type(result.get('processes_before_cleanup')) is int and result['processes_before_cleanup'] == 0
            and result.get('cleanup_errors') == []
            and result.get('switch_desktop_called') is False
            and result.get('input_desktop_unchanged') is True)


def validate_existing(report_path, events_path, bundle, payload, expected_source):
    """Independently re-read archived events and bind them to the candidate."""
    for path in (report_path, events_path):
        if security.plain(path).stat().st_size > 16 * 1024**2:
            raise ValueError('Oversized native-session evidence')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if not isinstance(report, dict):
        raise ValueError('Native-session report must be an object')
    tests = parse_results(events_path.read_text(encoding='utf-8'))
    if (type(report.get('schema_version')) is not int or report['schema_version'] != 1
            or report.get('contract') != CONTRACT or report.get('status') != 'passed'
            or report.get('source') != expected_source
            or report.get('required_tests') != sorted(REQUIRED)
            or report.get('executed_tests') != tests
            or report.get('events_sha256') != security.digest(events_path)
            or report.get('bindings_unchanged') is not True
            or report.get('owned_profiles_removed') is not True
            or not isinstance(report.get('execution'), dict)
            or not execution_passed(report['execution'])):
        raise ValueError('Native-session evidence does not prove all five scenarios')
    execution = report['execution']
    if (not isinstance(execution.get('private_desktop'), str)
            or not re.fullmatch('ShardXSessionAcceptance-[a-f0-9]{32}', execution['private_desktop'])
            or not isinstance(execution.get('input_desktop_before'), str)
            or not execution['input_desktop_before']
            or execution.get('input_desktop_after') != execution['input_desktop_before']
            or execution['private_desktop'] == execution['input_desktop_before']):
        raise ValueError('Native-session desktop observation is inconsistent')
    expected = {
        'browser_sha256': payload['chrome.exe'], 'chrome_dll_sha256': payload['chrome.dll'],
        'capabilities_sha256': security.digest(REPO / 'build/native-browser-capabilities.json'),
        'source_receipt_sha256': expected_source['source_receipt_sha256'],
        'build_record_sha256': security.digest(bundle / 'google-services-build.json'),
        'verifier_inputs': verifier_inputs(),
    }
    binding = report.get('binding')
    if (not isinstance(binding, dict) or any(binding.get(key) != value for key, value in expected.items())
            or security.digest(bundle / 'chrome.exe') != payload['chrome.exe']
            or security.digest(bundle / 'chrome.dll') != payload['chrome.dll']
            or security.digest(bundle / 'native-browser-capabilities.json') != expected['capabilities_sha256']
            or any(not re.fullmatch('[a-f0-9]{64}', str(binding.get(key, '')))
                   for key in ('go_sha256', 'test_executable_sha256'))):
        raise ValueError('Native-session candidate or verifier identity differs')
    build = json.loads((bundle / 'google-services-build.json').read_text(encoding='utf-8-sig'))
    try:
        created, started, completed = (datetime.fromisoformat(value.replace('Z', '+00:00')) for value in (
            build['created_at_utc'], report['started_at_utc'], report['completed_at_utc']))
        if any(value.utcoffset() != timezone.utc.utcoffset(value) for value in (created, started, completed)):
            raise ValueError('Timestamps must be UTC')
        if not created <= started <= completed:
            raise ValueError('Native-session evidence predates the immutable candidate')
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('Native-session timestamps are missing') from error
    return {'status': 'passed', 'report_sha256': security.digest(report_path),
            'events_sha256': security.digest(events_path), 'tests': tests, 'binding': binding,
            'producer_identity': 'Producer hashes retained; not assumed equal to the verification host'}


def remove_owned_directory(temporary, report_directory):
    cleanup = security.plain(temporary).resolve()
    if (not cleanup.is_relative_to(report_directory.resolve())
            or not cleanup.name.startswith('security-runtime-native-session-')):
        raise ValueError('Owned session cleanup escaped its report directory')
    shutil.rmtree(cleanup)
    return not temporary.exists()


def host_preflight(go_name, destination):
    """Only probe runner/desktop support; this cannot substitute for five cases."""
    report = {'schema_version': 1, 'contract': 'shardx-native-session-host-preflight-v1',
              'status': 'failed', 'browser_acceptance': False,
              'qualification': 'Runner capability only; exact candidate lifecycle acceptance remains mandatory.',
              'started_at_utc': datetime.now(timezone.utc).isoformat()}
    temporary = None
    try:
        if os.name != 'nt':
            raise ValueError('Native session host preflight requires Windows')
        path = shutil.which(go_name)
        if not path:
            raise ValueError('The pinned Go toolchain is required')
        go = security.plain(Path(path))
        report['go_sha256'] = security.digest(go)
        temporary = Path(tempfile.mkdtemp(prefix='security-runtime-native-session-host-', dir=destination.parent))
        environment = safe_environment(temporary)
        environment.update(GOTOOLCHAIN='local', GOFLAGS='', GOWORK='off')
        log = destination.with_suffix('.host.log')
        report['execution'] = run_private_desktop([str(go), 'version'],
            environment=environment, cwd=MODULE, log=log, timeout=30)
        report['go_version'] = log.read_text(encoding='utf-8').strip()
        report['log_sha256'] = security.digest(log)
        report['tool_unchanged'] = security.digest(go) == report['go_sha256']
        if (not execution_passed(report['execution']) or not report['tool_unchanged']
                or report['go_version'] != 'go version go1.27.2 windows/amd64'):
            raise RuntimeError('Native session runner or private desktop preflight failed')
        report['status'] = 'passed'
    except Exception as error:
        report['error_type'] = type(error).__name__
    finally:
        if temporary:
            try:
                report['owned_directory_removed'] = remove_owned_directory(temporary, destination.parent)
                if not report['owned_directory_removed']:
                    raise ValueError('Owned host-preflight directory remains')
            except (OSError, ValueError) as error:
                report.update(status='failed', cleanup_error_type=type(error).__name__)
        report['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
        destination.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'browser_acceptance': False,
                      'failed': [] if report['status'] == 'passed' else ['native_session_host_preflight']}))
    return 0 if report['status'] == 'passed' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path)
    parser.add_argument('--source-record', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--go', default='go')
    parser.add_argument('--host-preflight', action='store_true',
                        help='Probe private desktop and Go only; never grants candidate acceptance')
    args = parser.parse_args(argv)
    if args.host_preflight:
        if args.browser or args.source_record:
            parser.error('Host preflight does not accept browser artifacts')
    elif not args.browser or not args.source_record:
        parser.error('Candidate acceptance requires --browser and --source-record')
    destination = security.plain(args.report)
    if destination.exists():
        parser.error('Use a new report path; previous evidence is retained')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if args.host_preflight:
        return host_preflight(args.go, destination)
    report = {'schema_version': 1, 'contract': CONTRACT,
              'status': 'failed', 'required_tests': sorted(REQUIRED),
              'started_at_utc': datetime.now(timezone.utc).isoformat()}
    temporary = None
    try:
        if os.name != 'nt':
            raise ValueError('Native session acceptance requires Windows')
        go = shutil.which(args.go)
        if not go:
            raise ValueError('The pinned Go toolchain is required')
        go = security.plain(Path(go))
        browser = security.plain(args.browser)
        report['source'], report['binding'] = input_identity(browser, args.source_record)
        report['binding']['go_sha256'] = security.digest(go)
        temporary = Path(tempfile.mkdtemp(prefix='security-runtime-native-session-', dir=destination.parent))
        environment = safe_environment(temporary)
        environment.update(GOTOOLCHAIN='local', GOFLAGS='', GOWORK='off',
            CHROMIX_LITE_SESSION_CANDIDATE=str(browser),
            CHROMIX_LITE_SESSION_CANDIDATE_DLL_SHA256=report['binding']['chrome_dll_sha256'])
        executable = temporary / 'native-session.test.exe'
        build_log = destination.with_suffix('.build.log')
        with build_log.open('xb') as output:
            verified = subprocess.run([str(go), 'mod', 'verify'], cwd=MODULE, env=environment,
                stdout=output, stderr=subprocess.STDOUT, timeout=120)
            if verified.returncode != 0:
                raise RuntimeError('Native session dependency cache differs from its lock')
            built = subprocess.run([str(go), 'test', '-mod=readonly', '-c', '-o', str(executable), '.'],
                cwd=MODULE, env=environment, stdout=output, stderr=subprocess.STDOUT, timeout=180)
        if built.returncode != 0:
            raise RuntimeError('Native session verifier compilation failed')
        report['binding']['test_executable_sha256'] = security.digest(executable)
        log = destination.with_suffix('.events.jsonl')
        report['execution'] = run_private_desktop(
            [str(go), 'tool', 'test2json', '-t', '-p', PACKAGE, str(executable),
             '-test.v=test2json', '-test.count=1', '-test.timeout=150s'],
            environment=environment, cwd=MODULE, log=log)
        report['events_sha256'] = security.digest(log)
        if not execution_passed(report['execution']):
            raise RuntimeError('Native session execution or owned cleanup failed')
        report['executed_tests'] = parse_results(log.read_text(encoding='utf-8'))
        source_after, inputs_after = input_identity(browser, args.source_record)
        expected = {key: value for key, value in report['binding'].items()
                    if key not in ('go_sha256', 'test_executable_sha256')}
        report['bindings_unchanged'] = (source_after == report['source'] and inputs_after == expected
            and security.digest(go) == report['binding']['go_sha256']
            and security.digest(executable) == report['binding']['test_executable_sha256'])
        if not report['bindings_unchanged']:
            raise ValueError('Native session inputs changed during acceptance')
        report['status'] = 'passed'
    except Exception as error:
        report['error_type'] = type(error).__name__
    finally:
        if temporary:
            try:
                report['owned_profiles_removed'] = remove_owned_directory(temporary, destination.parent)
            except (OSError, ValueError) as error:
                report['status'] = 'failed'
                report['cleanup_error_type'] = type(error).__name__
        report['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
        destination.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if report['status'] == 'passed':
        try:
            validate_existing(destination, destination.with_suffix('.events.jsonl'), browser.parent,
                {'chrome.exe': report['binding']['browser_sha256'],
                 'chrome.dll': report['binding']['chrome_dll_sha256']}, report['source'])
        except Exception as error:
            report.update(status='failed', error_type=type(error).__name__)
            destination.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'executed_tests': len(report.get('executed_tests', [])),
                      'failed': [] if report['status'] == 'passed' else ['native_session_runtime']}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
