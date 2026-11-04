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
import stat
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


class GoToolResolutionError(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def resolve_go_tool(name, diagnostic):
    """Resolve host-tool aliases without changing payload/source path policy.

    setup-go puts its tool-cache installation on PATH; hosted tool-cache roots
    may be junctions. Execute and hash the canonical file instead of treating
    that installation alias as a mutable candidate input.
    """
    found = shutil.which(name)
    if not found:
        raise GoToolResolutionError('go_not_found')
    selected = Path(found).absolute()
    diagnostic['go_discovery_path'] = str(selected)
    try:
        canonical = selected.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise GoToolResolutionError('go_path_resolution_failed') from error
    diagnostic['go_canonical_path'] = str(canonical)
    diagnostic['go_alias_resolved'] = selected != canonical
    try:
        # Keep the final physical file strict. This exception applies only to
        # discovering the host Go installation, never to a browser or source.
        security.plain(canonical)
        info = canonical.stat()
        if canonical.suffix.lower() != '.exe' or not stat.S_ISREG(info.st_mode):
            raise GoToolResolutionError('go_not_native_executable')
        with canonical.open('rb') as stream:
            dos = stream.read(64)
            if len(dos) != 64 or dos[:2] != b'MZ':
                raise GoToolResolutionError('go_invalid_pe')
            offset = int.from_bytes(dos[60:64], 'little')
            if not 64 <= offset <= info.st_size - 24:
                raise GoToolResolutionError('go_invalid_pe')
            stream.seek(offset)
            pe = stream.read(24)
            flags = int.from_bytes(pe[22:24], 'little')
            if (pe[:4] != b'PE\0\0' or int.from_bytes(pe[4:6], 'little') != 0x8664
                    or not flags & 0x0002 or flags & 0x2000):
                raise GoToolResolutionError('go_invalid_pe')
    except GoToolResolutionError:
        raise
    except ValueError as error:
        raise GoToolResolutionError('go_final_path_not_plain') from error
    except OSError as error:
        raise GoToolResolutionError('go_file_unreadable') from error
    return canonical


def record_failure(report, error, phase):
    report['error_type'] = type(error).__name__
    report['failure_phase'] = phase
    report['failure_reason'] = error.reason if isinstance(error, GoToolResolutionError) else 'operation_failed'
    code = getattr(error, 'winerror', None) or getattr(error, 'errno', None)
    if isinstance(code, int):
        report['os_error_code'] = code


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
    """Gate the runner into an owned job on its actual window station."""
    import ctypes as c
    from ctypes import wintypes as w
    user = c.WinDLL('user32', use_last_error=True)
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.ProcessIdToSessionId.argtypes = [w.DWORD, c.POINTER(w.DWORD)]
    kernel.ProcessIdToSessionId.restype = w.BOOL
    for name, args, result in (
        ('CreateDesktopW', [w.LPCWSTR, w.LPCWSTR, c.c_void_p, w.DWORD, w.DWORD, c.c_void_p], w.HANDLE),
        ('GetProcessWindowStation', [], w.HANDLE),
        ('OpenInputDesktop', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ('CloseDesktop', [w.HANDLE], w.BOOL),
        ('GetUserObjectInformationW', [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
    ):
        function = getattr(user, name)
        function.argtypes, function.restype = args, result

    class ObjectFlags(c.Structure):
        _fields_ = [('inherit', w.BOOL), ('reserved', w.BOOL), ('flags', w.DWORD)]

    def object_name(handle):
        value, length = c.create_unicode_buffer(256), w.DWORD()
        if not user.GetUserObjectInformationW(handle, 2, value, c.sizeof(value), c.byref(length)):
            raise c.WinError(c.get_last_error())
        return value.value

    def station_context(suffix):
        session_id = w.DWORD()
        if not kernel.ProcessIdToSessionId(os.getpid(), c.byref(session_id)):
            raise c.WinError(c.get_last_error())
        handle = user.GetProcessWindowStation()  # Borrowed handle; never close it.
        if not handle:
            raise c.WinError(c.get_last_error())
        flags, length = ObjectFlags(), w.DWORD()
        if not user.GetUserObjectInformationW(handle, 1, c.byref(flags), c.sizeof(flags), c.byref(length)):
            raise c.WinError(c.get_last_error())
        result['session_id' + suffix] = session_id.value
        result['window_station' + suffix] = object_name(handle)
        result['window_station_visible' + suffix] = bool(flags.flags & 1)  # WSF_VISIBLE

    def input_name(suffix):
        handle = user.OpenInputDesktop(0, False, 1)
        if not handle:
            result['input_desktop' + suffix + '_error'] = c.get_last_error()
            return None
        try:
            return object_name(handle)
        finally:
            user.CloseDesktop(handle)

    name = 'ShardXSessionAcceptance-' + uuid.uuid4().hex
    result = {'private_desktop': name, 'switch_desktop_called': False,
              'timed_out': False, 'exit_code': None, 'cleanup_errors': []}
    desktop, job, process = None, None, None
    phase = 'inspect_window_station'
    try:
        station_context('_before')
        phase = 'read_input_desktop_before'
        result['input_desktop_before'] = input_name('_before')
        if result['input_desktop_before'] is None:
            if not service_station_context(result):
                raise c.WinError(result['input_desktop_before_error'])
            result['input_desktop_check'] = 'service_session0_no_input'
        else:
            result['input_desktop_check'] = 'observed'
        phase = 'create_private_desktop'
        desktop = user.CreateDesktopW(name, None, None, 0, 0x10000000, None)
        if not desktop:
            raise c.WinError(c.get_last_error())
        result['private_desktop_observed'] = object_name(desktop)
        result['startup_desktop'] = result['window_station_before'] + '\\' + name
        if result['private_desktop_observed'] != name:
            raise ValueError('Private desktop identity differs')
        job = WindowsJob()
        startup = subprocess.STARTUPINFO()
        startup.lpDesktop = result['startup_desktop']
        gate = ('import subprocess,sys;sys.exit(subprocess.call(sys.argv[1:],'
                'stdin=subprocess.DEVNULL) if sys.stdin.buffer.read(1)==b"1" else 125)')
        phase = 'start_owned_runner'
        with log.open('xb') as output:
            process = subprocess.Popen([sys.executable, '-c', gate, *command],
                cwd=cwd, env=environment, startupinfo=startup,
                creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.PIPE,
                stdout=output, stderr=subprocess.STDOUT)
            job.assign(process)
            process.stdin.write(b'1')
            process.stdin.close()
            phase = 'await_owned_runner'
            try:
                result['exit_code'] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result['timed_out'] = True
        if process.poll() is not None:
            phase = 'inspect_owned_processes'
            deadline = time.monotonic() + 5
            while True:
                info = job.Accounting()
                if not job.kernel.QueryInformationJobObject(job.handle, 1, job.c.byref(info), job.c.sizeof(info), None):
                    raise job.c.WinError(job.c.get_last_error())
                result['processes_before_cleanup'] = info.active
                if info.active == 0 or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
    except Exception as error:
        record_failure(result, error, phase)
    finally:
        if job:
            for cleanup in (job.terminate, job.close):
                try:
                    cleanup()
                except OSError as error:
                    result['cleanup_errors'].append(type(error).__name__)
        if process:
            try:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
                process.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired) as error:
                result['cleanup_errors'].append(type(error).__name__)
        try:
            station_context('_after')
            result['input_desktop_after'] = input_name('_after')
            before, after = result.get('input_desktop_before'), result['input_desktop_after']
            result['input_desktop_unchanged'] = before == after != name if before is not None and after is not None else None
        except OSError as error:
            result['cleanup_errors'].append(type(error).__name__)
            result['desktop_postcheck_error'] = getattr(error, 'winerror', None) or getattr(error, 'errno', None)
        finally:
            if desktop and not user.CloseDesktop(desktop):
                result['cleanup_errors'].append('CloseDesktopError')
                result['desktop_close_error'] = c.get_last_error()
    return result


def service_station_context(result):
    """Only an evidenced noninteractive service station has no input desktop."""
    return (type(result.get('session_id_before')) is int and result['session_id_before'] == 0
            and result.get('window_station_visible_before') is False
            and isinstance(result.get('window_station_before'), str)
            and re.fullmatch(r'Service-0x[0-9a-f]+-[0-9a-f]+\$', result['window_station_before'], re.I) is not None)


def desktop_observation_valid(result):
    private = result.get('private_desktop')
    station = result.get('window_station_before')
    if (not isinstance(private, str) or not re.fullmatch('ShardXSessionAcceptance-[a-f0-9]{32}', private)
            or result.get('private_desktop_observed') != private
            or not isinstance(station, str) or not station or '\\' in station
            or result.get('window_station_after') != station
            or result.get('startup_desktop') != station + '\\' + private
            or type(result.get('session_id_before')) is not int or result['session_id_before'] < 0
            or type(result.get('session_id_after')) is not int or result['session_id_after'] != result['session_id_before']
            or type(result.get('window_station_visible_before')) is not bool
            or result.get('window_station_visible_after') is not result['window_station_visible_before']):
        return False
    if result.get('input_desktop_check') == 'observed':
        before = result.get('input_desktop_before')
        return (isinstance(before, str) and bool(before) and before != private
                and result.get('input_desktop_after') == before
                and result.get('input_desktop_unchanged') is True
                and 'input_desktop_before_error' not in result and 'input_desktop_after_error' not in result)
    if result.get('input_desktop_check') == 'service_session0_no_input':
        first = result.get('input_desktop_before_error')
        last = result.get('input_desktop_after_error')
        return (service_station_context(result) and result.get('input_desktop_before') is None
                and result.get('input_desktop_after') is None and result.get('input_desktop_unchanged') is None
                and type(first) is int and first > 0 and type(last) is int and last == first)
    return False


def execution_passed(result):
    return (type(result.get('exit_code')) is int and result['exit_code'] == 0
            and result.get('timed_out') is False
            and type(result.get('processes_before_cleanup')) is int and result['processes_before_cleanup'] == 0
            and result.get('cleanup_errors') == []
            and result.get('switch_desktop_called') is False
            and 'error_type' not in result and desktop_observation_valid(result))


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
    phase = 'platform'
    try:
        report['host_os'] = os.name
        if os.name != 'nt':
            raise ValueError('Native session host preflight requires Windows')
        phase = 'resolve_go_tool'
        go = resolve_go_tool(go_name, report)
        phase = 'hash_go_tool'
        report['go_sha256'] = security.digest(go)
        phase = 'prepare_owned_directory'
        temporary = Path(tempfile.mkdtemp(prefix='security-runtime-native-session-host-', dir=destination.parent))
        environment = safe_environment(temporary)
        environment.update(GOTOOLCHAIN='local', GOFLAGS='', GOWORK='off')
        log = destination.with_suffix('.host.log')
        phase = 'private_desktop'
        report['execution'] = run_private_desktop([str(go), 'version'],
            environment=environment, cwd=MODULE, log=log, timeout=30)
        phase = 'verify_go_version_and_cleanup'
        if not execution_passed(report['execution']):
            raise RuntimeError('Native session private desktop preflight failed')
        report['go_version'] = log.read_text(encoding='utf-8').strip()
        report['log_sha256'] = security.digest(log)
        report['tool_unchanged'] = security.digest(go) == report['go_sha256']
        if (not execution_passed(report['execution']) or not report['tool_unchanged']
                or report['go_version'] != 'go version go1.27.2 windows/amd64'):
            raise RuntimeError('Native session runner or private desktop preflight failed')
        report['status'] = 'passed'
    except Exception as error:
        record_failure(report, error, phase)
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
    phase = 'platform'
    try:
        if os.name != 'nt':
            raise ValueError('Native session acceptance requires Windows')
        phase = 'resolve_go_tool'
        go = resolve_go_tool(args.go, report)
        phase = 'candidate_input_identity'
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
        phase = 'verify_dependencies_and_compile_runner'
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
        phase = 'private_desktop_scenarios'
        report['execution'] = run_private_desktop(
            [str(go), 'tool', 'test2json', '-t', '-p', PACKAGE, str(executable),
             '-test.v=test2json', '-test.count=1', '-test.timeout=150s'],
            environment=environment, cwd=MODULE, log=log)
        phase = 'validate_events_and_bindings'
        if not execution_passed(report['execution']):
            raise RuntimeError('Native session execution or owned cleanup failed')
        report['events_sha256'] = security.digest(log)
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
        record_failure(report, error, phase)
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
            report['status'] = 'failed'
            record_failure(report, error, 'validate_archived_evidence')
            destination.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'executed_tests': len(report.get('executed_tests', [])),
                      'failed': [] if report['status'] == 'passed' else ['native_session_runtime']}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
