"""Exact Windows 156 native suites bound to the immutable pretest build record."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re

try:
    from .verify_native_browser_tests import REQUIRED as BROWSER_TESTS
    from .verify_native_cookie_tests import REQUIRED as COOKIE_TESTS
except ImportError:
    from verify_native_browser_tests import REQUIRED as BROWSER_TESTS
    from verify_native_cookie_tests import REQUIRED as COOKIE_TESTS

HEX = re.compile(r'[a-f0-9]{64}')
COMMIT = re.compile(r'[a-f0-9]{40}')
VERSION = '156.0.8078.79'
VARIANT = 'fingerprint-modes-win156'
TEST_EXECUTABLE = 'native_passkey_unittests.exe'

# The frozen list comes from the actual Chromium 156 v3 native execution, not
# a list supplied by a candidate under evaluation. Audit records retain bytes.
PASSKEY_SUITE_SOURCE_SHA256 = 'd1e9c617ca04e7ab919002e4e05b12e01658d62a45e5a436b9d3c97548382802'
PASSKEY_TESTS = frozenset([
    'AuthenticatorRequestDialogControllerTest.AdvanceThroughCableV2States',
    'AuthenticatorRequestDialogControllerTest.AdvanceThroughCableV2StatesStopTimer',
    'AuthenticatorRequestDialogControllerTest.AwaitingAcknowledgement',
    'AuthenticatorRequestDialogControllerTest.Cable2ndFactorFlows',
    'AuthenticatorRequestDialogControllerTest.ConditionalUICancelRequest',
    'AuthenticatorRequestDialogControllerTest.ConditionalUINoRecognizedCredential',
    'AuthenticatorRequestDialogControllerTest.ConditionalUIRecognizedCredential',
    'AuthenticatorRequestDialogControllerTest.ConditionalUIWindowsCancel',
    'AuthenticatorRequestDialogControllerTest.Crbug1503187',
    'AuthenticatorRequestDialogControllerTest.DeduplicateAccounts',
    'AuthenticatorRequestDialogControllerTest.GpmDisabledForCreate',
    'AuthenticatorRequestDialogControllerTest.HybridButtonLabel',
    'AuthenticatorRequestDialogControllerTest.JumpToWindowsWithNewUI',
    'AuthenticatorRequestDialogControllerTest.ListICloudKeychainPasskeysInConditionalUI',
    'AuthenticatorRequestDialogControllerTest.Mechanisms',
    'AuthenticatorRequestDialogControllerTest.MechanismsFromUserAccounts',
    'AuthenticatorRequestDialogControllerTest.NoAvailableTransports',
    'AuthenticatorRequestDialogControllerTest.NoICloudKeychainMechanism_ModalImmediate_UnknownCredStatus',
    'AuthenticatorRequestDialogControllerTest.PopulatePasswordsWithOrigin',
    'AuthenticatorRequestDialogControllerTest.PreSelect',
    'AuthenticatorRequestDialogControllerTest.PreSelectWithMultipleTransportsOnCredential',
    'AuthenticatorRequestDialogControllerTest.WinCancel',
    'AuthenticatorRequestDialogControllerTest.WinCancel_AfterMatchingLocalCred',
    'AuthenticatorRequestDialogControllerTest.WinCredMatchEmptyAllowList',
    'AuthenticatorRequestDialogControllerTest.WinNoPlatformAuthenticator',
    'AuthenticatorRequestDialogControllerTest.WinSingleCachedCredentialPreservesOriginalAllowList',
    'AuthenticatorRequestDialogControllerTest.WindowsHelloButtonLabel_GetAssertion',
    'AuthenticatorRequestDialogControllerTest.WindowsHelloButtonLabel_MakeCredential',
    'WinAuthenticatorTest.EnumeratePlatformCredentials_NotSupported',
    'WinAuthenticatorTest.EnumeratePlatformCredentials_ScopedToRp',
    'WinAuthenticatorTest.EnumeratePlatformCredentials_Supported',
    'WinAuthenticatorTest.GetAssertionLargeBlobError',
    'WinAuthenticatorTest.GetAssertionLargeBlobNotSupported',
    'WinAuthenticatorTest.GetAssertionLargeBlobSuccess',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_HasCredentials',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_Incognito',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_IsAsynchronous',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_NativeProvidersEmptyCache',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_NativeProvidersFilteredCache',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_NativeProvidersPreservesMatch',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_NativeProvidersRespectsTransports',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_NativeProvidersUnavailableBeforeV9',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_NoCredentials',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_NonEmptyAllowList_Found',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_NonEmptyAllowList_Internal',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_NonEmptyAllowList_NoInternal',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_NonEmptyAllowList_NotMatching',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_Rdp',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_UnknownError',
    'WinAuthenticatorTest.GetCredentialInformationForRequest_Unsupported',
    'WinAuthenticatorTest.HmacSecret',
    'WinAuthenticatorTest.HmacSecretMakeCredentialAvailability',
    'WinAuthenticatorTest.MakeCredentialLargeBlob',
    'WinAuthenticatorTest.MakeCredentialLargeBlobAttachmentUndefined',
    'WinAuthenticatorTest.PrfOnGetAssertionNotSupported',
    'WinAuthenticatorTest.PrfOnMakeCredentialNotSupported',
    'WinAuthenticatorTest.PrfRoundtripOneValue',
    'WinAuthenticatorTest.PrfRoundtripTwoValues',
    'WinAuthenticatorTest.SignalAllAcceptedCredentials_Found',
    'WinAuthenticatorTest.SignalAllAcceptedCredentials_NoMatchingRpId',
    'WinAuthenticatorTest.SignalAllAcceptedCredentials_NoMatchingUserId',
    'WinAuthenticatorTest.SignalAllAcceptedCredentials_NotFound',
    'WinAuthenticatorTest.SignalAllAcceptedCredentials_NotSupported',
    'WinAuthenticatorTest.SignalUnknownCredential',
    'WinAuthenticatorTest.SignalUnknownCredential_NotFound',
    'WinAuthenticatorTest.SignalUnknownCredential_NotSupported',
    'WinAuthenticatorTest.SignalUnknownCredential_WrongRpId',
])
REQUIRED_TESTS = {
    'passkey': PASSKEY_TESTS,
    'browser': frozenset(BROWSER_TESTS),
    'cookie': frozenset(COOKIE_TESTS),
}
TEST_FILTERS = {
    'passkey': 'WinAuthenticatorTest.*:AuthenticatorRequestDialogControllerTest.*',
    'browser': 'ShardXNativeBrowserTest.*',
    'cookie': 'ShardXNativeCookieTest.*',
}


def _plain(path):
    path = Path(path).absolute()
    if any(item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction())
           for item in (path, *path.parents)) or not path.is_file():
        raise ValueError('Native test evidence must be a plain regular file')
    return path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate native evidence JSON property')
        result[key] = value
    return result


def _read(path):
    path = _plain(path)
    if path.stat().st_size > 4 * 1024**2:
        raise ValueError('Native test evidence is too large')
    with path.open('rb') as stream:
        raw = stream.read(4 * 1024**2 + 1)
    if len(raw) > 4 * 1024**2:
        raise ValueError('Native test evidence is too large')
    data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object)
    if not isinstance(data, dict):
        raise ValueError('Native test evidence must be an object')
    return data, hashlib.sha256(raw).hexdigest()


def _utc(value):
    if not isinstance(value, str):
        raise ValueError('Native evidence timestamps must be explicit UTC strings')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('Native evidence timestamp is invalid') from error
    if stamp.utcoffset() != timedelta(0):
        raise ValueError('Native evidence timestamps must use UTC')
    return stamp


def validate_compiled_suite(summary_path, launcher_path, build_record_path, kind):
    if kind not in REQUIRED_TESTS:
        raise ValueError('Unknown Windows 156 native suite')
    required = REQUIRED_TESTS[kind]
    if {name: len(tests) for name, tests in REQUIRED_TESTS.items()} != {'passkey': 67, 'browser': 9, 'cookie': 10}:
        raise ValueError('Frozen Windows 156 native suite inventory changed')
    summary, summary_hash = _read(summary_path)
    launcher, launcher_hash = _read(launcher_path)
    build, build_hash = _read(build_record_path)
    iterations = summary.get('per_iteration_data')
    if (not isinstance(iterations, list) or len(iterations) != 1 or not isinstance(iterations[0], dict)
            or set(iterations[0]) != required
            or any(not isinstance(attempts, list) or len(attempts) != 1
                   or not isinstance(attempts[0], dict) or attempts[0].get('status') != 'SUCCESS'
                   for attempts in iterations[0].values())):
        raise ValueError('Native suite must contain its exact frozen tests, each passing once without retry')
    executable = build.get('native_test_executable')
    if (type(build.get('schema_version')) is not int or build['schema_version'] != 1
            or build.get('build_variant') != VARIANT or build.get('chromium_version') != VERSION
            or not COMMIT.fullmatch(str(build.get('source_commit', '')))
            or type(build.get('run_id')) is not int or build['run_id'] <= 0
            or any(not HEX.fullmatch(str(build.get(key, ''))) for key in ('preparation_sha256', 'gn_args_sha256'))
            or not isinstance(executable, dict) or executable.get('name') != TEST_EXECUTABLE
            or type(executable.get('bytes')) is not int or executable['bytes'] <= 0
            or not HEX.fullmatch(str(executable.get('sha256', '')))):
        raise ValueError('Immutable pretest build record lacks the exact native test executable/source binding')
    if (type(launcher.get('schema_version')) is not int or launcher['schema_version'] != 1
            or launcher.get('status') != 'passed' or launcher.get('test_filter') != TEST_FILTERS[kind]
            or str(launcher.get('executable', '')).replace('\\', '/').rsplit('/', 1)[-1] != TEST_EXECUTABLE
            or launcher.get('executable_sha256') != executable['sha256']
            or type(launcher.get('executable_bytes')) is not int or launcher['executable_bytes'] != executable['bytes']
            or launcher.get('build_record_sha256') != build_hash
            or launcher.get('summary_sha256') != summary_hash
            or type(launcher.get('run_id')) is not int
            or any(launcher.get(key) != build[key] for key in ('source_commit', 'run_id', 'preparation_sha256', 'gn_args_sha256'))
            or type(launcher.get('native_exit_code')) is not int or launcher['native_exit_code'] != 0
            or type(launcher.get('host_process_id')) is not int or not 1 <= launcher['host_process_id'] <= 0xffffffff
            or launcher.get('startup_assertions_unchanged') is not True
            or launcher.get('test_host_priority') != 'Normal'
            or launcher.get('original_host_priority') not in ('Normal', 'Idle', 'BelowNormal', 'AboveNormal', 'High', 'RealTime')
            or launcher.get('restored_host_priority') != launcher['original_host_priority']
            or launcher.get('error') or launcher.get('priority_restore_error')):
        raise ValueError('Native launcher evidence does not bind this fresh execution to the immutable build')
    created, started, completed = (_utc(build.get('created_at_utc')),
                                   _utc(launcher.get('started_at_utc')), _utc(launcher.get('completed_at_utc')))
    if not created <= started <= completed:
        raise ValueError('Native tests must start after the pretest build record and finish after starting')
    # The aggregate candidate gate checks these again after all runtime work.
    # This local check also protects direct consumers against mid-read mutation.
    for path, expected in ((summary_path, summary_hash), (launcher_path, launcher_hash), (build_record_path, build_hash)):
        with _plain(path).open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError('Native compiled evidence changed while validating it')
    return {'status': 'passed', 'tests': sorted(required), 'required_count': len(required),
            'summary_sha256': summary_hash, 'launcher_sha256': launcher_hash,
            'test_executable_sha256': executable['sha256'], 'build_record_sha256': build_hash,
            'started_at_utc': launcher['started_at_utc'], 'completed_at_utc': launcher['completed_at_utc']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--launcher', type=Path, required=True)
    parser.add_argument('--build-record', type=Path, required=True)
    parser.add_argument('--kind', choices=sorted(REQUIRED_TESTS), required=True)
    args = parser.parse_args(argv)
    result = validate_compiled_suite(args.summary, args.launcher, args.build_record, args.kind)
    print(json.dumps({'status': result['status'], 'kind': args.kind, 'required_count': result['required_count']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
