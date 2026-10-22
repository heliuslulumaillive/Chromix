#!/usr/bin/env python3
"""Verify an existing Actions candidate; never restore source or invoke a compiler."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from fingerprint_subprocess import run_command
from verify_windows_bundle import extract_archive, runtime_smoke, sha256

REPO = Path(__file__).resolve().parents[1]
VERSION = '155.0.8059.39'
GOOGLE_VARIANT = 'google-services'
FINGERPRINT_VARIANT = 'fingerprint-modes-win155'
ARTIFACT_NAMES = {
    GOOGLE_VARIANT: 'google-services-win155-x64',
    FINGERPRINT_VARIANT: 'fingerprint-modes-win155-x64',
}
FINGERPRINT_REPORTS = ('modes-native.json', 'modes-runtime.json', 'fingerprint-modes-capabilities.json')
FINGERPRINT_RUNTIME_SCENARIOS = {
    'native_off_control', 'all_native', 'legacy_seeded', 'canvas_shardx',
    'webgl_cloak', 'shard_surfaces', 'memory_custom', 'hardware_cloak', 'gpu_custom',
    'webgl_shardx', 'fonts_shard42', 'fonts_shard43', 'fonts_cloak10026', 'fonts_allowlists', 'fonts_empty',
    'cloak_voices', 'cloak_voices_no_local', 'cloak_offset', 'cloak_text_positive', 'cloak_audio_positive',
    'audio_defaults_44100', 'audio_defaults_32000', 'audio_default_mono', 'audio_default_stereo',
    'audio_default_32', 'gpu_masked_custom',
}
FINGERPRINT_POSITIVE_SCENARIOS = {
    'legacy_canvas': {'legacy_seeded'}, 'shard_canvas': {'canvas_shardx'}, 'cloak_canvas': {'hardware_cloak'},
    'shard_webgl': {'webgl_shardx'}, 'cloak_webgl': {'webgl_cloak'}, 'shard_audio': {'shard_surfaces'},
    'shard_rects': {'shard_surfaces'}, 'shard_text': {'shard_surfaces'}, 'shard_sensors': {'shard_surfaces'},
    'shard_media': {'shard_surfaces'}, 'memory_custom': {'memory_custom'}, 'hardware_profile': {'hardware_cloak'},
    'webgl_resource_limits': {'all_native', 'gpu_custom'}, 'webgpu_capabilities': {'gpu_custom', 'hardware_cloak'},
    'hardware_gpu_identity': {'hardware_cloak'}, 'masked_webgl': {'gpu_masked_custom'},
    'shard_fonts': {'fonts_shard42', 'fonts_shard43'}, 'cloak_fonts': {'fonts_cloak10026'},
    'font_allowlists': {'fonts_allowlists', 'fonts_empty'},
    'cloak_voices': {'cloak_voices', 'cloak_voices_no_local'}, 'cloak_offset': {'cloak_offset'},
    'cloak_text': {'cloak_text_positive'}, 'cloak_audio': {'cloak_audio_positive'},
    'audio_defaults': {'audio_defaults_44100', 'audio_defaults_32000'},
    'audio_channels': {'audio_defaults_44100', 'audio_defaults_32000', 'audio_default_mono',
                       'audio_default_stereo', 'audio_default_32'},
}
FINGERPRINT_EXTRA_SCOPES = {
    'all_native': ('fonts', 'reference', 'audio', 'renderbuffer'),
    'gpu_custom': ('renderbuffer',),
    **{name: ('fonts',) for name in ('fonts_shard42', 'fonts_shard43', 'fonts_cloak10026', 'fonts_allowlists', 'fonts_empty')},
    **{name: ('reference',) for name in ('cloak_voices', 'cloak_voices_no_local', 'cloak_offset', 'cloak_text_positive')},
    **{name: ('audio',) for name in ('cloak_audio_positive', 'audio_defaults_44100', 'audio_defaults_32000',
                                   'audio_default_mono', 'audio_default_stereo', 'audio_default_32')},
}
FINGERPRINT_BASIC_SCENARIOS = set(FINGERPRINT_EXTRA_SCOPES) - {'all_native', 'gpu_custom'}
REQUIRED_CHECKS = {
    'secure_context', 'ordinary_registration', 'ordinary_authentication',
    'foreign_rp_rejected', 'google_registration', 'google_appid_exclude',
    'google_corporate_appid_exclude', 'google_legacy_appid',
    'foreign_appid_rejected', 'appid_fragment_rejected',
    'profile_storage_restart', 'google_authentication_after_restart',
}


def read_object(path, *, max_bytes=512 * 1024):
    if path.stat().st_size > max_bytes:
        raise ValueError('Candidate metadata is too large')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Candidate metadata must be an object')
    return value


def validate_source_run(source, *, build_variant=GOOGLE_VARIANT):
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', str(source.get('repository', '')))
            or type(source.get('run_id')) is not int or source['run_id'] <= 0
            or type(source.get('artifact_id')) is not int or source['artifact_id'] <= 0
            or not re.fullmatch('[0-9a-f]{40}', str(source.get('head_sha', '')))
            or source.get('artifact_name') != ARTIFACT_NAMES[build_variant]):
        raise ValueError('Invalid source run or artifact identity')


def validate_source_recipe(inner, outer, repo=REPO, *, build_variant=GOOGLE_VARIANT):
    if inner != outer:
        raise ValueError('Packaged source receipt differs from the artifact receipt')
    if inner.get('variant') != 'google-services-self-use' or inner.get('chromium_version') != VERSION:
        raise ValueError('Candidate is not the expected Google-service Windows 155 variant')
    if build_variant == FINGERPRINT_VARIANT and inner.get('build_variant') != FINGERPRINT_VARIANT:
        raise ValueError('Candidate is not the fingerprint-modes build variant')
    if inner.get('patch_sha256') != sha256(repo / 'patches/google-services/0001-restore-google-services.patch'):
        raise ValueError('Candidate was built with a different Google restoration patch')
    inputs = inner.get('preparation_inputs', {})
    if not isinstance(inputs, dict):
        raise ValueError('Candidate preparation inputs must be an object')
    names = ['build/google-services-win155.json',
             'patches/google-services/0001-restore-google-services.json',
             'patches/google-services/0002-windows-native-passkey-discovery.patch',
             'patches/chromium155-windows/0233-renderer-timezone-webstring-api.patch']
    if build_variant == FINGERPRINT_VARIANT:
        names.append('build/fingerprint-modes-capabilities.json')
        config = read_object(repo / 'build/google-services-win155.json')
        for name in config.get('restoration_overlays', []):
            path = (repo / name).resolve()
            if not path.is_relative_to(repo.resolve()) or not path.is_file():
                raise ValueError('Invalid restoration overlay path')
            names.append(name)
    for name in dict.fromkeys(names):
        if inputs.get(name) != sha256(repo / name):
            raise ValueError('Candidate source inputs do not match: ' + name)


ORIGINAL_NATIVE_COUNTS = {
    'original_pixel_checks': 78,
    'original_reference_checks': 2660,
    'original_audio_checks': 337,
    'original_webgl_public_checks': 4327,
    'original_webgl_translated_checks': 32,
}
COMPATIBLE_RUNTIME_SCOPE = 'compatible-v1-covered-groups'
ORIGINAL_RUNTIME_SURFACES = {
    'canvas_pixels', 'webgl_pixels', 'canvas_references', 'webgl_references',
    'text', 'fonts', 'audio', 'voices', 'webgpu_identity', 'webgpu_features', 'webgpu_limits', 'memory', 'webgl_identity', 'webgl_capabilities', 'webgl_precision', 'webgl_extensions', 'webgl_translated_shaders',
}


def validate_original_runtime_scope(report):
    """This build runner has no original-mode browser scenarios or acceptance."""
    original = report.get('original_runtime_acceptance')
    if (report.get('full_strategy_acceptance_scope') != COMPATIBLE_RUNTIME_SCOPE
            or report.get('full_original_cloak_acceptance') is not False
            or not isinstance(original, dict)
            or set(original) != {'status', 'source_goldens_are_runtime_acceptance', 'surfaces'}
            or original.get('status') != 'not_tested'
            or original.get('source_goldens_are_runtime_acceptance') is not False
            or not isinstance(original.get('surfaces'), dict)
            or set(original['surfaces']) != ORIGINAL_RUNTIME_SURFACES):
        raise ValueError('Original runtime acceptance must remain explicitly not_tested in this runner')
    for name, surface in original['surfaces'].items():
        if (not isinstance(surface, dict) or set(surface) != {'status', 'evidence', 'reason'}
                or surface.get('status') != 'not_tested' or surface.get('evidence') != []
                or not isinstance(surface.get('reason'), str) or not surface['reason']):
            raise ValueError('Native/source goldens cannot supply original browser acceptance: ' + name)


def validate_mode_native(report, payload):
    expected = {
        'config-legacy': ('legacy', {'configuration_checks'}, '--config-legacy'),
        'config-chromix': ('chromix', {'configuration_checks'}, '--config-chromix'),
        'config-original': ('original', {*ORIGINAL_NATIVE_COUNTS, 'configuration_checks'}, '--config-original'),
        'production-helpers-and-config': ('production', {
            'graphics_cases', 'checks', 'gpu_checks', 'hardware_checks', 'font_allowlist_checks',
            'audio_default_checks', 'configuration_checks'}, None),
    }
    processes, groups = report.get('processes'), report.get('groups')
    if (report.get('status') != 'passed'
            or report.get('browser_sha256') != payload['chrome.exe']
            or report.get('chrome_dll_sha256') != payload['chrome.dll']
            or not isinstance(processes, list) or len(processes) != len(expected)
            or not isinstance(groups, dict) or set(groups) != {'legacy', 'chromix', 'original', 'production'}):
        raise ValueError('Native mode evidence is incomplete or bound to another payload')
    seen, binaries, counts = set(), set(), {}
    for process in processes:
        if not isinstance(process, dict) or process.get('label') not in expected:
            raise ValueError('Unexpected native mode process')
        label = process['label']
        group, fields, argument = expected[label]
        if (label in seen or type(process.get('exit_code')) is not int or process['exit_code'] != 0
                or process.get('timed_out') is not False
                or not re.fullmatch('[0-9a-f]{64}', str(process.get('executable_sha256', '')))):
            raise ValueError('Native mode process failed, timed out or is duplicated')
        seen.add(label)
        binaries.add(process['executable_sha256'])
        arguments = process.get('arguments')
        if label == 'config-original':
            valid_arguments = (isinstance(arguments, list) and len(arguments) == 2
                               and arguments[0] == '--config-original'
                               and isinstance(arguments[1], str)
                               and arguments[1].replace('\\', '/').endswith('/tools/tests/fixtures'))
        else:
            valid_arguments = not (not isinstance(arguments, list) or len(arguments) != 1
                or not isinstance(arguments[0], str) or not arguments[0]
                or (argument is not None and arguments != [argument])
                or (argument is None and not arguments[0].replace('\\', '/').endswith('/tools/tests/fixtures')))
        if not valid_arguments:
            raise ValueError('Native mode process has the wrong test arguments')
        output = process.get('stdout')
        if (not isinstance(output, str) or not output
                or hashlib.sha256(output.encode('utf-8')).hexdigest() != process.get('stdout_sha256')
                or not isinstance(process.get('stdout_file'), str) or not process['stdout_file']):
            raise ValueError('Native mode stdout identity is missing')
        records = [json.loads(line) for line in output.splitlines() if line.lstrip().startswith('{')]
        if not records or records != groups[group]:
            raise ValueError('Native mode groups differ from executed stdout')
        for record in records:
            if (not isinstance(record, dict) or type(record.get('failures')) is not int
                    or record['failures'] != 0 or record.get('status', 'passed') != 'passed'):
                raise ValueError('Native mode group failed or lacks a failure count')
        for field in fields:
            values = [record[field] for record in records if field in record]
            if len(values) != 1 or type(values[0]) is not int or values[0] <= 0:
                raise ValueError('Native mode group did not execute: ' + field)
            if group == 'original' and field in ORIGINAL_NATIVE_COUNTS and values[0] != ORIGINAL_NATIVE_COUNTS[field]:
                raise ValueError('Native mode group differs from the pinned suite: ' + field)
            counts[group + '.' + field] = values[0]
        if not any(r.get('status') == 'passed' and r.get('configuration_checks', 0) > 0 for r in records):
            raise ValueError('Native mode configuration acceptance is missing')
    if len(binaries) != 1:
        raise ValueError('Native mode processes used different executables')
    return counts


def validate_positive_coverage(report, checks):
    validate_original_runtime_scope(report)
    coverage, skipped = report.get('positive_acceptance'), report.get('not_tested')
    if not isinstance(coverage, dict) or set(coverage) != set(FINGERPRINT_POSITIVE_SCENARIOS):
        raise ValueError('Runtime positive strategy coverage is incomplete')
    if not isinstance(skipped, list):
        raise ValueError('Runtime unavailable conditions are missing')
    for record in [*checks, *skipped]:
        if not isinstance(record, dict):
            raise ValueError('Positive coverage evidence must be an object')
        group = record.get('acceptance_group')
        if group is not None and (group not in FINGERPRINT_POSITIVE_SCENARIOS or
                                  record.get('scenario') not in FINGERPRINT_POSITIVE_SCENARIOS[group]):
            raise ValueError('Positive evidence is attached to the wrong strategy')
    for record in skipped:
        if (not isinstance(record, dict) or record.get('scenario') not in FINGERPRINT_RUNTIME_SCENARIOS
                or not isinstance(record.get('name'), str) or not record['name']
                or not isinstance(record.get('reason'), str) or not record['reason']
                or (record.get('optional_candidate') is not None and
                    (record.get('optional_candidate') is not True or
                     record.get('acceptance_group') not in {'cloak_text', 'shard_fonts', 'cloak_fonts', 'font_allowlists'}))):
            raise ValueError('Unavailable positive condition has no concrete reason')
    full = True
    for group, value in coverage.items():
        if not isinstance(value, dict):
            raise ValueError('Invalid positive acceptance record')
        actual = [r for r in checks if r.get('acceptance_group') == group]
        missing = [r for r in skipped if r.get('acceptance_group') == group and r.get('optional_candidate') is not True]
        passed = sum(r.get('passed') is True for r in actual)
        expected = 'failed' if passed != len(actual) else 'not_tested' if not actual or missing else 'observed_pass'
        if (value.get('status') != expected or expected == 'failed'
                or type(value.get('eligible_count')) is not int or value['eligible_count'] != len(actual)
                or type(value.get('passed_count')) is not int or value['passed_count'] != passed
                or not isinstance(value.get('reason'), str) or not value['reason']
                or (not actual and not missing)
                or value.get('evidence') != [{'scenario': r['scenario'], 'name': r['name']} for r in actual]
                or value.get('missing') != [{'scenario': r['scenario'], 'name': r['name'], 'reason': r['reason']} for r in missing]):
            raise ValueError('Positive acceptance does not match executed checks and unavailable conditions: ' + group)
        full = full and expected == 'observed_pass'
    if (type(report.get('full_strategy_acceptance')) is not bool or report['full_strategy_acceptance'] != full
            or type(report.get('partial')) is not bool or report['partial'] != (not full)):
        raise ValueError('Runtime full/partial strategy acceptance is inconsistent')
    return coverage


def validate_mode_runtime(report, payload):
    checks, scenarios = report.get('checks'), report.get('scenarios')
    if (report.get('status') != 'passed' or report.get('baseline_only') is not False
            or report.get('browser_sha256') != payload['chrome.exe']
            or report.get('chrome_dll_sha256') != payload['chrome.dll']
            or not isinstance(checks, list) or not checks or report.get('failed') != []
            or not isinstance(scenarios, list) or len(scenarios) != len(FINGERPRINT_RUNTIME_SCENARIOS)
            or report.get('expected_scenarios') != len(FINGERPRINT_RUNTIME_SCENARIOS)
            or not isinstance(report.get('not_tested'), list)
            or not isinstance(report.get('coverage_limits'), list) or not report['coverage_limits']):
        raise ValueError('Runtime mode evidence is failed, empty, partial, or bound to another payload')
    executed = set()
    for check in checks:
        if (not isinstance(check, dict) or check.get('passed') is not True
                or check.get('scenario') not in FINGERPRINT_RUNTIME_SCENARIOS
                or not isinstance(check.get('name'), str) or not check['name']):
            raise ValueError('Runtime mode check failed or lacks identity')
        identity = (check['scenario'], check['name'])
        if identity in executed:
            raise ValueError('Runtime mode check is duplicated')
        executed.add(identity)
    seen = set()
    common = {'probe_completed', 'normal_owned_browser_shutdown', 'secure_loopback', 'no_internal_probe_errors',
              'window_worker_cpu_memory', 'probe_scope'}
    for scenario in scenarios:
        if not isinstance(scenario, dict) or scenario.get('id') not in FINGERPRINT_RUNTIME_SCENARIOS:
            raise ValueError('Unknown runtime mode scenario')
        identity = scenario['id']
        cleanup, probe = scenario.get('cleanup', {}), scenario.get('probe', {})
        level = 'basic' if identity in FINGERPRINT_BASIC_SCENARIOS else 'full'
        required_here = common if level == 'basic' else common | {
            'audio_alias_copy_preserves_unwritten_tail', 'analyser_preserves_unwritten_tails', 'synthetic_media_tracks'}
        if (identity in seen or scenario.get('error') or not isinstance(probe, dict)
                or probe.get('errors') != [] or probe.get('secureContext') is not True
                or probe.get('level') != level or scenario.get('probe_level') != level
                or not isinstance(cleanup, dict) or cleanup.get('normalExit') is not True
                or cleanup.get('closeSent') is not True or cleanup.get('forced') is not False
                or type(cleanup.get('exitCode')) is not int or cleanup['exitCode'] != 0
                or not cleanup.get('owned_tree_before_close')
                or cleanup.get('known_owned_processes_remaining') != []
                or cleanup.get('process_tree_error') is not None
                or any((identity, name) not in executed for name in required_here)):
            raise ValueError('Runtime mode scenario is incomplete or did not close normally')
        seen.add(identity)
        for scope in FINGERPRINT_EXTRA_SCOPES.get(identity, ()):
            extras = scenario.get('extra')
            value = extras.get(scope) if isinstance(extras, dict) else None
            if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
                    or (identity, scope + '_acceptance_probe_present') not in executed):
                raise ValueError('Positive browser probe evidence is missing: ' + identity + '/' + scope)
    required = {
        ('all_native', 'native_cpu_matches_off_control'), ('all_native', 'native_memory_matches_off_control'),
        ('all_native', 'native_ua_matches_off_control'), ('all_native', 'native_screen_matches_off_control'),
        ('legacy_seeded', 'selected_canvas_noise_reaches_runtime'),
        ('canvas_shardx', 'selected_canvas_noise_reaches_runtime'),
        ('shard_surfaces', 'audio_changed'), ('shard_surfaces', 'rect_mode_effect_and_bound'),
        ('shard_surfaces', 'text_mode_effect_and_bound'), ('shard_surfaces', 'media_filter_observable_on_fake_devices'),
        ('hardware_cloak', 'expected_cpu_and_memory'), ('memory_custom', 'expected_cpu_and_memory'),
        ('cloak_offset', 'offset_120_native_control'), ('cloak_offset', 'offset_120_captured_oracle'),
        ('cloak_offset', 'offset_50_native_control'), ('cloak_offset', 'offset_50_captured_oracle'),
    }
    required.update((scenario, name + '_matches_js')
                    for scenario in ('hardware_cloak', 'memory_custom')
                    for name in ('device-memory', 'sec-ch-device-memory'))
    if not required.issubset(executed):
        raise ValueError('Runtime mode routing coverage is incomplete')
    validate_positive_coverage(report, checks)
    return len(checks)


def validate_fingerprint_tests(report_path, bundle, payload_hashes=None):
    """Validate executed evidence, without rerunning an unavailable native exe."""
    report = read_object(report_path)
    payload = payload_hashes or {name: sha256(bundle / name) for name in ('chrome.exe', 'chrome.dll')}
    if (report.get('status') != 'passed'
            or report.get('browser_sha256') != payload['chrome.exe']
            or report.get('chrome_dll_sha256') != payload['chrome.dll']):
        raise ValueError('Fingerprint evidence failed or belongs to another browser')
    files = report.get('files', {})
    if not isinstance(files, dict) or set(files) != set(FINGERPRINT_REPORTS):
        raise ValueError('Fingerprint evidence must bind native, runtime and capability reports')
    for name in FINGERPRINT_REPORTS:
        path = report_path.parent / name
        if (not re.fullmatch('[0-9a-f]{64}', str(files[name])) or path.is_symlink()
                or not path.is_file() or sha256(path) != files[name]):
            raise ValueError('Fingerprint evidence file identity changed: ' + name)
    native = read_object(report_path.parent / 'modes-native.json', max_bytes=8 * 1024**2)
    runtime = read_object(report_path.parent / 'modes-runtime.json', max_bytes=16 * 1024**2)
    capabilities = read_object(report_path.parent / 'fingerprint-modes-capabilities.json')
    native_counts = validate_mode_native(native, payload)
    runtime_checks = validate_mode_runtime(runtime, payload)
    packaged_capabilities = bundle / 'fingerprint-modes-capabilities.json'
    if (type(capabilities.get('schema_version')) is not int or capabilities['schema_version'] != 1
            or capabilities.get('build_variant') != FINGERPRINT_VARIANT
            or capabilities.get('chromium_version') != VERSION
            or packaged_capabilities.is_symlink() or not packaged_capabilities.is_file()
            or sha256(packaged_capabilities) != files['fingerprint-modes-capabilities.json']
            or runtime.get('capabilities_sha256') != files['fingerprint-modes-capabilities.json']):
        raise ValueError('Fingerprint capabilities identity differs from the packaged contract')
    if (report.get('required_runtime_checks') != runtime_checks
            or report.get('not_tested') != runtime['not_tested']
            or report.get('coverage_limits') != runtime['coverage_limits']
            or report.get('positive_acceptance') != runtime['positive_acceptance']
            or report.get('full_strategy_acceptance') is not runtime['full_strategy_acceptance']
            or report.get('full_strategy_acceptance_scope') != runtime['full_strategy_acceptance_scope']
            or report.get('full_original_cloak_acceptance') is not False
            or report.get('original_runtime_acceptance') != runtime['original_runtime_acceptance']
            or report.get('partial') is not runtime['partial']):
        raise ValueError('Fingerprint summary does not preserve actual runtime coverage')
    return {'status': 'passed', 'report_sha256': sha256(report_path), 'files': files,
            'native_executed_counts': native_counts, 'runtime_checks': runtime_checks,
            'not_tested': runtime['not_tested'],
            'positive_acceptance': runtime['positive_acceptance'],
            'full_strategy_acceptance': runtime['full_strategy_acceptance'], 'partial': runtime['partial'],
            'full_strategy_acceptance_scope': runtime['full_strategy_acceptance_scope'],
            'full_original_cloak_acceptance': False,
            'original_runtime_acceptance': runtime['original_runtime_acceptance'],
            'execution_scope': 'Exact-browser-byte receipts for native helper/configuration tests and compatible-v1-covered-groups browser scenarios; no original-mode Web API runtime acceptance',
            'additional_gpu_hardware_acceptance': 'not_tested_by_receipt_verification'}


NATIVE_PASSKEY_TESTS = {
    'WinAuthenticatorTest.GetCredentialInformationForRequest_' + suffix
    for suffix in ('NativeProvidersEmptyCache', 'NativeProvidersFilteredCache',
                   'NativeProvidersPreservesMatch', 'NativeProvidersRespectsTransports',
                   'NativeProvidersUnavailableBeforeV9')
}
NATIVE_PASSKEY_TESTS.update({
    'AuthenticatorRequestDialogControllerTest.WinSingleCachedCredentialPreservesOriginalAllowList',
    'AuthenticatorRequestDialogControllerTest.WinCredMatchEmptyAllowList',
    'AuthenticatorRequestDialogControllerTest.ConditionalUIWindowsCancel',
})


def validate_native_passkey_tests(report):
    iterations = report.get('per_iteration_data', [])
    if not isinstance(iterations, list) or not iterations:
        raise ValueError('Native passkey regression results are missing')
    executed = set()
    for iteration in iterations:
        if not isinstance(iteration, dict):
            raise ValueError('Invalid native passkey regression results')
        for name, attempts in iteration.items():
            if not name.startswith(('WinAuthenticatorTest.', 'AuthenticatorRequestDialogControllerTest.')):
                continue
            if (not isinstance(attempts, list) or not attempts
                    or any(not isinstance(attempt, dict) or attempt.get('status') != 'SUCCESS'
                           for attempt in attempts)):
                raise ValueError('Native passkey regression failed: ' + name)
            executed.add(name)
    if not NATIVE_PASSKEY_TESTS.issubset(executed):
        raise ValueError('Native passkey regression coverage is incomplete')
    return sorted(executed)


def inventory(bundle):
    return {path.relative_to(bundle).as_posix(): sha256(path)
            for path in sorted(bundle.rglob('*')) if path.is_file()}


def verify_candidate(artifact_dir, source, report_dir, record, *, build_variant=GOOGLE_VARIANT):
    validate_source_run(source, build_variant=build_variant)
    native_tests = artifact_dir / 'native-passkey-unittests.json'
    record['native_passkey_regression_tests'] = validate_native_passkey_tests(read_object(native_tests))
    record['native_passkey_regression_sha256'] = sha256(native_tests)
    archive = artifact_dir / 'chromix-win-x64.zip'
    manifest = artifact_dir / 'SHA256SUMS'
    bundle_report = extract_archive(archive, manifest, report_dir.parent / 'verified-browser', 'x64')
    bundle = Path(bundle_report['bundle_dir'])
    inner = read_object(bundle / 'google-services-source.json')
    validate_source_recipe(inner, read_object(artifact_dir / 'google-services-source.json'), build_variant=build_variant)
    record.update(source=source, archive_sha256=bundle_report['archive']['sha256'],
                  compiler_invoked=False, source_checkpoint_restored=False,
                  built_source_commit=source['head_sha'],
                  verification_commit=os.environ.get('GITHUB_SHA'),
                  qualification='Tests of the original source-run artifact; not a rebuild of the verification commit')
    before = inventory(bundle)
    if build_variant == FINGERPRINT_VARIANT:
        build = read_object(bundle / 'google-services-build.json')
        if (build.get('build_variant') != FINGERPRINT_VARIANT
                or build.get('source_commit') != source['head_sha']
                or build.get('run_id') != source['run_id']
                or build.get('repository') != source['repository']
                or build.get('patch_sha256') != inner['patch_sha256']
                or build.get('preparation_sha256') != inner.get('preparation_sha256')):
            raise ValueError('Fingerprint build receipt does not match its source run and recipe')
        expected_files = {name: digest for name, digest in before.items() if name != 'google-services-build.json'}
        if build.get('files') != expected_files:
            raise ValueError('Fingerprint candidate inventory differs from the pretest build receipt')
        record['build_variant'] = build_variant
        record['fingerprint_modes'] = validate_fingerprint_tests(
            artifact_dir / 'fingerprint-modes-validation.json', bundle, before)
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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--artifact-dir', type=Path)
    mode.add_argument('--native-tests-json', type=Path)
    mode.add_argument('--fingerprint-tests-json', type=Path)
    parser.add_argument('--source-run-json', type=Path)
    parser.add_argument('--report-dir', type=Path)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--build-variant', choices=ARTIFACT_NAMES, default=GOOGLE_VARIANT)
    args = parser.parse_args()
    if args.native_tests_json:
        executed = validate_native_passkey_tests(read_object(args.native_tests_json))
        print(json.dumps({'native_passkey_tests': 'passed', 'executed': len(executed)}))
        return 0
    if args.fingerprint_tests_json:
        if not args.bundle:
            parser.error('Fingerprint evidence verification requires --bundle')
        print(json.dumps(validate_fingerprint_tests(args.fingerprint_tests_json, args.bundle)))
        return 0
    if not args.source_run_json or not args.report_dir:
        parser.error('Artifact verification requires --source-run-json and --report-dir')
    args.report_dir.mkdir(parents=True, exist_ok=False)
    record = {'schema_version': 1, 'status': 'failed', 'compiler_invoked': False,
              'source_checkpoint_restored': False}
    try:
        verify_candidate(args.artifact_dir, read_object(args.source_run_json), args.report_dir, record,
                         build_variant=args.build_variant)
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
