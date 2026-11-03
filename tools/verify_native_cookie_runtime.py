"""Exercise native Cookie bootstrap in the actual browser instance.

Only synthetic, owned inputs and loopback HTTP are used. CDP observes state and
closes the browser; it never writes Cookies. Session restore is an explicit
fixture in one case, not a production preference override.
"""
from __future__ import annotations

import argparse
from http.cookies import SimpleCookie
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import uuid

try:
    from . import verify_security_runtime as security
    from . import fingerprint_subprocess as process_helper
except ImportError:
    import verify_security_runtime as security
    import fingerprint_subprocess as process_helper

REPO = Path(__file__).resolve().parents[1]
CONTRACT = 'shardx-native-cookie-v1'
ENVIRONMENT_ID = 'a7423600-295b-4a29-93d6-b5dcd6824571'
HEX = re.compile(r'[a-f0-9]{64}\Z')
FAILURES = {
    'bad_hash': 'envelope_digest_mismatch', 'bad_json': 'invalid_envelope',
    'duplicate_conflict': 'conflicting_cookie_key', 'unsupported_partition': 'invalid_cookie',
    'quota_readback': 'readback_mismatch', 'backend_rejection': 'cookie_set_rejected',
    'deadline_expired': 'timeout',
}
SUCCESS = {'first_http': (2, 0, 0), 'duplicate_identical': (1, 1, 0),
           'expired_delete': (1, 0, 1), 'about_blank': (2, 0, 0),
           'no_startup_window': (2, 0, 0),
           'restored_tabs': (2, 0, 0)}
CASE_NAMES = (*SUCCESS, 'restart_pref5', 'completed_token', *FAILURES, 'receipt_unwritable')
RECEIPT_FIELDS = ('schemaVersion', 'contract', 'environmentId', 'requestId', 'token',
    'envelopeSha256', 'profileDirectory', 'browserPid', 'status', 'sourceSha256', 'deadlineUnixMs',
    'cookieCount', 'uniqueCookieCount', 'acceptedCookieCount', 'verifiedCookieCount',
    'expiredCookieCount', 'duplicateCookieCount', 'attemptedCookieCount',
    'partialApplied', 'writeOutcomeUnknown', 'rollbackPerformed', 'errorCode')


def now_ms():
    return int(time.time() * 1000)


def integer(value, low=0, high=10000):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and int(value) == value and low <= value <= high)


def safe_error(error):
    # Never put exception payloads or Cookie values into a report.
    code = re.search(r'net::(ERR_[A-Z_]+)', str(error))
    return {'type': type(error).__name__, 'network_error': code[1] if code else None}


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def audit_environment(previous):
    environment = {key: value for key, value in previous.items() if not any(
        marker in key.upper() for marker in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'CREDENTIAL'))}
    bypass = ['127.0.0.1', 'localhost', '::1', *environment.get('NO_PROXY', '').split(',')]
    environment['NO_PROXY'] = ','.join(dict.fromkeys(value.strip() for value in bypass if value.strip()))
    environment['no_proxy'] = environment['NO_PROXY']
    return environment


def wait_until(callback, seconds, alive=None):
    end = time.monotonic() + seconds
    while True:
        value = callback()
        if value is not None:
            return value
        if alive and not alive():
            raise RuntimeError('Owned browser exited before the expected observation')
        if time.monotonic() >= end:
            raise TimeoutError('Owned native Cookie observation timed out')
        time.sleep(0.05)


def make_profile(path, startup=5, urls=()):
    security.plain(path).mkdir()  # Never accept an existing, unowned profile here.
    (path / 'Default').mkdir()
    value = {'session': {'restore_on_startup': startup}}
    if urls:
        value['session']['startup_urls'] = list(urls)
    (path / 'Default/Preferences').write_bytes(json_bytes(value))


def preference(path):
    try:
        return json.loads((path / 'Default/Preferences').read_text())['session']['restore_on_startup']
    except (OSError, ValueError, KeyError, TypeError):
        return None


def native_startup_preferences(native):
    """Observe effective native preferences without writing protected hashes."""
    page = native.connection.contexts[0].new_page()
    try:
        page.goto('chrome://settings/onStartup', wait_until='domcontentloaded', timeout=15000)
        page.wait_for_function("typeof chrome?.settingsPrivate?.getPref === 'function'", timeout=10000)
        prefs = page.evaluate(security.READ_PREFS, ['session.restore_on_startup', 'session.startup_urls'])
        values = prefs.get('values', {})
        mode = values.get('session.restore_on_startup', {}).get('pref', {}).get('value')
        stored_urls = values.get('session.startup_urls', {}).get('pref', {}).get('value')
        if prefs.get('available') is not True or mode not in (1, 4, 5):
            raise ValueError('Native startup preference observation failed')
        return {'restore_on_startup': mode, 'startup_urls': stored_urls, 'native_api': True}
    finally:
        page.close()


def make_stage(root, data_root, name, cookies, *, token=None, original_name=None,
               bad_hash=False, bad_json=False, expired_deadline=False):
    source = root / 'inputs' / ((original_name or name) + '.json')
    source.parent.mkdir(exist_ok=True)
    raw = json_bytes(cookies)
    if source.exists():
        if source.read_bytes() != raw:
            raise ValueError('A synthetic original input changed unexpectedly')
    else:
        source.write_bytes(raw)
    token, request_id = token or secrets.token_hex(32), str(uuid.uuid4())
    source_sha = hashlib.sha256(raw).hexdigest()
    deadline = now_ms() + (-1000 if expired_deadline else 60000)
    envelope = {'schemaVersion': 1, 'contract': CONTRACT, 'environmentId': ENVIRONMENT_ID,
                'requestId': request_id, 'token': token, 'sourceSha256': source_sha,
                'cookieCount': len(cookies), 'profileDirectory': 'Default',
                'deadlineUnixMs': deadline, 'cookies': cookies}
    body = b'{' if bad_json else json_bytes(envelope)
    request_dir = data_root / 'ShardX Cookie Imports/requests'
    request_dir.mkdir(parents=True, exist_ok=True)
    request = request_dir / (token + '.' + request_id + '.json')
    with request.open('xb') as stream:
        stream.write(body)
    actual = hashlib.sha256(body).hexdigest()
    return {'schemaVersion': 1, 'contract': CONTRACT, 'environmentId': ENVIRONMENT_ID,
            'requestId': request_id, 'token': token, 'sourceSha256': source_sha,
            'cookieCount': len(cookies), 'profileDirectory': 'Default', 'deadlineUnixMs': deadline,
            'envelopeSha256': '0' * 64 if bad_hash else actual, 'requestFileSha256': actual,
            'requestPath': str(request), 'sourcePath': str(source),
            'receiptPath': str(data_root / 'ShardX Cookie Imports/receipts' / (token + '.json'))}


def read_receipt(path):
    try:
        path = security.plain(path)
        if not path.is_file():
            return None
        if path.stat().st_size > 65536:
            raise ValueError('Native receipt is too large')
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Native receipt is not an object')
        return {key: value[key] for key in RECEIPT_FIELDS if key in value}
    except (FileNotFoundError, NotADirectoryError):
        return None


def launch_args(data_root, proxy_port, stage, urls, no_window=False, restore_previous=False):
    args = ['--headless=new', '--enable-automation', '--remote-debugging-address=127.0.0.1',
            '--remote-debugging-port=0', '--user-data-dir=' + str(data_root),
            '--profile-directory=Default', '--no-first-run', '--no-default-browser-check',
            '--shardx-managed-environment=' + ENVIRONMENT_ID, '--shardx-history-retention-days=0',
            '--shardx-history-retention-days=0',
            '--shardx-restore-previous-session=' + str(restore_previous).lower(),
            '--proxy-server=http://127.0.0.1:' + str(proxy_port),
            '--proxy-bypass-list=127.0.0.1;localhost',
            '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost']
    if stage:
        args.extend(['--shardx-cookie-import=' + stage['requestPath'],
                     '--shardx-cookie-import-sha256=' + stage['envelopeSha256']])
    if no_window:
        args.append('--no-startup-window')
    args.extend(urls)
    return args


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        cookies = SimpleCookie()
        cookies.load(self.headers.get('Cookie', ''))
        got = {key: item.value for key, item in cookies.items()}
        with self.server.condition:
            expected = dict(self.server.expected)
            self.server.events.append({
                'case': self.server.case, 'path': self.path, 'nonce': self.server.nonce,
                'cookie_names': sorted(key for key in got if key.startswith('SX_')),
                'cookie_matches': {key: (key not in got if value is None else got.get(key) == value)
                                   for key, value in expected.items()}})
            self.server.condition.notify_all()
        data = ('<!doctype html><title>Owned Cookie fixture</title><p>' + self.server.nonce + '</p>').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Fixture-Nonce', self.server.nonce)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


class Fixtures:
    def __init__(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.condition = threading.Condition()
        self.server.events, self.server.expected, self.server.case = [], {}, 'preflight'
        self.server.nonce = secrets.token_hex(24)
        self.proxy = ThreadingHTTPServer(('127.0.0.1', 0), security.DenyProxy)
        self.proxy.denied = []
        self.threads = []
        for server in (self.server, self.proxy):
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start(); self.threads.append(thread)
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)

    def select(self, name, expected):
        with self.server.condition:
            self.server.case, self.server.expected = name, dict(expected)

    def events(self, name):
        with self.server.condition:
            return [dict(row) for row in self.server.events if row['case'] == name]

    def wait_paths(self, name, paths, seconds=20):
        def observe():
            seen = {row['path'] for row in self.events(name)}
            return True if set(paths) <= seen else None
        return wait_until(observe, seconds)

    def preflight(self):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('GET', '/health')
            response = connection.getresponse()
            body = response.read()
            return response.status == 200 and self.server.nonce.encode() in body
        finally:
            connection.close()

    def close(self):
        for server in (self.server, self.proxy):
            server.shutdown(); server.server_close()
        for thread in self.threads:
            thread.join(timeout=5)
        return not any(thread.is_alive() for thread in self.threads)


class NativeProcess:
    """A non-browser supervisor gates the one Chrome process into an owned job."""
    def __init__(self, browser, args, root, name, environment, playwright):
        import psutil
        self.playwright, self.browser, self.data_root = playwright, browser, None
        self.connection, self.cdp, self.closed = None, None, False
        data_dirs = [arg.removeprefix('--user-data-dir=') for arg in args if arg.startswith('--user-data-dir=')]
        if len(data_dirs) != 1:
            raise ValueError('The probe must launch exactly one owned user data directory')
        self.data_root = security.plain(Path(data_dirs[0]))
        if self.data_root == root or not self.data_root.is_relative_to(security.plain(root)):
            raise ValueError('Native Cookie probe data escaped its owned root')
        # This is our stopped fixture, not a live user's profile. A normal
        # Chromium close can leave the old port file behind; never attach to it.
        rendezvous = security.plain(self.data_root / 'DevToolsActivePort')
        if rendezvous.exists():
            if not rendezvous.is_file():
                raise ValueError('DevTools rendezvous is not a regular file')
            rendezvous.unlink()
        self.job, self.descendants = process_helper.WindowsJob(), {}
        logs = root / 'process'
        logs.mkdir(exist_ok=True)
        pid_path = logs / (name + '.pid')
        self.log = (logs / (name + '.log')).open('xb')
        gate = ('import subprocess,sys; from pathlib import Path; '
                'ok=sys.stdin.buffer.read(1)==b"1"; '
                'sys.exit(125) if not ok else None; '
                'p=subprocess.Popen(sys.argv[2:],stdin=subprocess.DEVNULL); '
                'Path(sys.argv[1]).write_text(str(p.pid)); sys.exit(p.wait())')
        self.supervisor = None
        try:
            self.supervisor = subprocess.Popen([sys.executable, '-c', gate, str(pid_path), str(browser), *args],
                env=environment, stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.job.assign(self.supervisor)
            self.supervisor.stdin.write(b'1'); self.supervisor.stdin.close()
            pid = wait_until(lambda: int(pid_path.read_text()) if pid_path.is_file() else None,
                             15, lambda: self.supervisor.poll() is None)
            self.process = psutil.Process(pid)
            if os.path.normcase(self.process.exe()) != os.path.normcase(str(browser)):
                raise ValueError('The native process is not the exact supplied browser')
            self.pid, self.created = pid, self.process.create_time()
            self.process_args = self.process.cmdline()
            self.collect()
        except BaseException:
            try:
                if self.supervisor and self.supervisor.stdin and not self.supervisor.stdin.closed:
                    # Failed job assignment must not leave the non-browser gate
                    # indefinitely waiting for a byte it will never receive.
                    self.supervisor.stdin.close()
                self.job.terminate()
            finally:
                self.job.close()
                if self.supervisor:
                    try:
                        self.supervisor.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.supervisor.kill()  # Only the exact owned gate handle.
                        self.supervisor.wait(timeout=10)
                self.log.close()
            raise

    def collect(self):
        import psutil
        try:
            for process in self.process.children(recursive=True):
                self.descendants[(process.pid, process.create_time())] = process
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    def alive(self):
        self.collect()
        return self.supervisor.poll() is None

    def attach(self, data_root, timeout):
        if self.connection:
            return
        def endpoint():
            file = data_root / 'DevToolsActivePort'
            if not file.exists():
                return None
            if security.plain(file).stat().st_size > 4096:
                raise ValueError('DevTools rendezvous file is invalid')
            lines = file.read_text().splitlines()
            if len(lines) < 2 or not lines[0].isdigit() or not 1 <= int(lines[0]) <= 65535:
                return None
            if not re.fullmatch(r'/devtools/browser/[a-zA-Z0-9-]+', lines[1]):
                raise ValueError('DevTools rendezvous path is invalid')
            # Avoid an HTTP discovery request that a host HTTP_PROXY could
            # redirect. The entire websocket address comes from this owned file.
            return 'ws://127.0.0.1:' + lines[0] + lines[1]
        address = wait_until(endpoint, min(timeout, 30), self.alive)
        self.connection = self.playwright.chromium.connect_over_cdp(address, timeout=30000)
        self.cdp = self.connection.new_browser_cdp_session()
        processes = self.cdp.send('SystemInfo.getProcessInfo')['processInfo']
        browser_pids = [row['id'] for row in processes if row['type'] == 'browser']
        if browser_pids != [self.pid]:
            raise ValueError('CDP is not attached to the one owned native browser')

    def close(self, data_root, timeout):
        import psutil
        if self.closed:
            return self.close_result
        forced = False
        try:
            if self.alive():
                try:
                    self.attach(data_root, timeout)
                    self.cdp.send('Browser.close')
                except Exception:
                    pass  # A normal Browser.close can disconnect before replying.
            try:
                self.supervisor.wait(timeout=20)
            except subprocess.TimeoutExpired:
                forced = True
            if self.descendants:
                psutil.wait_procs(list(self.descendants.values()), timeout=5)
        finally:
            errors = []
            try:
                self.job.terminate()
            except OSError:
                errors.append('owned-job-cleanup-failed')
            self.job.close()
            self.supervisor.wait(timeout=10)
            self.log.close()
            if self.connection:
                try:
                    self.connection.close()
                except Exception:
                    pass
            self.closed = True
            self.close_result = {'normal': not forced and self.supervisor.returncode == 0,
                                 'forced_browser_stop': forced, 'exit_code': self.supervisor.returncode,
                                 'cleanup_errors': errors}
        return self.close_result


def receipt_base(receipt, request):
    return (isinstance(receipt, dict) and type(receipt.get('schemaVersion')) is int and receipt['schemaVersion'] == 1
            and receipt.get('contract') == CONTRACT
            and all(receipt.get(key) == request.get(key)
                    for key in ('environmentId', 'token', 'profileDirectory'))
            and integer(receipt.get('browserPid'), 1, 0xffffffff))


def completed_counts(receipt, request, unique, duplicates, expired):
    return (receipt.get('status') == 'completed'
            and receipt.get('sourceSha256') == request.get('sourceSha256')
            and all(integer(receipt.get(key)) for key in ('cookieCount', 'uniqueCookieCount',
                'attemptedCookieCount', 'acceptedCookieCount', 'verifiedCookieCount',
                'expiredCookieCount', 'duplicateCookieCount'))
            and integer(receipt.get('cookieCount')) and receipt['cookieCount'] == request['cookieCount']
            and request['cookieCount'] == unique + duplicates
            and receipt.get('uniqueCookieCount') == unique
            and receipt.get('attemptedCookieCount') == receipt.get('acceptedCookieCount') == unique
            and receipt.get('verifiedCookieCount') == unique - expired
            and receipt.get('expiredCookieCount') == expired
            and receipt.get('duplicateCookieCount') == duplicates
            and all(receipt.get(key) is False for key in
                    ('partialApplied', 'writeOutcomeUnknown', 'rollbackPerformed')))


def assess(report):
    checks = []
    def add(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})
    add('schema', type(report.get('schema_version')) is int and report['schema_version'] == 1 and report.get('contract') == CONTRACT)
    binding, source = report.get('binding', {}), report.get('source', {})
    required_hashes = ('browser_sha256', 'chrome_dll_sha256', 'capabilities_sha256',
        'source_receipt_sha256', 'verifier_sha256', 'security_helper_sha256',
        'process_helper_sha256', 'recipe_tool_sha256', 'requirements_sha256', 'python_sha256')
    add('exact_binding', all(HEX.fullmatch(str(binding.get(key, ''))) for key in required_hashes)
        and all(HEX.fullmatch(str(source.get(key, ''))) for key in
                ('record_sha256', 'recipe_sha256', 'source_receipt_sha256', 'gn_sha256'))
        and source.get('source_receipt_sha256') == binding.get('source_receipt_sha256'))
    add('fixture_preflight', report.get('fixture_preflight') is True)
    network = report.get('network', {})
    add('no_external_forwarding', integer(network.get('external_forwarded_requests'), 0, 0)
        and integer(network.get('proxy_port'), 1, 65535)
        and re.fullmatch(r'http://127\.0\.0\.1:[0-9]+', str(network.get('origin', ''))) is not None)
    rows = report.get('cases', [])
    names = [row.get('name') for row in rows if isinstance(row, dict)]
    add('complete_case_inventory', len(names) == len(CASE_NAMES) and set(names) == set(CASE_NAMES))
    by_name = {row.get('name'): row for row in rows if isinstance(row, dict)}
    first = by_name.get('first_http', {})
    nonce = report.get('fixture_nonce')
    for name in CASE_NAMES:
        row = by_name.get(name, {})
        prefix = name + '/'
        add(prefix + 'completed_observation', row.get('completed') is True and not row.get('error'))
        pid = row.get('browser_pid')
        add(prefix + 'owned_process', integer(pid, 1, 0xffffffff) and row.get('owned_exact_browser') is True
            and isinstance(row.get('process_created'), (int, float)) and row['process_created'] > 0)
        try:
            root = Path(report['temporary_root'])
            data_root = root / ('lifecycle' if name in ('first_http', 'restart_pref5', 'completed_token', 'expired_delete') else name)
            paths = (['/case/restored_tabs/0', '/case/restored_tabs/1', '/case/restored_tabs/extra-start']
                     if name == 'restored_tabs' else ['/case/' + name])
            if name in ('about_blank', 'no_startup_window'):
                paths = []
            urls = [] if name == 'no_startup_window' else (
                [network['origin'] + paths[-1]] if name == 'restored_tabs' else
                ['about:blank'] if name == 'about_blank' else [network['origin'] + path for path in paths])
            request = row.get('request')
            owned_request = request is None if name == 'restart_pref5' else (
                isinstance(request, dict) and request.get('environmentId') == ENVIRONMENT_ID
                and type(request.get('schemaVersion')) is int and request['schemaVersion'] == 1
                and request.get('contract') == CONTRACT and integer(request.get('cookieCount'))
                and request.get('profileDirectory') == 'Default' and HEX.fullmatch(str(request.get('token', '')))
                and str(uuid.UUID(request['requestId'])) == request['requestId']
                and HEX.fullmatch(str(request.get('sourceSha256', '')))
                and HEX.fullmatch(str(request.get('envelopeSha256', '')))
                and Path(request['requestPath']) == data_root / 'ShardX Cookie Imports/requests' / (request['token'] + '.' + request['requestId'] + '.json')
                and Path(request['receiptPath']) == data_root / 'ShardX Cookie Imports/receipts' / (request['token'] + '.json')
                and Path(request['sourcePath']).is_relative_to(root / 'inputs'))
            expected_args = launch_args(data_root, network['proxy_port'], request, urls,
                                        name == 'no_startup_window', name == 'restored_tabs')
            matched = (root.is_absolute() and root.name.startswith('security-runtime-native-cookie-')
                and owned_request and row['expected_paths'] == paths and row['requested_arguments'] == expected_args
                and security.arguments_match(row['actual_arguments'], expected_args, Path(report['browser'])))
        except (KeyError, TypeError, ValueError):
            matched = False
        add(prefix + 'safe_exact_arguments', matched and
            not any(arg in ('--no-sandbox', '--restore-last-session', '--disable-web-security')
                    for arg in row.get('actual_arguments', [])))
        add(prefix + 'exact_version', row.get('browser_version') == 'Chrome/' + security.VERSION
            or (name == 'no_startup_window' and row.get('exited_after_eager_completion') is True))
        events, paths = row.get('http_events', []), row.get('expected_paths', [])
        if name in FAILURES or name == 'receipt_unwritable':
            add(prefix + 'navigation_cancelled', row.get('navigation_error') == 'ERR_BLOCKED_BY_CLIENT'
                and row.get('navigation_timed_out') is False and events == [])
        elif paths:
            wanted_names = (['SX_SESSION'] if name == 'duplicate_identical' else [] if name == 'expired_delete'
                            else ['SX_PERSIST'] if name in ('restart_pref5', 'completed_token')
                            else ['SX_PERSIST', 'SX_SESSION'])
            wanted_keys = {'SX_SESSION'} if name == 'duplicate_identical' else {'SX_SESSION', 'SX_PERSIST'}
            add(prefix + 'first_native_http_cookies', bool(events)
                and set(paths) <= {event.get('path') for event in events}
                and all(event.get('nonce') == nonce and event.get('cookie_names') == wanted_names
                        and set(event.get('cookie_matches', {})) == wanted_keys
                        and all(value is True for value in event['cookie_matches'].values())
                        for event in events))
        else:
            add(prefix + 'no_http_trigger', events == [] and row.get('receipt_before_cdp') is True)
        request, receipt = row.get('request'), row.get('receipt', {})
        if name in SUCCESS:
            unique, duplicates, expired = SUCCESS[name]
            add(prefix + 'native_completion', isinstance(request, dict)
                and receipt_base(receipt, request)
                and all(receipt.get(key) == request.get(key) for key in ('requestId', 'envelopeSha256', 'deadlineUnixMs'))
                and receipt.get('browserPid') == pid
                and completed_counts(receipt, request, unique, duplicates, expired)
                and integer(row.get('receipt_read_unix_ms'), 1, 9007199254740991)
                and row['receipt_read_unix_ms'] <= request['deadlineUnixMs'])
        elif name in FAILURES:
            add(prefix + 'native_failure', isinstance(request, dict) and receipt_base(receipt, request)
                and all(receipt.get(key) == request.get(key) for key in ('requestId', 'envelopeSha256'))
                and receipt.get('browserPid') == pid and receipt.get('status') == 'failed'
                and receipt.get('errorCode') == FAILURES[name] and receipt.get('rollbackPerformed') is False
                and all(integer(receipt.get(key)) for key in ('attemptedCookieCount',
                    'acceptedCookieCount', 'verifiedCookieCount', 'expiredCookieCount')))
            if name == 'deadline_expired':
                add(prefix + 'shared_deadline_already_expired', isinstance(request, dict)
                    and integer(request.get('deadlineUnixMs'), 1, 9007199254740991)
                    and request['deadlineUnixMs'] < row.get('process_created', 0) * 1000)
            if name == 'quota_readback':
                add(prefix + 'real_quota_readback_failure', receipt.get('attemptedCookieCount') == 200
                    and receipt.get('acceptedCookieCount') == 200
                    and receipt.get('uniqueCookieCount') == 200
                    and integer(receipt.get('verifiedCookieCount'), 0, 199)
                    and receipt.get('partialApplied') is True)
            elif name == 'backend_rejection':
                add(prefix + 'actual_set_exclusion', receipt.get('attemptedCookieCount') == 1
                    and receipt.get('acceptedCookieCount') == 0
                    and receipt.get('writeOutcomeUnknown') is False)
            else:
                add(prefix + 'no_partial_prevalidation_write', receipt.get('attemptedCookieCount') == 0
                    and receipt.get('acceptedCookieCount') == 0 and receipt.get('partialApplied') is False
                    and row.get('stored_fixture_cookie_names') == [])
        elif name == 'receipt_unwritable':
            add(prefix + 'no_false_completion', row.get('receipt_unwritable') is True
                and not receipt and row.get('completed_receipt_exists') is False)
        elif name == 'restart_pref5':
            add(prefix + 'no_import_on_restart', request is None
                and not any(arg.startswith('--shardx-cookie-import') for arg in row.get('actual_arguments', [])))
        elif name == 'completed_token':
            old_receipt = first.get('receipt', {})
            add(prefix + 'historical_completion_not_current_success', isinstance(request, dict)
                and receipt_base(receipt, request) and completed_counts(receipt, request, 2, 0, 0)
                and receipt.get('browserPid') == first.get('browser_pid')
                and row.get('process_created') != first.get('process_created')
                and receipt.get('requestId') == old_receipt.get('requestId') != request.get('requestId')
                and row.get('receipt_sha256') == first.get('receipt_sha256'))
        if name in ('first_http', 'restart_pref5', 'completed_token', 'expired_delete'):
            add(prefix + 'native_normal_shutdown_pref5', row.get('startup_pref_after') == 5
                and row.get('startup_pref_observed') == 5
                and row.get('close', {}).get('normal') is True)
        if name in ('restart_pref5', 'completed_token'):
            add(prefix + 'session_not_promoted_or_replayed',
                row.get('stored_fixture_cookie_names') == ['SX_PERSIST'])
        if name == 'first_http':
            attrs = row.get('cookie_attributes', {})
            add(prefix + 'session_persistent_attributes', set(attrs) == {'SX_SESSION', 'SX_PERSIST'}
                and attrs.get('SX_SESSION', {}).get('session') is True
                and attrs.get('SX_SESSION', {}).get('httpOnly') is True
                and attrs.get('SX_PERSIST', {}).get('session') is False
                and attrs.get('SX_PERSIST', {}).get('priority') == 'High'
                and all(item.get('secure') is False and item.get('path') == '/' for item in attrs.values()))
        if name == 'expired_delete':
            add(prefix + 'existing_cookie_deleted', row.get('stored_fixture_cookie_names') == [])
        if name == 'restored_tabs':
            add(prefix + 'restored_tabs_and_additional_start_url_waited', len(paths) == 3
                and len({event.get('path') for event in events if event.get('path') in paths}) == 3)
            seed = report.get('restored_seed', {})
            add(prefix + 'actual_previous_session_fixture', seed.get('completed') is True
                and len(seed.get('expected_paths', [])) == 2
                and seed.get('expected_paths') == paths[:2]
                and set(seed['expected_paths']) <= {event.get('path') for event in seed.get('http_events', [])}
                and seed.get('request') is None and seed.get('close', {}).get('normal') is True
                and seed.get('startup_pref_observed') == 5
                and row.get('startup_pref_observed') == 1
                and row.get('startup_pref_after') == 1)
        if request:
            add(prefix + 'original_file_unchanged', row.get('original_sha256_after') == request.get('sourceSha256'))
    cleanup = report.get('cleanup', {})
    for name in ('owned_processes_closed', 'fixture_servers_closed', 'bindings_unchanged', 'owned_profile_removed'):
        add(name, cleanup.get(name) is True)
    return checks


def report_passed(report):
    recomputed = assess(report)
    declared = report.get('checks')
    if not isinstance(declared, list) or not all(isinstance(row, dict) for row in declared):
        return False
    names = [row.get('name') for row in declared]
    expected = [row['name'] for row in recomputed]
    return (report.get('status') == 'passed' and not report.get('error')
            and all(isinstance(name, str) for name in names)
            and len(names) == len(set(names)) == len(expected) and set(names) == set(expected)
            and all(row.get('passed') is True for row in declared)
            and all(row['passed'] is True for row in recomputed))


def binding_identity(browser, source_record):
    browser = security.plain(browser)
    dll = security.plain(browser.with_name('chrome.dll'))
    caps = security.plain(browser.with_name('native-cookie-capabilities.json'))
    receipt = security.plain(browser.with_name('google-services-source.json'))
    build = security.plain(browser.with_name('google-services-build.json'))
    source = security.source_identity(source_record, security.build_record.recipe(REPO))
    if security.digest(receipt) != source['source_receipt_sha256']:
        raise ValueError('Cookie runtime source receipt differs from the actual build record')
    cap_hash = security.digest(caps)
    if cap_hash != security.digest(REPO / 'build/native-cookie-capabilities.json'):
        raise ValueError('Native Cookie sidecar is not this source contract')
    if json.loads(caps.read_text()).get('contract') != CONTRACT:
        raise ValueError('Unexpected native Cookie contract')
    inventory = json.loads(build.read_text(encoding='utf-8-sig'))['files']
    for path in (browser, dll, caps, receipt):
        if inventory.get(path.name) != security.digest(path):
            raise ValueError('Packaged native Cookie runtime differs from its immutable inventory')
    if json.loads(receipt.read_text())['preparation_inputs'].get('build/native-cookie-capabilities.json') != cap_hash:
        raise ValueError('Native Cookie sidecar is not bound by preparation')
    binding = {'browser_sha256': security.digest(browser), 'chrome_dll_sha256': security.digest(dll),
        'capabilities_sha256': cap_hash, 'source_receipt_sha256': security.digest(receipt),
        'verifier_sha256': security.digest(Path(__file__)),
        'security_helper_sha256': security.digest(Path(security.__file__)),
        'process_helper_sha256': security.digest(Path(process_helper.__file__)),
        'recipe_tool_sha256': security.digest(Path(security.build_record.__file__)),
        'requirements_sha256': security.digest(REPO / 'tools/google-services-requirements.txt'),
        'python_sha256': security.digest(Path(sys.executable)), 'dependencies': security.dependency_identity()}
    return source, binding


def probe(browser, root, report, timeout):
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    fixtures = Fixtures()
    processes = []
    report['fixture_nonce'] = fixtures.server.nonce
    report['fixture_preflight'] = fixtures.preflight()
    report['network'] = {'origin': fixtures.origin, 'proxy_port': fixtures.proxy.server_port,
                         'external_forwarded_requests': 0}
    previous_environment = dict(os.environ)
    environment = audit_environment(previous_environment)
    # The Playwright driver must use the same credential-free loopback policy.
    os.environ.clear(); os.environ.update(environment)
    session_value, persistent_value = secrets.token_hex(24), secrets.token_hex(24)
    base_cookies = [
        {'name': 'SX_SESSION', 'value': session_value, 'url': fixtures.origin + '/input',
         'path': '/', 'httpOnly': True, 'secure': False, 'sameSite': 'Lax'},
        {'name': 'SX_PERSIST', 'value': persistent_value, 'url': fixtures.origin + '/input',
         'path': '/', 'httpOnly': False, 'secure': False, 'priority': 'High', 'expires': int(time.time()) + 86400}]
    both = {'SX_SESSION': session_value, 'SX_PERSIST': persistent_value}
    try:
        with sync_playwright() as playwright:
            def run_case(name, data_root, stage, wanted, paths, *, urls=None,
                         no_window=False, without_receipt=False, output=None,
                         restore_previous=False, additional_tabs=()):
                row = {'name': name, 'request': stage, 'expected_paths': paths, 'completed': False,
                       'receipt_unwritable': without_receipt}
                (report['cases'] if output is None else output).append(row)
                fixtures.select(name, wanted)
                urls = [fixtures.origin + path for path in paths] if urls is None else urls
                args = launch_args(data_root, fixtures.proxy.server_port, stage, urls, no_window, restore_previous)
                row['requested_arguments'] = args
                native = None
                try:
                    native = NativeProcess(browser, args, root, name, environment, playwright)
                    processes.append(native)
                    row.update(browser_pid=native.pid, process_created=native.created,
                               owned_exact_browser=True, actual_arguments=native.process_args)
                    if stage and not without_receipt:
                        def observe_receipt():
                            receipt = read_receipt(Path(stage['receiptPath']))
                            if receipt is not None:
                                return receipt
                            if fixtures.events(name):
                                raise RuntimeError('Navigation escaped the pending native Cookie barrier')
                            return None
                        row['receipt'] = wait_until(observe_receipt, timeout, native.alive)
                        row['receipt_read_unix_ms'] = now_ms()
                        row['receipt_sha256'] = security.digest(Path(stage['receiptPath']))
                        row['receipt_before_cdp'] = True
                    if no_window and not native.alive():
                        row['exited_after_eager_completion'] = native.supervisor.returncode == 0
                    else:
                        native.attach(data_root, timeout)
                        row['actual_arguments'] = native.cdp.send('Browser.getBrowserCommandLine')['arguments']
                        row['browser_version'] = native.cdp.send('Browser.getVersion')['product']
                        for url in additional_tabs:
                            native.connection.contexts[0].new_page().goto(url, wait_until='domcontentloaded', timeout=15000)
                        if name in FAILURES or without_receipt:
                            pages = native.connection.contexts[0].pages
                            if not pages:
                                raise RuntimeError('Failed native startup did not expose its owned target')
                            try:
                                pages[0].goto(fixtures.origin + '/case/' + name + '/must-not-navigate',
                                              wait_until='domcontentloaded', timeout=12000)
                                row.update(navigation_error=None, navigation_timed_out=False)
                            except PlaywrightTimeout:
                                row.update(navigation_error=None, navigation_timed_out=True)
                            except Exception as error:
                                row.update(navigation_error=safe_error(error)['network_error'], navigation_timed_out=False)
                        elif paths:
                            fixtures.wait_paths(name, paths)
                        cookies = native.cdp.send('Storage.getCookies')['cookies']
                        selected = {cookie['name']: cookie for cookie in cookies if cookie['name'].startswith('SX_')}
                        row['stored_fixture_cookie_names'] = sorted(selected)
                        row['cookie_attributes'] = {key: {field: value.get(field) for field in
                            ('domain', 'path', 'secure', 'httpOnly', 'session', 'expires', 'sameSite', 'priority')}
                            for key, value in selected.items()}
                        if name in ('first_http', 'restart_pref5', 'completed_token', 'expired_delete',
                                    'restored_seed', 'restored_tabs'):
                            row['startup_pref_observed'] = native_startup_preferences(native)['restore_on_startup']
                    if without_receipt:
                        row['completed_receipt_exists'] = bool(read_receipt(Path(stage['receiptPath'])))
                    row['completed'] = True
                except Exception as error:
                    row['error'] = safe_error(error)
                finally:
                    row['http_events'] = fixtures.events(name)
                    if native:
                        row['close'] = native.close(data_root, timeout)
                    stored = preference(data_root)
                    row['startup_pref_stored_after'] = stored
                    # Managed command-line prefs intentionally outrank the
                    # user's stored layer without rewriting it.
                    row['startup_pref_after'] = row.get('startup_pref_observed', stored)
                    if stage:
                        source = Path(stage['sourcePath'])
                        row['original_sha256_after'] = security.digest(source) if source.is_file() else None
                return row

            life = root / 'lifecycle'
            make_profile(life)
            first = make_stage(root, life, 'first_http', base_cookies, original_name='lifecycle')
            run_case('first_http', life, first, both, ['/case/first_http'])
            run_case('restart_pref5', life, None, {'SX_SESSION': None, 'SX_PERSIST': persistent_value},
                     ['/case/restart_pref5'])
            repeated = make_stage(root, life, 'completed_token', base_cookies, token=first['token'], original_name='lifecycle')
            run_case('completed_token', life, repeated, {'SX_SESSION': None, 'SX_PERSIST': persistent_value},
                     ['/case/completed_token'])
            expired = [{'name': 'SX_PERSIST', 'value': 'expired-delete', 'url': fixtures.origin + '/input',
                        'path': '/', 'secure': False, 'expires': 0}]
            run_case('expired_delete', life, make_stage(root, life, 'expired_delete', expired),
                     {'SX_SESSION': None, 'SX_PERSIST': None}, ['/case/expired_delete'])
            for name in ('duplicate_identical', *FAILURES, 'receipt_unwritable', 'about_blank', 'no_startup_window'):
                data = root / name
                paths = ['/case/' + name]
                make_profile(data)
                cookies, wanted = [dict(item) for item in base_cookies], dict(both)
                if name == 'duplicate_identical':
                    cookies = [dict(base_cookies[0]), dict(base_cookies[0])]
                    wanted = {'SX_SESSION': session_value}
                elif name == 'duplicate_conflict':
                    cookies = [dict(base_cookies[0]), {**base_cookies[0], 'value': 'different-synthetic-value'}]
                elif name == 'unsupported_partition':
                    cookies = [{**base_cookies[0], 'partitionKey': {'topLevelSite': 'https://fixture.test'}}]
                elif name == 'quota_readback':
                    cookies = [{'name': 'SX_QUOTA_' + str(i), 'value': 'synthetic', 'url': fixtures.origin,
                                'path': '/', 'expires': int(time.time()) + 86400} for i in range(200)]
                elif name == 'backend_rejection':
                    cookies = [{'name': 'SX_NONE', 'value': 'synthetic', 'url': fixtures.origin,
                                'path': '/', 'secure': False, 'sameSite': 'None'}]
                stage = make_stage(root, data, name, cookies, bad_hash=name == 'bad_hash',
                                   bad_json=name == 'bad_json', expired_deadline=name == 'deadline_expired')
                if name == 'receipt_unwritable':
                    (data / 'ShardX Cookie Imports/receipts').write_bytes(b'owned negative-control file')
                eager = name in ('about_blank', 'no_startup_window')
                run_case(name, data, stage, wanted, [] if eager else paths,
                         urls=([] if name == 'no_startup_window' else ['about:blank']) if eager else None,
                         no_window=name == 'no_startup_window', without_receipt=name == 'receipt_unwritable')
            restored = root / 'restored_tabs'
            paths = ['/case/restored_tabs/0', '/case/restored_tabs/1', '/case/restored_tabs/extra-start']
            make_profile(restored)
            seed_rows = []
            run_case('restored_seed', restored, None, {'SX_SESSION': None, 'SX_PERSIST': None},
                     paths[:2], urls=[fixtures.origin + paths[0]], output=seed_rows,
                     additional_tabs=[fixtures.origin + paths[1]])
            report['restored_seed'] = seed_rows[0]
            run_case('restored_tabs', restored, make_stage(root, restored, 'restored_tabs', base_cookies),
                     both, paths, urls=[fixtures.origin + paths[-1]], restore_previous=True)
    finally:
        for native in processes:
            if not native.closed:
                try:
                    native.job.terminate(); native.job.close()
                except OSError:
                    pass
        report['cleanup']['owned_processes_closed'] = bool(processes) and all(
            native.closed and not native.close_result['cleanup_errors'] for native in processes)
        report['cleanup']['fixture_servers_closed'] = fixtures.close()
        report['network']['denied_external_requests'] = len(fixtures.proxy.denied)
        os.environ.clear(); os.environ.update(previous_environment)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--source-record', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=75)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 90:
        parser.error('Native Cookie observation timeout must be in 1..90 seconds')
    destination = security.plain(args.report)
    if destination.exists():
        parser.error('Use a new report path; retain previous evidence')
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'contract': CONTRACT, 'status': 'failed',
              'source': {}, 'binding': {}, 'cases': [], 'checks': [], 'cleanup': {},
              'scope': 'Native same-instance synthetic Cookie import, first HTTP request, native session lifetime and fail-closed navigation',
              'persistent_real_profiles_touched': False, 'real_accounts': 'not_tested',
              'late_history_policy': 'An old completed ledger proves prior-PID write/readback only; it never proves current session Cookies survived.'}
    root = None
    try:
        if os.name != 'nt':
            raise ValueError('The native Cookie acceptance lane requires Windows')
        browser = security.plain(args.browser)
        report['browser'] = str(browser)
        report['source'], report['binding'] = binding_identity(browser, args.source_record)
        root = Path(tempfile.mkdtemp(prefix='security-runtime-native-cookie-', dir=destination.parent))
        owner = secrets.token_hex(32)
        (root / '.security-runtime-owner').write_text(owner)
        report['temporary_root'] = str(root)
        probe(browser, root, report, args.timeout)
        source, binding = binding_identity(browser, args.source_record)
        report['cleanup']['bindings_unchanged'] = source == report['source'] and binding == report['binding']
    except Exception as error:
        report['error'] = safe_error(error)
    finally:
        if root:
            try:
                if report['cleanup'].get('owned_processes_closed') is not True:
                    raise ValueError('Retain owned files until all browser processes are closed')
                security.remove_owned(root, destination.parent, owner)
                report['cleanup']['owned_profile_removed'] = True
            except Exception:
                report['cleanup']['owned_profile_removed'] = False
        report['checks'] = assess(report)
        if not report.get('error') and all(row['passed'] for row in report['checks']):
            report['status'] = 'passed'
        with destination.open('x', encoding='utf-8') as output:
            json.dump(report, output, indent=2); output.write('\n')
        print(json.dumps({'status': report['status'], 'report': str(destination),
                          'failed': [row['name'] for row in report['checks'] if not row['passed']],
                          'error': report.get('error')}))
    return 0 if report_passed(report) else 1


if __name__ == '__main__':
    raise SystemExit(main())
