#!/usr/bin/env python3
"""Check Google AppID compatibility against local HTTPS fixtures, without accounts.

Uses an explicitly supplied browser, an ephemeral certificate and a synthetic
CTAP2 authenticator. Nothing is installed into the Windows certificate store.
The report deliberately does not claim Windows Hello or Google cloud access.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import tempfile
import threading
from urllib.parse import urlsplit

HOSTS = ('accounts.google.com', 'webauthn.example.test')
APPIDS = ('https://www.gstatic.com/securitykey/origins.json',
          'https://www.gstatic.com/securitykey/a/google.com/origins.json')
CREATE = """async ({rp, extensions}) => {
  try {
    const credential = await navigator.credentials.create({signal:AbortSignal.timeout(10000),publicKey:{
      challenge:crypto.getRandomValues(new Uint8Array(32)),
      rp:{id:rp,name:'Chromix local fixture'},
      user:{id:crypto.getRandomValues(new Uint8Array(16)),name:'synthetic',displayName:'Synthetic test'},
      pubKeyCredParams:[{type:'public-key',alg:-7}],
      authenticatorSelection:{residentKey:'required',userVerification:'required'},
      timeout:5000,attestation:'none',extensions
    }});
    return {ok:!!credential,id:credential?.id};
  } catch(error) { return {ok:false,error:error.name,message:error.message}; }
}"""
GET = """async ({rp, id}) => {
  const raw = Uint8Array.from(atob(id.replaceAll('-','+').replaceAll('_','/')),c=>c.charCodeAt(0));
  try {
    const credential = await navigator.credentials.get({signal:AbortSignal.timeout(10000),publicKey:{rpId:rp,
      challenge:crypto.getRandomValues(new Uint8Array(32)),
      allowCredentials:[{type:'public-key',id:raw}],userVerification:'required',timeout:5000}});
    return {ok:credential?.id===id};
  } catch(error) { return {ok:false,error:error.name,message:error.message}; }
}"""


def sha256(path: Path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def local_server(root: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Chromix local test only')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host) for host in HOSTS]), False)
            .sign(key, hashes.SHA256()))
    cert_file, key_file = root / 'fixture.pem', root / 'fixture-key.pem'
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public_key = key.public_key().public_bytes(serialization.Encoding.DER,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)
    spki = base64.b64encode(hashlib.sha256(public_key).digest()).decode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get('Host', '').split(':')[0] not in HOSTS:
                self.send_error(403)
                return
            body = b'<!doctype html><title>Chromix local WebAuthn fixture</title><p>Synthetic test; no account login.</p>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_file, key_file)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, spki


def virtual_authenticator(context, page, credentials=()):
    session = context.new_cdp_session(page)
    session.send('WebAuthn.enable', {'enableUI': False})
    authenticator = session.send('WebAuthn.addVirtualAuthenticator', {'options': {
        'protocol': 'ctap2', 'transport': 'internal', 'hasResidentKey': True,
        'hasUserVerification': True, 'isUserVerified': True,
        'automaticPresenceSimulation': True}})['authenticatorId']
    for credential in credentials:
        session.send('WebAuthn.addCredential', {'authenticatorId': authenticator, 'credential': credential})
    return session, authenticator


def verify(browser: Path, report, root: Path):
    from playwright.sync_api import sync_playwright
    server, thread, spki = local_server(root)
    port = server.server_port
    checks = report['checks']

    def record(name, result, expected=True, allowed_errors=('SecurityError',)):
        passed = result.get('ok') is True if expected else result.get('error') in allowed_errors
        # Never export authenticator private keys, credentials, cookies or tokens.
        checks.append({'name': name, 'passed': passed,
                       **({'error': result.get('error'), 'message': result.get('message')} if not passed else {})})

    def restrict(route):
        url = urlsplit(route.request.url)
        if url.scheme == 'https' and url.hostname in HOSTS and url.port == port:
            route.continue_()
        else:
            route.abort()

    env = {name: value for name, value in os.environ.items()
           if name not in ('GOOGLE_API_KEY', 'GOOGLE_DEFAULT_CLIENT_ID', 'GOOGLE_DEFAULT_CLIENT_SECRET',
                           'CHROMIX_GOOGLE_API_CREDENTIALS')}
    arguments = ['--fingerprint=42', '--fingerprint-pixel-noise=seeded', '--no-proxy-server',
                 '--disable-background-networking', '--disable-sync', '--disable-component-update',
                 # WebAuthn related-origin lookups originate in the browser
                 # process and bypass Playwright routing. Deny all other DNS.
                 '--host-resolver-rules=' + ','.join('MAP ' + host + ' 127.0.0.1' for host in HOSTS) + ',MAP * ~NOTFOUND',
                 '--ignore-certificate-errors-spki-list=' + spki]
    profile = root / 'profile'
    context = None
    try:
        with sync_playwright() as playwright:
            def launch():
                opened = playwright.chromium.launch_persistent_context(str(profile), executable_path=str(browser),
                    headless=True, chromium_sandbox=True, args=arguments, env=env, timeout=60000)
                opened.route('**/*', restrict)
                opened.set_default_timeout(10000)
                return opened

            try:
                context = launch()
                page = context.pages[0]
                session, authenticator = virtual_authenticator(context, page)
                report['browser_version'] = session.send('Browser.getVersion')['product']
                page.goto(f'https://{HOSTS[1]}:{port}/')
                record('secure_context', {'ok': page.evaluate('isSecureContext')})
                ordinary = page.evaluate(CREATE, {'rp': HOSTS[1], 'extensions': {}})
                record('ordinary_registration', ordinary)
                if ordinary.get('ok'):
                    record('ordinary_authentication', page.evaluate(GET, {'rp': HOSTS[1], 'id': ordinary['id']}))
                # New Chromium versions may report failed related-origin lookup
                # as NotAllowedError instead of SecurityError.
                record('foreign_rp_rejected', page.evaluate(CREATE, {'rp': 'google.com', 'extensions': {}}),
                       False, ('SecurityError', 'NotAllowedError'))
                page.goto(f'https://{HOSTS[0]}:{port}/')
                google_credential = None
                credentials = []
                for name, extensions in (
                        ('google_registration', {}),
                        ('google_appid_exclude', {'appidExclude': APPIDS[0]}),
                        ('google_corporate_appid_exclude', {'appidExclude': APPIDS[1]}),
                        ('google_legacy_appid', {'googleLegacyAppidSupport': True})):
                    session.send('WebAuthn.clearCredentials', {'authenticatorId': authenticator})
                    page.reload()
                    result = page.evaluate(CREATE, {'rp': 'google.com', 'extensions': extensions})
                    record(name, result)
                    if name == 'google_appid_exclude' and result.get('ok'):
                        google_credential = result['id']
                        credentials = session.send('WebAuthn.getCredentials', {'authenticatorId': authenticator})['credentials']
                record('foreign_appid_rejected', page.evaluate(CREATE, {'rp': 'google.com',
                    'extensions': {'appidExclude': 'https://unrelated.example.test/origins.json'}}), False)
                record('appid_fragment_rejected', page.evaluate(CREATE, {'rp': 'google.com',
                    'extensions': {'appidExclude': APPIDS[0] + '#invalid'}}), False)
                page.evaluate("localStorage.setItem('chromix-fixture','persisted');document.cookie='fixture=persisted;Secure;SameSite=Strict;Max-Age=3600'")
                context.close()
                context = launch()
                page = context.pages[0]
                virtual_authenticator(context, page, credentials)
                page.goto(f'https://{HOSTS[0]}:{port}/')
                record('profile_storage_restart', {'ok': page.evaluate("localStorage.getItem('chromix-fixture')==='persisted'&&document.cookie.includes('fixture=persisted')")})
                if google_credential:
                    record('google_authentication_after_restart', page.evaluate(GET, {'rp': 'google.com', 'id': google_credential}))
                else:
                    record('google_authentication_after_restart', {'ok': False, 'error': 'RegistrationFailed'})
            finally:
                if context:
                    context.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    report['status'] = 'passed' if checks and all(check['passed'] for check in checks) else 'failed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    browser = args.browser.resolve(strict=True)
    report = {'schema_version': 1, 'status': 'failed', 'browser_sha256': sha256(browser), 'checks': [],
              'scope': 'local HTTPS fixtures and synthetic CTAP2; no real Google account',
              'virtual_authenticator_rehydrated_after_restart': True,
              'windows_hello': 'not_tested', 'google_signin_and_sync': 'not_tested',
              'google_password_manager_cloud_passkeys': 'not_tested'}
    dll = browser.with_name('chrome.dll')
    if dll.is_file():
        report['chrome_dll_sha256'] = sha256(dll)
    try:
        with tempfile.TemporaryDirectory(prefix='chromix-google-webauthn-') as temporary:
            verify(browser, report, Path(temporary))
    except Exception as error:
        report['error'] = str(error)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'checks': report['checks'], 'report': str(args.report)}, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
