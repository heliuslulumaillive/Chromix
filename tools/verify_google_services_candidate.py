#!/usr/bin/env python3
"""Verify an existing Actions candidate; never restore source or invoke a compiler."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

from fingerprint_subprocess import run_command
from verify_windows_bundle import extract_archive, runtime_smoke, sha256

REPO = Path(__file__).resolve().parents[1]
VERSION = '155.0.8059.39'
REQUIRED_CHECKS = {
    'secure_context', 'ordinary_registration', 'ordinary_authentication',
    'foreign_rp_rejected', 'google_registration', 'google_appid_exclude',
    'google_corporate_appid_exclude', 'google_legacy_appid',
    'foreign_appid_rejected', 'appid_fragment_rejected',
    'profile_storage_restart', 'google_authentication_after_restart',
}


def read_object(path):
    if path.stat().st_size > 512 * 1024:
        raise ValueError('Candidate metadata is too large')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Candidate metadata must be an object')
    return value


def validate_source_run(source):
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', str(source.get('repository', '')))
            or type(source.get('run_id')) is not int or source['run_id'] <= 0
            or type(source.get('artifact_id')) is not int or source['artifact_id'] <= 0
            or not re.fullmatch('[0-9a-f]{40}', str(source.get('head_sha', '')))
            or source.get('artifact_name') != 'google-services-win155-x64'):
        raise ValueError('Invalid source run or artifact identity')


def validate_source_recipe(inner, outer, repo=REPO):
    if inner != outer:
        raise ValueError('Packaged source receipt differs from the artifact receipt')
    if inner.get('variant') != 'google-services-self-use' or inner.get('chromium_version') != VERSION:
        raise ValueError('Candidate is not the expected Google-service Windows 155 variant')
    if inner.get('patch_sha256') != sha256(repo / 'patches/google-services/0001-restore-google-services.patch'):
        raise ValueError('Candidate was built with a different Google restoration patch')
    inputs = inner.get('preparation_inputs', {})
    if not isinstance(inputs, dict):
        raise ValueError('Candidate preparation inputs must be an object')
    for name in ('build/google-services-win155.json',
                 'patches/google-services/0001-restore-google-services.json',
                 'patches/chromium155-windows/0233-renderer-timezone-webstring-api.patch'):
        if inputs.get(name) != sha256(repo / name):
            raise ValueError('Candidate source inputs do not match: ' + name)


def inventory(bundle):
    return {path.relative_to(bundle).as_posix(): sha256(path)
            for path in sorted(bundle.rglob('*')) if path.is_file()}


def verify_candidate(artifact_dir, source, report_dir, record):
    validate_source_run(source)
    archive = artifact_dir / 'chromix-win-x64.zip'
    manifest = artifact_dir / 'SHA256SUMS'
    bundle_report = extract_archive(archive, manifest, report_dir.parent / 'verified-browser', 'x64')
    bundle = Path(bundle_report['bundle_dir'])
    inner = read_object(bundle / 'google-services-source.json')
    validate_source_recipe(inner, read_object(artifact_dir / 'google-services-source.json'))
    record.update(source=source, archive_sha256=bundle_report['archive']['sha256'],
                  compiler_invoked=False, source_checkpoint_restored=False,
                  built_source_commit=source['head_sha'],
                  verification_commit=os.environ.get('GITHUB_SHA'),
                  qualification='Tests of the original source-run artifact; not a rebuild of the verification commit')
    before = inventory(bundle)
    native = runtime_smoke(bundle_report, VERSION, report_dir / 'native-smoke')
    (report_dir / 'native-smoke.json').write_text(json.dumps(native, indent=2) + '\n', encoding='utf-8')
    probe_report = report_dir / 'google-webauthn.json'
    execution = run_command(
        [sys.executable, '-B', '-X', 'utf8', str(REPO / 'tools/verify_google_services_runtime.py'),
         '--browser', str(bundle / 'chrome.exe'), '--report', str(probe_report)],
        timeout=180, log=report_dir / 'google-webauthn.log', cwd=REPO)
    record['probe_process'] = execution
    if execution['exit_code'] != 0 or execution['timed_out'] or execution['cleanup_errors']:
        raise ValueError('Google WebAuthn runtime verification failed; see probe evidence')
    probe = read_object(probe_report)
    checks = probe.get('checks', [])
    passed = {check.get('name') for check in checks if check.get('passed') is True}
    if (probe.get('status') != 'passed' or not REQUIRED_CHECKS.issubset(passed)
            or any(check.get('passed') is not True for check in checks)
            or probe.get('browser_sha256') != before['chrome.exe']
            or probe.get('chrome_dll_sha256') != before['chrome.dll']):
        raise ValueError('Runtime evidence is incomplete or belongs to another browser')
    if before != inventory(bundle) or sha256(archive) != record['archive_sha256']:
        raise ValueError('Candidate bytes changed during verification')
    record.update(status='passed', files=before, runtime_checks=len(checks),
                  google_account_and_cloud_access='not_tested', windows_hello='not_tested',
                  google_password_manager_cloud_passkeys='not_tested')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-dir', type=Path, required=True)
    parser.add_argument('--source-run-json', type=Path, required=True)
    parser.add_argument('--report-dir', type=Path, required=True)
    args = parser.parse_args()
    args.report_dir.mkdir(parents=True, exist_ok=False)
    record = {'schema_version': 1, 'status': 'failed', 'compiler_invoked': False,
              'source_checkpoint_restored': False}
    try:
        verify_candidate(args.artifact_dir, read_object(args.source_run_json), args.report_dir, record)
    except Exception as error:
        record['error'] = str(error)
    (args.report_dir / 'verification.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    summary = {'status': record['status'], 'source': record.get('source'),
               'compiler_invoked': False, 'error': record.get('error')}
    print(json.dumps(summary, indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write('\nExisting candidate verification (no compilation):\n```json\n' + json.dumps(summary, indent=2) + '\n```\n')
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
