#!/usr/bin/env python3
"""Exact-candidate native browser probe using owned, account-free profiles.

External requests are rejected by a loopback proxy. Only owned IPv4/IPv6 HTTP
fixtures are bypassed. Fake media devices exercise permission/capture routing;
no real camera, microphone, account, or existing browser profile is opened.
Headless results do not establish Windows taskbar/avatar UI behavior.
"""
from __future__ import annotations

import argparse
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import tempfile
import threading
from urllib.parse import urlsplit

try:
    from . import verify_security_runtime as security
except ImportError:
    import verify_security_runtime as security

REPO = Path(__file__).resolve().parents[1]
VERSION = security.VERSION
CONTRACT = 'shardx-native-browser-v1'
ENVIRONMENT_ID = '01234567-89ab-4cde-8123-456789abcdef'
PREFS = {'search.suggest_enabled': ('BOOLEAN', True),
         'translate.enabled': ('BOOLEAN', True),
         'session.restore_on_startup': ('NUMBER', 5)}
SCENARIOS = (
    ('defaults', 'Default', None, None),
    ('allow_default', 'Default', False, None),
    ('allow_profile1', 'Profile 1', False, None),
    ('blocked_true_default', 'Default', True, True),
    ('blocked_true_profile1', 'Profile 1', True, True),
    ('blocked_false_default', 'Default', True, False),
    ('blocked_false_profile1', 'Profile 1', True, False),
)
CLEANUP_CHECKS = {'owned_processes_closed', 'fixture_servers_closed',
                  'bindings_unchanged', 'owned_profile_removed'}
HASH = re.compile('[a-f0-9]{64}\\Z')
LOCKED_DEPENDENCIES = dict(re.findall(r'(?m)^(playwright|psutil)==([^\s#]+)$',
    (REPO / 'tools/google-services-requirements.txt').read_text(encoding='utf-8')))
READ_SEARCH = """async () => {
  const {sendWithPromise} = await import('chrome://resources/js/cr.js');
  const result = await sendWithPromise('getSearchEnginesList');
  const groups = ['defaults','actives','others','extensions'];
  return {available: true, engines: groups.flatMap(group =>
    Array.isArray(result[group]) ? result[group].map(row => ({group,
      name: row.name, keyword: row.keyword, url: row.url,
      default: row.default === true, id: row.id})) : [])};
}"""
READ_MEDIA = """async () => {
  const result = {secure: isSecureContext,
    available: typeof navigator.mediaDevices?.getUserMedia === 'function',
    permissions: {}, capture: {}};
  for (const name of ['camera','microphone']) {
    try { result.permissions[name] = (await navigator.permissions.query({name})).state; }
    catch (error) { result.permissions[name] = {error: error.name}; }
  }
  for (const [name, constraints] of Object.entries({audio:{audio:true},video:{video:true}})) {
    let timedOut = false;
    const request = navigator.mediaDevices.getUserMedia(constraints).then(stream => {
      const count = stream.getTracks().length;
      stream.getTracks().forEach(track => track.stop());
      return {ok: !timedOut, tracks: count};
    }, error => ({ok:false, error:error.name}));
    result.capture[name] = await Promise.race([request,
      new Promise(resolve => setTimeout(() => {
        timedOut = true; resolve({ok:false,error:'ProbeTimeout'});
      }, 7000))]);
  }
  return result;
}"""
FETCH = """async url => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 7000);
  try {
    const response = await fetch(url, {cache:'no-store',signal:controller.signal});
    return {ok:response.ok,status:response.status,payload:await response.json()};
  } catch (error) { return {ok:false,error:error.name,message:String(error.message)}; }
  finally { clearTimeout(timer); }
}"""


def obj(value):
    return value if isinstance(value, dict) else {}


def safe_environment(root):
    env = {key: value for key, value in os.environ.items() if not re.search(
        r'TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL|SSLKEYLOGFILE|^PWDEBUG$|^DEBUG$|'
        r'^NODE_OPTIONS$|^NODE_PATH$|^(GOOGLE_|GH_|GITHUB_|CHROME_|CHROMIUM_|CHROMIX_|SHARDX_|UXR_|CLOAK_|CODEX_|OPENAI_|ANTHROPIC_|AWS_|AZURE_)', key, re.I)}
    env.update(TEMP=str(root), TMP=str(root), TMPDIR=str(root))
    return env


def launch_options(browser, data_root, native_profile, proxy_port, blocked_port,
                   media_blocked, dnt, environment):
    args = ['--headless=new', '--enable-automation', '--remote-debugging-pipe',
        '--user-data-dir=' + str(data_root), '--profile-directory=' + native_profile,
        '--no-first-run', '--no-default-browser-check',
        '--shardx-managed-environment=' + ENVIRONMENT_ID,
        '--proxy-server=http://127.0.0.1:' + str(proxy_port),
        '--proxy-bypass-list=127.0.0.1;[::1];localhost',
        '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE ::1, EXCLUDE localhost']
    if media_blocked is not None:
        # Replace hardware only. Fake UI installs a separate access proxy which
        # bypasses Chrome's permission/capture dispatcher, the subject of this
        # probe. The owned profile's saved grants exercise the native UI path.
        args.append('--use-fake-device-for-media-stream')
    if media_blocked:
        args.extend(['--shardx-block-camera', '--shardx-block-microphone',
                     '--shardx-blocked-ports=' + str(blocked_port),
                     '--explicitly-allowed-ports=' + str(blocked_port),
                     '--shardx-do-not-track=' + str(dnt).lower()])
    args.append('about:blank')
    return dict(executable_path=str(browser), ignore_default_args=True, args=args,
        headless=True, chromium_sandbox=True, ignore_https_errors=False,
        bypass_csp=False, accept_downloads=False, timeout=60000, env=environment)


def seed_media_preferences(data_root, native_profile, origin):
    data_root = security.plain(data_root)
    data_root.mkdir()
    profile = security.plain(data_root / native_profile)
    profile.mkdir()  # Existing data roots/profiles are never accepted here.
    value = {'profile': {'default_content_setting_values': {
        'media_stream_camera': 1, 'media_stream_mic': 1},
        'content_settings': {'exceptions': {
            key: {origin + ',*': {'setting': 1}}
            for key in ('media_stream_camera', 'media_stream_mic')}}}}
    path = profile / 'Preferences'
    with path.open('x', encoding='utf-8') as output:
        json.dump(value, output, sort_keys=True)
    return {'preferences_sha256': security.digest(path), 'origin': origin,
            'stored_grants': value['profile']['content_settings']['exceptions']}


def stored_media_grants(data_root, native_profile, origin):
    value = json.loads((data_root / native_profile / 'Preferences').read_text(encoding='utf-8'))
    exceptions = obj(obj(obj(value.get('profile')).get('content_settings')).get('exceptions'))
    return {key: obj(obj(exceptions.get(key)).get(origin + ',*')).get('setting')
            for key in ('media_stream_camera', 'media_stream_mic')}


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        with self.server.hit_lock:
            self.server.hits.append(self.path)
        if self.path.startswith('/page/'):
            data = b'<!doctype html><meta charset="utf-8"><title>Owned native browser fixture</title>'
            content_type = 'text/html; charset=utf-8'
        else:
            data = json.dumps({'nonce': self.server.nonce,
                'path': self.path, 'dnt': self.headers.get('DNT')}).encode()
            content_type = 'application/json'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'DNT, Content-Type')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *_args):
        pass


class IPv6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


class Fixtures:
    def __init__(self):
        self.servers, self.threads = [], []
        self.targets, self.nonces = {}, {}
        try:
            proxy = ThreadingHTTPServer(('127.0.0.1', 0), security.DenyProxy)
            proxy.denied = []
            self.proxy = self.start(proxy)
            for kind in ('blocked', 'allowed'):
                nonce = secrets.token_hex(24)
                self.nonces[kind] = nonce
                ipv4 = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
                port = ipv4.server_port
                ipv4.nonce, ipv4.hits, ipv4.hit_lock = nonce, [], threading.Lock()
                self.targets[kind + '_ipv4'] = (self.start(ipv4), f'http://127.0.0.1:{port}')
                ipv6 = IPv6Server(('::1', port), FixtureHandler)
                ipv6.nonce, ipv6.hits, ipv6.hit_lock = nonce, [], threading.Lock()
                self.targets[kind + '_ipv6'] = (self.start(ipv6), f'http://[::1]:{port}')
            self.blocked_port = self.targets['blocked_ipv4'][0].server_port
            self.allowed_port = self.targets['allowed_ipv4'][0].server_port
        except BaseException:
            self.close()
            raise

    def start(self, server):
        server.daemon_threads = True
        self.servers.append(server)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.threads.append(thread)
        thread.start()
        return server

    def close(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=5)
        return not any(thread.is_alive() for thread in self.threads)

    def preflight(self):
        rows = []
        for key, (_server, origin) in self.targets.items():
            target = urlsplit(origin)
            connection = HTTPConnection(target.hostname, target.port, timeout=5)
            try:
                connection.request('GET', '/health')
                response = connection.getresponse()
                value = json.loads(response.read())
                rows.append({'target': key, 'passed': response.status == 200
                    and value.get('nonce') == self.nonces[key.split('_')[0]]})
            finally:
                connection.close()
        return rows


def endpoint_results(page, fixtures, scenario):
    failures = {}
    page.on('requestfailed', lambda request: failures.__setitem__(request.url, request.failure))
    results = {}
    for key, (server, origin) in fixtures.targets.items():
        path = '/fetch/' + scenario + '/' + key
        url = origin + path
        value = page.evaluate(FETCH, url)
        page.wait_for_timeout(50)
        with server.hit_lock:
            received = server.hits.count(path)
        results[key] = {**obj(value), 'url': url, 'failure': failures.get(url),
                        'server_hits': received}
    return results


def assess(report):
    checks = []
    def add(name, passed):
        checks.append({'name': name, 'passed': passed is True})
    binding, source = obj(report.get('binding')), obj(report.get('source'))
    add('exact_input_bindings', all(HASH.fullmatch(str(binding.get(key, ''))) for key in (
        'browser_sha256', 'chrome_dll_sha256', 'capabilities_sha256', 'source_receipt_sha256',
        'verifier_sha256', 'security_helper_sha256', 'recipe_tool_sha256',
        'python_sha256', 'requirements_sha256'))
        and all(HASH.fullmatch(str(source.get(key, ''))) for key in (
            'record_sha256', 'recipe_sha256', 'source_receipt_sha256', 'gn_sha256'))
        and binding.get('source_receipt_sha256') == source.get('source_receipt_sha256')
        and all(HASH.fullmatch(str(obj(obj(binding.get('dependencies')).get(package)).get('inventory_sha256', '')))
            and obj(obj(binding.get('dependencies')).get(package)).get('version') == LOCKED_DEPENDENCIES[package]
            and type(obj(obj(binding.get('dependencies')).get(package)).get('files')) is int
            and obj(obj(binding.get('dependencies')).get(package))['files'] > 0
            for package in ('playwright', 'psutil'))
        and report.get('contract') == CONTRACT)
    network = obj(report.get('network'))
    preflight = report.get('fixture_preflight', [])
    expected_targets = {'blocked_ipv4', 'blocked_ipv6', 'allowed_ipv4', 'allowed_ipv6'}
    add('owned_loopback_fixtures_ready', isinstance(preflight, list) and len(preflight) == 4
        and {obj(row).get('target') if isinstance(obj(row).get('target'), str) else None
             for row in preflight} == expected_targets
        and all(obj(row).get('passed') is True for row in preflight)
        and network.get('external_forwarded_requests') == 0
        and type(network.get('blocked_port')) is int and type(network.get('allowed_port')) is int
        and 1 <= network['blocked_port'] <= 65535 and 1 <= network['allowed_port'] <= 65535
        and type(network.get('proxy_port')) is int and 1 <= network['proxy_port'] <= 65535
        and len({network['blocked_port'], network['allowed_port'], network['proxy_port']}) == 3)
    rows = report.get('scenarios', [])
    rows = rows if isinstance(rows, list) else []
    by_name = {(obj(row).get('name') if isinstance(obj(row).get('name'), str) else None): obj(row) for row in rows}
    add('complete_unique_scenarios', len(rows) == len(SCENARIOS)
        and len(by_name) == len(rows) and set(by_name) == {item[0] for item in SCENARIOS})
    identities = []
    for name, profile, blocked, dnt in SCENARIOS:
        row = by_name.get(name, {})
        prefix = name + '/'
        add(prefix + 'completed', not row.get('error') and row.get('completed') is True)
        add(prefix + 'version', row.get('browser_version') == 'Chrome/' + VERSION)
        identity = row.get('process_identity')
        valid_identity = (isinstance(identity, list) and len(identity) == 2
            and type(identity[0]) is int and identity[0] > 0
            and type(identity[1]) in (float, int) and identity[1] > 0 and math.isfinite(identity[1]))
        add(prefix + 'owned_process_identity', valid_identity)
        if valid_identity:
            identities.append(tuple(identity))
        safe = selected = False
        try:
            root = Path(report['temporary_root'])
            browser = Path(report['browser'])
            data_root = root / name
            expected = launch_options(browser, data_root, profile, network['proxy_port'],
                network['blocked_port'], blocked, dnt, {})
            safe = (root.name.startswith('security-runtime-native-browser-')
                and row.get('launch_policy') == {key: value for key, value in expected.items() if key != 'env'}
                and security.arguments_match(row.get('actual_arguments'), expected['args'], browser))
            selected = os.path.normcase(os.path.normpath(row['actual_profile_path'])) == os.path.normcase(str(data_root / profile))
        except (KeyError, TypeError, ValueError):
            pass
        add(prefix + 'exact_launch_policy', safe)
        add(prefix + 'native_profile_selected', selected)
        if name == 'defaults':
            preferences = obj(row.get('preferences'))
            for key, (kind, wanted) in PREFS.items():
                item = obj(obj(preferences.get('values')).get(key))
                pref = obj(item.get('pref'))
                add(prefix + key, preferences.get('available') is True and not item.get('error')
                    and pref.get('key') == key and pref.get('type') == kind
                    and type(pref.get('value')) is type(wanted) and pref.get('value') == wanted
                    and not pref.get('controlledBy') and not pref.get('enforcement'))
            search = obj(row.get('search'))
            engines = search.get('engines', [])
            engines = engines if isinstance(engines, list) else []
            add(prefix + 'native_google_definition', search.get('available') is True
                and any(obj(engine).get('name') == 'Google'
                    and obj(engine).get('keyword') == 'google.com'
                    and 'search?q=' in str(obj(engine).get('url', '')) for engine in engines)
                and all(obj(engine).get('name') != 'No Search'
                    and obj(engine).get('keyword') != 'nosearch' for engine in engines))
            flags = row.get('saved_labs_experiments')
            add(prefix + 'no_forced_history_or_session', isinstance(flags, list)
                and all(isinstance(flag, str) and flag.split('@')[0] != 'keep-old-history' for flag in flags)
                and isinstance(row.get('actual_arguments'), list)
                and all(isinstance(arg, str) for arg in row['actual_arguments'])
                and not any(arg.split('=')[0] in ('--keep-old-history', '--restore-last-session')
                            for arg in row['actual_arguments']))
        if blocked is not None:
            media = obj(row.get('media'))
            add(prefix + 'media_api_available', media.get('secure') is True and media.get('available') is True)
            permissions = obj(media.get('permissions'))
            capture = obj(media.get('capture'))
            for surface, device in (('camera', 'video'), ('microphone', 'audio')):
                result = obj(capture.get(device))
                good = (permissions.get(surface) == ('denied' if blocked else 'granted')
                    and (result.get('ok') is False and result.get('error') == 'NotAllowedError'
                         if blocked else result.get('ok') is True and type(result.get('tracks')) is int
                         and result['tracks'] >= 1))
                add(prefix + 'media_' + surface, good)
            grants = obj(row.get('saved_media_grants'))
            add(prefix + 'persistent_allow_remains_intact', all(type(grants.get(key)) is int
                and grants[key] == 1 for key in ('media_stream_camera', 'media_stream_mic'))
                and HASH.fullmatch(str(obj(row.get('media_seed')).get('preferences_sha256', ''))) is not None)
        if name == 'defaults' or blocked:
            fetches = obj(row.get('fetches'))
            for key in sorted(expected_targets):
                result = obj(fetches.get(key))
                denied = bool(blocked and key.startswith('blocked_'))
                kind, family = key.split('_')
                host = '127.0.0.1' if family == 'ipv4' else '[::1]'
                expected_url = f"http://{host}:{network.get(kind + '_port')}/fetch/{name}/{key}"
                if denied:
                    good = (result.get('ok') is False and result.get('failure') == 'net::ERR_UNSAFE_PORT'
                        and type(result.get('server_hits')) is int and result['server_hits'] == 0)
                else:
                    payload = obj(result.get('payload'))
                    good = (result.get('ok') is True and result.get('status') == 200
                        and type(result.get('server_hits')) is int and result['server_hits'] == 1 and not result.get('failure')
                        and payload.get('nonce') == obj(network.get('nonces')).get(key.split('_')[0])
                        and isinstance(payload.get('nonce'), str) and len(payload['nonce']) == 48
                        and payload.get('path') == '/fetch/' + name + '/' + key)
                add(prefix + 'network_' + key, good and result.get('url') == expected_url)
            if blocked:
                add(prefix + 'dnt', row.get('navigator_dnt') == ('1' if dnt else None)
                    and 'navigator_dnt' in row and all(
                        obj(obj(fetches.get(key)).get('payload')).get('dnt') == ('1' if dnt else None)
                        and 'dnt' in obj(obj(fetches.get(key)).get('payload'))
                        for key in ('allowed_ipv4', 'allowed_ipv6')))
    add('independent_policy_processes', len(identities) == len(SCENARIOS)
        and len(set(identities)) == len(identities))
    return checks


def report_passed(report):
    declared = report.get('checks', [])
    if not isinstance(declared, list):
        return False
    derived = assess(report)
    required = {row['name'] for row in derived} | CLEANUP_CHECKS
    names = [obj(row).get('name') for row in declared]
    return (not report.get('error') and all(isinstance(name, str) for name in names)
        and len(names) == len(set(names))
        and set(names) == required and all(obj(row).get('passed') is True for row in declared)
        and all(row['passed'] is True for row in derived))


def probe(browser, root, report):
    import psutil
    from playwright.sync_api import sync_playwright
    owned, context, fixtures = {}, None, None
    previous_environment = dict(os.environ)
    def retain_children():
        for process in psutil.Process().children(recursive=True):
            try:
                owned[(process.pid, process.create_time())] = process
            except psutil.NoSuchProcess:
                pass
    try:
        fixtures = Fixtures()
        report['fixture_preflight'] = fixtures.preflight()
        report['network'] = {'proxy_port': fixtures.proxy.server_port,
            'blocked_port': fixtures.blocked_port, 'allowed_port': fixtures.allowed_port,
            'nonces': fixtures.nonces, 'external_forwarded_requests': 0}
        environment = safe_environment(root)
        os.environ.clear()
        os.environ.update(environment)
        with sync_playwright() as playwright:
            for name, native_profile, blocked, dnt in SCENARIOS:
                row = {'name': name, 'completed': False}
                report['scenarios'].append(row)
                data_root = security.plain(root / name)
                origin = fixtures.targets['allowed_ipv4'][1]
                try:
                    if blocked is not None:
                        row['media_seed'] = seed_media_preferences(data_root, native_profile, origin)
                    else:
                        data_root.mkdir()
                    options = launch_options(browser, data_root, native_profile,
                        fixtures.proxy.server_port, fixtures.blocked_port, blocked, dnt, environment)
                    row['launch_policy'] = {key: value for key, value in options.items() if key != 'env'}
                    context = playwright.chromium.launch_persistent_context(str(data_root), **options)
                    retain_children()
                    session = context.browser.new_browser_cdp_session()
                    row['actual_arguments'] = session.send('Browser.getBrowserCommandLine')['arguments']
                    row['browser_version'] = session.send('Browser.getVersion')['product']
                    browser_processes = [entry['id'] for entry in session.send('SystemInfo.getProcessInfo')['processInfo']
                                         if entry.get('type') == 'browser']
                    if len(browser_processes) != 1:
                        raise ValueError('Expected one actual browser process')
                    process = psutil.Process(browser_processes[0])
                    identity = (process.pid, process.create_time())
                    if identity not in owned or os.path.normcase(process.exe()) != os.path.normcase(str(browser)):
                        raise ValueError('Browser process is not an owned exact candidate')
                    row['process_identity'] = list(identity)
                    page = context.new_page()
                    page.goto('chrome://version/', wait_until='domcontentloaded', timeout=15000)
                    row['actual_profile_path'] = page.locator('#profile_path').inner_text().strip()
                    if name == 'defaults':
                        page.goto('chrome://settings/searchEngines', wait_until='domcontentloaded', timeout=15000)
                        page.wait_for_function("typeof chrome?.settingsPrivate?.getPref === 'function'", timeout=10000)
                        row['preferences'] = page.evaluate(security.READ_PREFS, list(PREFS))
                        row['search'] = page.evaluate(READ_SEARCH)
                    page.goto(origin + '/page/' + name, wait_until='domcontentloaded', timeout=15000)
                    if blocked is not None:
                        row['media'] = page.evaluate(READ_MEDIA)
                    if name == 'defaults' or blocked:
                        row['fetches'] = endpoint_results(page, fixtures, name)
                    if blocked:
                        row['navigator_dnt'] = page.evaluate('navigator.doNotTrack')
                    context.close()
                    context = None
                    if blocked is not None:
                        row['saved_media_grants'] = stored_media_grants(data_root, native_profile, origin)
                    if name == 'defaults':
                        state = json.loads((data_root / 'Local State').read_text(encoding='utf-8'))
                        row['saved_labs_experiments'] = obj(state.get('browser')).get('enabled_labs_experiments', [])
                    row['completed'] = True
                except Exception as error:
                    row['error'] = str(error)
                finally:
                    retain_children()
                    if context:
                        try:
                            context.close()
                        finally:
                            context = None
    finally:
        retain_children()
        try:
            remaining = [process for process in owned.values() if process.is_running()]
            _, remaining = psutil.wait_procs(remaining, timeout=5)
            forced = len(remaining)
            for process in remaining:
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
            _, remaining = psutil.wait_procs(remaining, timeout=5)
            report['checks'].append({'name': 'owned_processes_closed', 'passed': not remaining,
                                    'forced_cleanup': forced})
        except Exception as error:
            report['checks'].append({'name': 'owned_processes_closed', 'passed': False, 'error': str(error)})
        if fixtures:
            report['checks'].append({'name': 'fixture_servers_closed', 'passed': fixtures.close()})
            report['network']['denied_external_requests'] = fixtures.proxy.denied
        os.environ.clear()
        os.environ.update(previous_environment)


def binding_identity(browser, source_record):
    dll = security.plain(browser.with_name('chrome.dll'))
    capabilities = security.plain(browser.with_name('native-browser-capabilities.json'))
    receipt = security.plain(browser.with_name('google-services-source.json'))
    if not browser.is_file() or not dll.is_file() or not capabilities.is_file() or not receipt.is_file():
        raise ValueError('Supply the complete exact native-browser candidate directory')
    source = security.source_identity(source_record, security.build_record.recipe(REPO))
    if security.digest(receipt) != source['source_receipt_sha256']:
        raise ValueError('Packaged source receipt differs from the build record')
    if security.digest(capabilities) != security.digest(REPO / 'build/native-browser-capabilities.json'):
        raise ValueError('Packaged native-browser capability contract differs')
    if json.loads(capabilities.read_text(encoding='utf-8')).get('contract') != CONTRACT:
        raise ValueError('Unexpected native-browser contract')
    binding = {'browser_sha256': security.digest(browser), 'chrome_dll_sha256': security.digest(dll),
        'capabilities_sha256': security.digest(capabilities), 'source_receipt_sha256': security.digest(receipt),
        'verifier_sha256': security.digest(Path(__file__)),
        'security_helper_sha256': security.digest(Path(security.__file__)),
        'recipe_tool_sha256': security.digest(Path(security.build_record.__file__)),
        'requirements_sha256': security.digest(REPO / 'tools/google-services-requirements.txt'),
        'python_sha256': security.digest(Path(sys.executable)),
        'dependencies': security.dependency_identity()}
    return source, binding


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--source-record', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    destination = security.plain(args.report)
    if destination.exists():
        parser.error('Use a new report path; previous evidence is retained')
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'contract': CONTRACT, 'status': 'failed', 'checks': [], 'scenarios': [],
        'scope': 'Owned headless native defaults, profile-wide media, ports and DNT; no real accounts.',
        'native_windows_avatar_taskbar': 'not_tested', 'online_translation': 'not_tested',
        'real_google_accounts_and_sync': 'not_tested', 'historical_retention_duration': 'not_tested',
        'persistent_real_profiles_touched': False}
    root = None
    try:
        if os.name != 'nt':
            raise ValueError('This acceptance lane requires Windows')
        browser = security.plain(args.browser)
        report['browser'] = str(browser)
        report['source'], report['binding'] = binding_identity(browser, args.source_record)
        root = Path(tempfile.mkdtemp(prefix='security-runtime-native-browser-', dir=destination.parent))
        owner = secrets.token_hex(32)
        (root / '.security-runtime-owner').write_text(owner)
        report['temporary_root'] = str(root)
        probe(browser, root, report)
        source, binding = binding_identity(browser, args.source_record)
        report['checks'].append({'name': 'bindings_unchanged', 'passed':
                                source == report['source'] and binding == report['binding']})
    except Exception as error:
        report['error'] = str(error)
    finally:
        if root:
            try:
                if not any(row['name'] == 'owned_processes_closed' and row['passed'] is True for row in report['checks']):
                    raise ValueError('Retaining owned files until process cleanup succeeds')
                security.remove_owned(root, destination.parent, owner)
                report['checks'].append({'name': 'owned_profile_removed', 'passed': True})
            except Exception as error:
                report['checks'].append({'name': 'owned_profile_removed', 'passed': False, 'error': str(error)})
        report['checks'].extend(assess(report))
        if report_passed(report):
            report['status'] = 'passed'
        with destination.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
        print(json.dumps({'status': report['status'], 'report': str(destination),
            'failed': [row['name'] for row in report['checks'] if row.get('passed') is not True],
            'error': report.get('error')}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
