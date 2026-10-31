#!/usr/bin/env python3
"""Mandatory Windows 156 product probe; fresh profile and read-only WebUI APIs.

This gate verifies compiled pages and registered preference defaults, not an
online threat verdict. A loopback proxy rejects every external request. It
never opens an existing profile, changes preferences, or trusts a certificate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
import tempfile
import threading
from urllib.parse import urlsplit

try:
    from . import security_build_record as build_record
except ImportError:
    import security_build_record as build_record

REPO = Path(__file__).resolve().parents[1]
VERSION = build_record.VERSION
# Chromium 156: safe_browsing_prefs.h/.cc; password_manager_pref_names.h
# (kPasswordLeakDetectionEnabled), PasswordManager::RegisterProfilePrefs;
# all four are allowed by chrome/.../settings_private/prefs_util.cc.
# The generated.password_leak_detection UI pref is NOT the underlying default.
EXPECTED_PREFS = {
    'safebrowsing.enabled': True,
    'safebrowsing.enhanced': False,
    'safebrowsing.scout_reporting_enabled': False,
    'profile.password_manager_leak_detection': True,
}
URLS = {'safe_browsing': 'chrome://safe-browsing/',
        'settings_security': 'chrome://settings/security'}
REQUIRED_CHECKS = {'exact_browser_version', 'exact_safe_launch_arguments',
    'safe_browsing_page_available', 'settings_security_page_available',
    'safe_browsing_section_visible', 'owned_processes_closed', 'deny_proxy_closed',
    'bindings_unchanged', 'owned_profile_removed',
    *('default_pref/' + key for key in EXPECTED_PREFS)}
# Chromium/Chromix may append these exact non-weakening arguments itself.
# The WebRTC value comes from retained patch 0176; flags markers must be empty.
KERNEL_ADDED_ARGS = {'--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                     '--noerrdialogs', '--flag-switches-begin', '--flag-switches-end'}
READ_PREFS = """async keys => {
  if (typeof chrome?.settingsPrivate?.getPref !== 'function')
    return {available:false, values:{}};
  return await Promise.race([
    Promise.all(keys.map(key => new Promise(resolve =>
      chrome.settingsPrivate.getPref(key, pref => {
        const error = chrome.runtime?.lastError?.message ?? null;
        resolve([key, {error, pref:pref ? {key:pref.key,type:pref.type,
          value:pref.value,controlledBy:pref.controlledBy,
          enforcement:pref.enforcement} : null}]);
      })))).then(rows=>({available:true,values:Object.fromEntries(rows)})),
    new Promise(resolve=>setTimeout(()=>resolve({available:false,timeout:true,values:{}}),8000))
  ]);
}"""
READ_PAGE = """() => {
  let section = null;
  const walk = node => {
    if(node.id==='safeBrowsingSection')section=node;
    if(node.shadowRoot)walk(node.shadowRoot);
    for(const child of node.children??[])walk(child);
  };
  walk(document);
  return {url:location.href,title:document.title,
    ready:document.readyState!=='loading'&&!!document.body,
    safeBrowsingSectionVisible:!!section&&section.getClientRects().length>0&&
      getComputedStyle(section).visibility!=='hidden'};
}"""


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plain(path: Path) -> Path:
    path = path.absolute()
    for entry in (path, *path.parents):
        if entry.exists() or entry.is_symlink():
            info = entry.lstat()
            if entry.is_symlink() or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                raise ValueError('A linked path is not accepted')
    return path


def source_identity(path: Path, expected_recipe: str) -> dict:
    path = plain(path)
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if (value.get('schema_version') != 1 or value.get('build_variant') != 'security-win156'
            or value.get('chromium_version') != VERSION):
        raise ValueError('Expected the Windows 156 security build record')
    for key in ('recipe_sha256', 'source_receipt_sha256'):
        if not re.fullmatch('[a-f0-9]{64}', value.get(key, '')):
            raise ValueError('Invalid source record field: ' + key)
    if value['recipe_sha256'] != expected_recipe:
        raise ValueError('Source recipe does not match this checkout')
    gn = value.get('gn', {})
    if not re.fullmatch('[a-f0-9]{64}', gn.get('sha256', '')) or any(
            gn.get('assignments', {}).get(key) != wanted for key, wanted in build_record.REQUIRED_FLAGS.items()):
        raise ValueError('Source record has not passed the required GN configuration')
    return {'record_sha256': digest(path), 'recipe_sha256': value['recipe_sha256'],
            'source_receipt_sha256': value['source_receipt_sha256'], 'gn_sha256': gn['sha256']}


def dependency_identity() -> dict:
    locked = dict(re.findall(r'(?m)^(playwright|psutil)==([^\s#]+)$',
                             (REPO / 'tools/google-services-requirements.txt').read_text()))
    result = {}
    for package in ('playwright', 'psutil'):
        distribution = importlib.metadata.distribution(package)
        if distribution.version != locked.get(package):
            raise ValueError('Install the locked runtime dependency: ' + package)
        files = {}
        for relative in distribution.files or ():
            if relative.parts[0] != package or '__pycache__' in relative.parts or relative.suffix == '.pyc':
                continue
            target = plain(Path(distribution.locate_file(relative)))
            if target.is_file():
                files[str(relative).replace('\\', '/')] = digest(target)
        if not files:
            raise ValueError('Runtime dependency has no verifiable inventory: ' + package)
        result[package] = {'version': distribution.version, 'files': len(files),
                          'inventory_sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}
    return result


def assess_pages(pages: dict, preferences: dict) -> list[dict]:
    checks = []
    for name, url in URLS.items():
        page = pages.get(name, {})
        passed = not page.get('error') and page.get('url') == url and page.get('ready') is True
        checks.append({'name': name + '_page_available', 'passed': passed})
    checks.append({'name': 'safe_browsing_section_visible',
                   'passed': pages.get('settings_security', {}).get('safeBrowsingSectionVisible') is True})
    for key, expected in EXPECTED_PREFS.items():
        row = preferences.get('values', {}).get(key, {})
        pref = row.get('pref') or {}
        passed = (preferences.get('available') is True and not row.get('error')
                  and pref.get('key') == key and pref.get('type') == 'BOOLEAN'
                  and type(pref.get('value')) is bool and pref['value'] is expected
                  and not pref.get('controlledBy') and not pref.get('enforcement'))
        checks.append({'name': 'default_pref/' + key, 'passed': passed})
    return checks


def report_passed(report: dict) -> bool:
    checks = report.get('checks', [])
    names = [row.get('name') for row in checks]
    return (not report.get('error') and REQUIRED_CHECKS.issubset(names)
            and len(names) == len(set(names)) and all(row.get('passed') is True for row in checks))


def launch_options(browser: Path, profile: Path, port: int, environment: dict) -> dict:
    # Discard the library's unsafe defaults instead of maintaining a partial
    # ignore list. Do not suppress Safe Browsing/component initialization:
    # their requests reach the local deny proxy and are never forwarded.
    args = ['--headless=new', '--enable-automation', '--remote-debugging-pipe',
            '--user-data-dir=' + str(profile), '--no-first-run', '--no-default-browser-check',
            '--proxy-server=http://127.0.0.1:' + str(port), '--proxy-bypass-list=<-loopback>',
            '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1', 'about:blank']
    return dict(executable_path=str(browser), ignore_default_args=True, args=args,
                headless=True, chromium_sandbox=True, ignore_https_errors=False,
                bypass_csp=False, accept_downloads=False, timeout=60000, env=environment)


def arguments_match(actual: list[str], requested: list[str], browser: Path) -> bool:
    if not actual or os.path.normcase(actual[0]) != os.path.normcase(str(browser)):
        return False
    args = actual[1:]
    additions = Counter(args) - Counter(requested)
    if Counter(requested) - Counter(args) or any(
            key not in KERNEL_ADDED_ARGS or count != 1 for key, count in additions.items()):
        return False
    if '--flag-switches-begin' in args or '--flag-switches-end' in args:
        if '--flag-switches-begin' not in args or '--flag-switches-end' not in args:
            return False
        if args.index('--flag-switches-end') != args.index('--flag-switches-begin') + 1:
            return False
    return True


class DenyProxy(BaseHTTPRequestHandler):
    def deny(self):
        target = urlsplit('https://' + self.path if self.command == 'CONNECT' else self.path)
        self.server.denied.append({'method': self.command, 'host': target.hostname, 'port': target.port})
        self.send_response(403)
        self.send_header('Connection', 'close')
        self.send_header('Content-Length', '0')
        self.end_headers()
        self.close_connection = True
    do_CONNECT = do_GET = do_POST = do_HEAD = do_OPTIONS = deny
    def log_message(self, *_args):
        pass


def remove_owned(root: Path, parent: Path, owner: str):
    root, parent = plain(root), plain(parent)
    if root.parent != parent or not root.name.startswith('security-runtime-'):
        raise ValueError('Temporary cleanup escaped the owned parent')
    if (root / '.security-runtime-owner').read_text() != owner:
        raise ValueError('Temporary cleanup ownership mismatch')
    for directory, names, files in os.walk(root, followlinks=False):
        for name in (*names, *files):
            plain(Path(directory) / name)
    shutil.rmtree(root)
    if root.exists():
        raise ValueError('Owned temporary directory remains')


def probe(browser: Path, root: Path, report: dict):
    import psutil
    from playwright.sync_api import sync_playwright
    checks, owned = report['checks'], {}
    server = ThreadingHTTPServer(('127.0.0.1', 0), DenyProxy)
    server.daemon_threads = True
    server.denied = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = None
    def retain_children():
        for process in psutil.Process().children(recursive=True):
            try:
                owned[(process.pid, process.create_time())] = process
            except psutil.NoSuchProcess:
                pass
    environment = {key: value for key, value in os.environ.items() if not re.search(
        r'TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL|SSLKEYLOGFILE|^PWDEBUG$|^DEBUG$|^(GOOGLE_|GH_|GITHUB_|CHROMIX_|UXR_|CLOAK_|CODEX_|OPENAI_|ANTHROPIC_|AWS_|AZURE_)', key, re.I)}
    environment.update(TEMP=str(root), TMP=str(root), TMPDIR=str(root))
    profile = root / 'profile'
    profile.mkdir()
    options = launch_options(browser, profile, server.server_port, environment)
    report['launch_policy'] = {key: value for key, value in options.items() if key != 'env'}
    report['reviewed_kernel_added_arguments'] = sorted(KERNEL_ADDED_ARGS)
    report['pages'], report['preferences'] = {}, {'available': False, 'values': {}}
    previous_environment = dict(os.environ)
    os.environ.clear()
    os.environ.update(environment)  # The Playwright driver inherits this too.
    try:
        with sync_playwright() as playwright:
            try:
                context = playwright.chromium.launch_persistent_context(str(profile), **options)
                retain_children()
                session = context.browser.new_browser_cdp_session()
                actual = session.send('Browser.getBrowserCommandLine')['arguments']
                version = session.send('Browser.getVersion')['product']
                report['actual_arguments'], report['browser_version'] = actual, version
                checks.append({'name': 'exact_browser_version', 'passed': version.split('/')[-1] == VERSION})
                # No default security-disable argument, hidden debug port or
                # extra profile path can be accepted behind the explicit list.
                checks.append({'name': 'exact_safe_launch_arguments', 'passed':
                    arguments_match(actual, options['args'], browser)})
                for name, url in URLS.items():
                    page = context.new_page()
                    try:
                        page.goto(url, wait_until='domcontentloaded', timeout=15000)
                        page.wait_for_timeout(400)
                        if name == 'settings_security':
                            report['preferences'] = page.evaluate(READ_PREFS, list(EXPECTED_PREFS))
                            try:
                                page.wait_for_function(
                                    '(' + READ_PAGE + ')().safeBrowsingSectionVisible', timeout=4000)
                            except Exception:
                                pass
                        report['pages'][name] = page.evaluate(READ_PAGE)
                    except Exception as error:
                        report['pages'][name] = {'url': page.url, 'error': str(error)}
                    finally:
                        page.close()
                checks.extend(assess_pages(report['pages'], report['preferences']))
            finally:
                retain_children()
                if context:
                    context.close()
    finally:
        # These objects originate only from this dedicated helper's process
        # tree. psutil retains PID/create-time identity before terminate/kill.
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
            checks.append({'name': 'owned_processes_closed', 'passed': not remaining,
                           'forced_cleanup': forced, 'remaining_pids': [p.pid for p in remaining]})
        except Exception as error:
            checks.append({'name': 'owned_processes_closed', 'passed': False, 'error': str(error)})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            checks.append({'name': 'deny_proxy_closed', 'passed': not thread.is_alive()})
            report['network'] = {'proxy': '127.0.0.1:' + str(server.server_port),
                                 'forwarded_requests': 0, 'denied_requests': server.denied}
            os.environ.clear()
            os.environ.update(previous_environment)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--source-record', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    destination = plain(args.report)
    if destination.exists():
        parser.error('Use a new report path; prior failures are retained')
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'status': 'failed', 'checks': [],
              'scope': 'Fresh account-free Windows 156 security restoration runtime gate',
              'online_safe_browsing_verdict': 'not_tested', 'real_accounts': 'not_tested',
              'preferences_changed': False, 'trust_store_changed': False}
    root = None
    try:
        if os.name != 'nt':
            raise ValueError('This acceptance lane requires Windows')
        browser = plain(args.browser)
        dll = plain(browser.with_name('chrome.dll'))
        if not browser.is_file() or not dll.is_file():
            raise ValueError('Supply the exact complete Windows browser')
        recipe = build_record.recipe(REPO)
        report['source'] = source_identity(args.source_record, recipe)
        report['binding'] = {'browser_sha256': digest(browser), 'chrome_dll_sha256': digest(dll),
            'verifier_sha256': digest(Path(__file__)), 'recipe_tool_sha256': digest(Path(build_record.__file__)),
            'requirements_sha256': digest(REPO / 'tools/google-services-requirements.txt'),
            'python_sha256': digest(Path(sys.executable)), 'dependencies': dependency_identity()}
        root = Path(tempfile.mkdtemp(prefix='security-runtime-', dir=destination.parent))
        report['temporary_root'] = str(root)
        owner = secrets.token_hex(32)
        (root / '.security-runtime-owner').write_text(owner)
        probe(browser, root, report)
        report['checks'].append({'name': 'bindings_unchanged', 'passed':
            digest(browser) == report['binding']['browser_sha256']
            and digest(dll) == report['binding']['chrome_dll_sha256']
            and digest(Path(__file__)) == report['binding']['verifier_sha256']
            and digest(Path(build_record.__file__)) == report['binding']['recipe_tool_sha256']
            and digest(REPO / 'tools/google-services-requirements.txt') == report['binding']['requirements_sha256']
            and dependency_identity() == report['binding']['dependencies']
            and source_identity(args.source_record, build_record.recipe(REPO)) == report['source']})
    except Exception as error:
        report['error'] = str(error)
    finally:
        if root:
            try:
                if not any(row['name'] == 'owned_processes_closed' and row['passed'] is True for row in report['checks']):
                    raise ValueError('Retaining temporary directory while owned processes remain')
                remove_owned(root, destination.parent, owner)
                report['checks'].append({'name': 'owned_profile_removed', 'passed': True})
            except Exception as error:
                report['checks'].append({'name': 'owned_profile_removed', 'passed': False, 'error': str(error)})
        if report_passed(report):
            report['status'] = 'passed'
        with destination.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
        print(json.dumps({'status': report['status'], 'report': str(destination),
                          'failed': [row['name'] for row in report['checks'] if not row['passed']],
                          'error': report.get('error')}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
