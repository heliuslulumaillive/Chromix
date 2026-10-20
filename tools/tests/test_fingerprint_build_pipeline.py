"""Isolated variant identity, checkpoint recovery and delivered-evidence contracts."""
import copy
import hashlib
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import yaml

from tools import google_services_checkpoint as checkpoint
from tools import google_services_cache_audit as audit

ROOT = Path(__file__).resolve().parents[2]
with mock.patch.object(sys, 'path', [str(ROOT / 'tools'), *sys.path]):
    from tools import verify_google_services_candidate as candidate

FP = checkpoint.FINGERPRINT_VARIANT


def artifact_list(prefix='fingerprint-modes-win155', stage=1):
    return [
        {'name': f'{prefix}-checkpoint-s{stage}', 'id': 10, 'expired': False},
        {'name': f'{prefix}-tree-s{stage}-part1', 'id': 11, 'expired': False},
    ]


def fp_manifest():
    return {'format_version': 2, 'build_variant': FP, 'arch': 'x64',
            'chromium_version': checkpoint.VERSION, 'run_id': '50', 'stage': 1,
            'commit': 'a' * 40, 'parts': [{'name': 'tree.7z.001', 'bytes': 2}],
            **{name: 'b' * 64 for name in (*checkpoint.IDENTITY_HASHES, 'source_receipt_sha256')}}


class VariantCheckpointTest(unittest.TestCase):
    def test_bootstrap_is_an_explicit_single_stage_exact_donor(self):
        donor = checkpoint.BOOTSTRAP_DONOR
        self.assertEqual(checkpoint.checkpoint_variant(FP, donor['run_id'], 1, True, donor['repository']),
                         checkpoint.GOOGLE_VARIANT)
        for stage, allowed, repository in ((1, False, donor['repository']), (2, True, donor['repository']),
                                           (1, True, 'other/repository')):
            with self.subTest(stage=stage, allowed=allowed), self.assertRaises(ValueError):
                checkpoint.checkpoint_variant(FP, donor['run_id'], stage, allowed, repository)
        manifest = {key: value for key, value in donor.items() if key != 'repository'}
        manifest.update(format_version=1, chromium_version=checkpoint.VERSION,
                        parts=[{'name': 'tree.7z.001', 'bytes': 2}])
        source = {'id': int(donor['run_id']), 'head_sha': donor['commit']}
        checkpoint.validate_manifest(manifest, source, artifact_list('google-win155'), 1,
                                     build_variant=FP, bootstrap=True)
        for field in ('commit', *checkpoint.IDENTITY_HASHES):
            changed = {**manifest, field: '0' * len(manifest[field])}
            with self.subTest(field=field), self.assertRaises(ValueError):
                checkpoint.validate_manifest(changed, source, artifact_list('google-win155'), 1,
                                             build_variant=FP, bootstrap=True)

    def test_fingerprint_selection_cannot_pick_old_or_future_namespaces(self):
        mixed = artifact_list() + artifact_list('google-win155', stage=4)
        self.assertEqual(checkpoint.select_manifest(mixed, '50', '51', 1, build_variant=FP)[0], 1)
        with self.assertRaises(ValueError):
            checkpoint.select_manifest(artifact_list('google-win155'), '50', '51', 1, build_variant=FP)
        with self.assertRaises(ValueError):
            checkpoint.select_manifest(mixed, '50', '50', 1, build_variant=FP)

    def test_new_manifest_requires_variant_arch_and_all_receipt_hashes(self):
        source = {'id': 50, 'head_sha': 'a' * 40}
        parts, hashes = checkpoint.validate_manifest(fp_manifest(), source, artifact_list(), 1, build_variant=FP)
        self.assertEqual(parts[0]['artifact_name'], 'fingerprint-modes-win155-tree-s1-part1')
        self.assertEqual(len(hashes), 4)
        for field, value in (('format_version', 1), ('build_variant', 'google-services'), ('arch', 'arm64'),
                             ('source_receipt_sha256', 'not a hash')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                checkpoint.validate_manifest({**fp_manifest(), field: value}, source, artifact_list(), 1, build_variant=FP)
        for field in (*checkpoint.IDENTITY_HASHES, 'source_receipt_sha256'):
            changed = fp_manifest()
            del changed[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                checkpoint.validate_manifest(changed, source, artifact_list(), 1, build_variant=FP)

    def test_bootstrap_never_loops_back_to_the_old_donor_without_committing_new_state(self):
        donor = checkpoint.BOOTSTRAP_DONOR['run_id']
        no_progress = checkpoint.continuation_plan('success', False, 'skipped', False, 'skipped',
                                                  donor, '99', build_variant=FP)
        self.assertFalse(no_progress['resume_needed'])
        self.assertEqual(no_progress['recovery_source'], donor)
        saved = checkpoint.continuation_plan('success', False, 'skipped', True, 'success',
                                            donor, '99', build_variant=FP)
        self.assertEqual(saved['resume_source'], '99')
        self.assertTrue(saved['resume_needed'])
        delivered = checkpoint.continuation_plan('success', True, 'success', True, 'skipped',
                                                donor, '99', build_variant=FP)
        self.assertFalse(delivered['resume_needed'])


class MetadataRefreshTest(unittest.TestCase):
    def exercise(self, refresh_text, plan_text):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.ninja_log').write_text('# ninja log v6\n0\t1\t2\tobj/a.obj\tabc\n')
            report = root / 'audit.json'
            command = ['audit', '--ninja', 'ninja.exe', '--out', str(root), '--report', str(report),
                       '--ensure-manifest', '--target', 'chrome', '--target', 'uxr_surface_modes_tests']
            outputs = [SimpleNamespace(returncode=0, stdout=text, stderr='') for text in (refresh_text, plan_text)]
            with mock.patch.object(sys, 'argv', command), redirect_stdout(io.StringIO()), \
                    mock.patch.object(audit.subprocess, 'run', side_effect=outputs) as run:
                code = audit.main()
            calls = [item.args[0] for item in run.call_args_list]
            self.assertEqual(calls[0][-1], 'build.ninja')
            self.assertNotIn('-n', calls[0])
            self.assertEqual(calls[1][-2:], ['chrome', 'uxr_surface_modes_tests'])
            self.assertIn('-n', calls[1])
            return code, json.loads(report.read_text())

    def test_unchanged_gn_metadata_does_not_force_generation_or_compilation(self):
        code, report = self.exercise('ninja: no work to do.\n', 'ninja: no work to do.\n')
        self.assertEqual(code, 0)
        self.assertFalse(report['manifest_refreshed'])
        self.assertFalse(report['compiler_invoked'])
        self.assertEqual(report['pending_actions'], 0)

    def test_changed_gn_inputs_are_refreshed_once_before_new_target_dry_run(self):
        code, report = self.exercise('[0/1] Regenerating ninja files\n', '[1/2] CXX obj/new.obj\n[2/2] LINK modes.exe\n')
        self.assertEqual(code, 0)
        self.assertTrue(report['manifest_refreshed'])
        self.assertEqual(report['pending_actions'], 2)
        self.assertEqual(report['pending_compile_actions'], 1)
        code, report = self.exercise('[0/1] Regenerating ninja files\n', '[0/1] Regenerating ninja files\n')
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'failed')


def write_fingerprint_evidence(root, bundle):
    # Small, schema-faithful versions of the actual PS/native/CDP receipts.
    # These are explicit test fixtures, not claims that a browser was executed.
    payload = {'browser_sha256': candidate.sha256(bundle / 'chrome.exe'),
               'chrome_dll_sha256': candidate.sha256(bundle / 'chrome.dll')}
    groups = {
        'legacy': [{'status': 'passed', 'configuration_checks': 6, 'failures': 0}],
        'chromix': [{'status': 'passed', 'configuration_checks': 4, 'failures': 0}],
        'production': [
            {'graphics_cases': 2, 'failures': 0, 'groups': {'fixture_pixels': 2}},
            {'checks': 3, 'failures': 0}, {'gpu_checks': 4, 'failures': 0},
            {'hardware_checks': 5, 'failures': 0},
            {'font_allowlist_checks': 38, 'failures': 0}, {'audio_default_checks': 28, 'failures': 0},
            {'status': 'passed', 'configuration_checks': 6, 'failures': 0},
        ],
    }
    processes = []
    for label, group, arguments in (
            ('config-legacy', 'legacy', ['--config-legacy']),
            ('config-chromix', 'chromix', ['--config-chromix']),
            ('production-helpers-and-config', 'production', ['D:/a/Chromix/Chromix/tools/tests/fixtures'])):
        stdout = '\n'.join(json.dumps(value) for value in groups[group]) + '\n'
        processes.append({'label': label, 'executable_sha256': 'a' * 64, 'arguments': arguments,
                          'exit_code': 0, 'timed_out': False, 'stdout': stdout,
                          'stdout_sha256': hashlib.sha256(stdout.encode()).hexdigest(),
                          'stdout_file': 'C:/evidence/' + label + '.stdout.log'})
    native = {'schema_version': 1, 'status': 'passed', **payload, 'processes': processes, 'groups': groups}
    cases = ['native_off_control', 'all_native', 'legacy_seeded', 'canvas_shardx',
             'webgl_cloak', 'shard_surfaces', 'memory_custom', 'hardware_cloak', 'gpu_custom',
             'webgl_shardx', 'fonts_shard42', 'fonts_shard43', 'fonts_cloak10026', 'fonts_allowlists', 'fonts_empty',
             'cloak_voices', 'cloak_voices_no_local', 'cloak_offset', 'cloak_text_positive', 'cloak_audio_positive',
             'audio_defaults_44100', 'audio_defaults_32000', 'audio_default_mono', 'audio_default_stereo',
             'audio_default_32', 'gpu_masked_custom']
    common = ['probe_completed', 'normal_owned_browser_shutdown', 'secure_loopback', 'no_internal_probe_errors',
              'window_worker_cpu_memory', 'probe_scope']
    full = ['audio_alias_copy_preserves_unwritten_tail', 'analyser_preserves_unwritten_tails', 'synthetic_media_tracks']
    checks = [{'scenario': case, 'name': name, 'passed': True} for case in cases
              for name in common + ([] if case in candidate.FINGERPRINT_BASIC_SCENARIOS else full)]
    specific = {
        'all_native': ['native_cpu_matches_off_control', 'native_memory_matches_off_control',
                       'native_ua_matches_off_control', 'native_screen_matches_off_control'],
        'legacy_seeded': ['selected_canvas_noise_reaches_runtime'],
        'canvas_shardx': ['selected_canvas_noise_reaches_runtime'],
        'shard_surfaces': ['audio_changed', 'rect_mode_effect_and_bound', 'text_mode_effect_and_bound',
                           'media_filter_observable_on_fake_devices'],
        'memory_custom': ['expected_cpu_and_memory', 'device-memory_matches_js', 'sec-ch-device-memory_matches_js'],
        'hardware_cloak': ['expected_cpu_and_memory', 'device-memory_matches_js', 'sec-ch-device-memory_matches_js'],
        'cloak_offset': ['offset_120_native_control', 'offset_120_captured_oracle',
                         'offset_50_native_control', 'offset_50_captured_oracle'],
    }
    checks.extend({'scenario': case, 'name': name, 'passed': True} for case, names in specific.items() for name in names)
    for scenario, scopes in candidate.FINGERPRINT_EXTRA_SCOPES.items():
        checks.extend({'scenario': scenario, 'name': scope + '_acceptance_probe_present', 'passed': True} for scope in scopes)
    positive_checks = {
        'legacy_canvas': ('legacy_seeded', 'selected_canvas_noise_reaches_runtime'),
        'shard_canvas': ('canvas_shardx', 'selected_canvas_noise_reaches_runtime'),
        'cloak_canvas': ('hardware_cloak', 'selected_canvas_noise_reaches_runtime'),
        'shard_webgl': ('webgl_shardx', 'webgl_captured_xor_oracle'),
        'cloak_webgl': ('webgl_cloak', 'webgl_noise_reaches_runtime'),
        'shard_audio': ('shard_surfaces', 'audio_changed'),
        'shard_rects': ('shard_surfaces', 'rect_mode_effect_and_bound'),
        'shard_text': ('shard_surfaces', 'text_mode_effect_and_bound'),
        'shard_sensors': ('shard_surfaces', 'sensor_mode_effect_and_bound'),
        'shard_media': ('shard_surfaces', 'media_filter_observable_on_fake_devices'),
        'memory_custom': ('memory_custom', 'expected_cpu_and_memory'),
        'hardware_profile': ('hardware_cloak', 'expected_cpu_and_memory'),
        'webgl_resource_limits': ('gpu_custom', 'webgl_texture_context_limit'),
        'webgpu_capabilities': ('gpu_custom', 'webgpu_compute_readback'),
        'hardware_gpu_identity': ('hardware_cloak', 'webgl_hardware_identity_positive'),
        'shard_fonts': ('fonts_shard42', 'windowLocal:Comic Sans MS:blocked'),
        'cloak_fonts': ('fonts_cloak10026', 'windowLocal:Gabriola:blocked'),
        'font_allowlists': ('fonts_allowlists', 'windowLocal:Consolas:blocked'),
        'cloak_voices': ('cloak_voices', 'native_voice_intersection_and_metadata'),
        'cloak_offset': ('cloak_offset', 'offset_120_captured_oracle'),
        'cloak_text': ('cloak_text_positive', 'text_reference_100_eligible_replacement'),
        'cloak_audio': ('cloak_audio_positive', 'reference_5000_full_pcm'),
        'audio_defaults': ('audio_defaults_44100', 'default_sample_rate'),
        'audio_channels': ('audio_defaults_44100', 'backend_bounded_capacity_and_initial_count'),
    }
    for group, (scenario, name) in positive_checks.items():
        existing = next((value for value in checks if value['scenario'] == scenario and value['name'] == name), None)
        if existing is None:
            existing = {'scenario': scenario, 'name': name, 'passed': True}
            checks.append(existing)
        existing['acceptance_group'] = group
    missing = [{'scenario': 'gpu_masked_custom', 'name': 'webgl_hardware_masked_identity',
                'acceptance_group': 'masked_webgl', 'reason': 'Synthetic schema fixture: actual hardware path unavailable'}]
    coverage = {}
    for group in candidate.FINGERPRINT_POSITIVE_SCENARIOS:
        actual = [c for c in checks if c.get('acceptance_group') == group]
        skipped = [c for c in missing if c.get('acceptance_group') == group]
        coverage[group] = {'status': 'not_tested' if skipped else 'observed_pass',
                           'eligible_count': len(actual), 'passed_count': len(actual),
                           'reason': 'Synthetic receipt structure; no browser execution claimed',
                           'evidence': [{'scenario': c['scenario'], 'name': c['name']} for c in actual],
                           'missing': [{'scenario': c['scenario'], 'name': c['name'], 'reason': c['reason']} for c in skipped]}
    scenarios = [{'id': case, 'probe_level': 'basic' if case in candidate.FINGERPRINT_BASIC_SCENARIOS else 'full',
                  'probe': {'errors': [], 'secureContext': True,
                            'level': 'basic' if case in candidate.FINGERPRINT_BASIC_SCENARIOS else 'full'},
                  'extra': {scope: {'version': 1} for scope in candidate.FINGERPRINT_EXTRA_SCOPES.get(case, ())},
                  'cleanup': {'normalExit': True, 'closeSent': True, 'forced': False, 'exitCode': 0,
                              'rootPID': 100 + index, 'owned_tree_before_close': [
                                  {'pid': 100 + index, 'parent': 1, 'created': '2026-10-20T00:00:00Z'}],
                              'known_owned_processes_remaining': [], 'process_tree_error': None}}
                 for index, case in enumerate(cases)]
    cap = (ROOT / 'build/fingerprint-modes-capabilities.json').read_bytes()
    (root / 'fingerprint-modes-capabilities.json').write_bytes(cap)
    (bundle / 'fingerprint-modes-capabilities.json').write_bytes(cap)
    runtime = {'schema_version': 1, 'status': 'passed', **payload, 'baseline_only': False,
               'capabilities_sha256': hashlib.sha256(cap).hexdigest(),
               'expected_scenarios': len(cases), 'scenarios': scenarios, 'checks': checks, 'failed': [],
               'not_tested': missing, 'positive_acceptance': coverage,
               'full_strategy_acceptance': False, 'partial': True,
               'coverage_limits': ['Synthetic fixture of the receipt schema; no test execution claimed']}
    for name, value in [('modes-native.json', native), ('modes-runtime.json', runtime)]:
        (root / name).write_text(json.dumps(value))
    files = {name: candidate.sha256(root / name) for name in candidate.FINGERPRINT_REPORTS}
    report = root / 'fingerprint-modes-validation.json'
    report.write_text(json.dumps({'status': 'passed', 'files': files, **payload,
                                 'required_runtime_checks': len(checks), 'not_tested': runtime['not_tested'],
                                 'coverage_limits': runtime['coverage_limits'], 'positive_acceptance': coverage,
                                 'full_strategy_acceptance': False, 'partial': True}))
    return report


def rebind_fingerprint_fixture(root):
    """Maintain valid outer hashes so negative tests exercise the inner checks."""
    runtime = json.loads((root / 'modes-runtime.json').read_text())
    runtime['capabilities_sha256'] = candidate.sha256(root / 'fingerprint-modes-capabilities.json')
    (root / 'modes-runtime.json').write_text(json.dumps(runtime))
    path = root / 'fingerprint-modes-validation.json'
    value = json.loads(path.read_text())
    value.update(files={name: candidate.sha256(root / name) for name in candidate.FINGERPRINT_REPORTS},
                 required_runtime_checks=len(runtime.get('checks', [])),
                 not_tested=runtime.get('not_tested'), coverage_limits=runtime.get('coverage_limits'),
                 positive_acceptance=runtime.get('positive_acceptance'),
                 full_strategy_acceptance=runtime.get('full_strategy_acceptance'), partial=runtime.get('partial'))
    path.write_text(json.dumps(value))


class FingerprintEvidenceTest(unittest.TestCase):
    def test_runtime_acceptance_modules_and_matrix_contract(self):
        node = os.environ.get('FINGERPRINT_TEST_NODE') or shutil.which('node')
        if not node:
            self.skipTest('Node is not installed on this source-only test host')
        result = subprocess.run([node, '--test', str(ROOT / 'tools/tests/test_fingerprint_runtime_acceptance.mjs')],
                                cwd=ROOT, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        contract = subprocess.run([node, '--input-type=module', '-e',
                                  "import {scenarios,BASIC_SCENARIOS,POSITIVE_GROUPS} from './tools/verify_fingerprint_modes_runtime.mjs';"
                                  "console.log(JSON.stringify({scenarios:scenarios().map(s=>s.id),basic:[...BASIC_SCENARIOS],groups:POSITIVE_GROUPS}));"],
                                 cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(contract.returncode, 0, contract.stderr)
        value = json.loads(contract.stdout)
        self.assertEqual(set(value['scenarios']), candidate.FINGERPRINT_RUNTIME_SCENARIOS)
        self.assertEqual(set(value['basic']), candidate.FINGERPRINT_BASIC_SCENARIOS)
        self.assertEqual(set(value['groups']), set(candidate.FINGERPRINT_POSITIVE_SCENARIOS))

    def test_self_consistent_hashes_do_not_hide_missing_or_failed_inner_evidence(self):
        mutations = [
            ('native_status', lambda n, r, c: n.update(status='failed')),
            ('native_payload', lambda n, r, c: n.update(browser_sha256='0' * 64)),
            ('native_process_missing', lambda n, r, c: n['processes'].pop(1)),
            ('native_process_failed', lambda n, r, c: n['processes'][0].update(exit_code=1)),
            ('native_process_timeout', lambda n, r, c: n['processes'][0].update(timed_out=True)),
            ('native_wrong_binary', lambda n, r, c: n['processes'][0].update(executable_sha256='b' * 64)),
            ('native_wrong_arguments', lambda n, r, c: n['processes'][1].update(arguments=['--config-legacy'])),
            ('native_empty_group', lambda n, r, c: n['groups'].update(chromix=[])),
            ('native_zero_count', lambda n, r, c: n['groups']['production'][0].update(graphics_cases=0)),
            ('native_boolean_count', lambda n, r, c: n['groups']['production'][2].update(gpu_checks=True)),
            ('native_string_count', lambda n, r, c: n['groups']['production'][3].update(hardware_checks='99')),
            ('native_inner_failure', lambda n, r, c: n['groups']['production'][1].update(failures=1)),
            ('native_inner_status', lambda n, r, c: n['groups']['legacy'][0].update(status='failed')),
            ('native_missing_font_group', lambda n, r, c: n['groups']['production'].pop(4)),
            ('native_missing_audio_group', lambda n, r, c: n['groups']['production'].pop(5)),
            ('runtime_status', lambda n, r, c: r.update(status='failed')),
            ('runtime_baseline', lambda n, r, c: r.update(baseline_only=True)),
            ('runtime_payload', lambda n, r, c: r.update(chrome_dll_sha256='0' * 64)),
            ('runtime_no_checks', lambda n, r, c: r.update(checks=[])),
            ('runtime_failed_check', lambda n, r, c: r['checks'][0].update(passed=False)),
            ('runtime_missing_routing_check', lambda n, r, c: r['checks'].pop()),
            ('runtime_missing_scenario', lambda n, r, c: r['scenarios'].pop()),
            ('runtime_duplicated_scenario', lambda n, r, c: r['scenarios'].__setitem__(1, copy.deepcopy(r['scenarios'][0]))),
            ('runtime_probe_failure', lambda n, r, c: r['scenarios'][0]['probe']['errors'].append({'error': 'fixture'})),
            ('runtime_false_full_probe', lambda n, r, c: r['scenarios'][10]['probe'].update(level='full')),
            ('runtime_forced_close', lambda n, r, c: r['scenarios'][0]['cleanup'].update(forced=True)),
            ('runtime_remaining_process', lambda n, r, c: r['scenarios'][0]['cleanup'].update(known_owned_processes_remaining=[{'pid': 100}])),
            ('runtime_extra_missing', lambda n, r, c: r['scenarios'][1]['extra'].pop('audio')),
            ('positive_missing', lambda n, r, c: r.pop('positive_acceptance')),
            ('positive_strategy_missing', lambda n, r, c: r['positive_acceptance'].pop('cloak_audio')),
            ('positive_count_zero', lambda n, r, c: r['positive_acceptance']['cloak_audio'].update(eligible_count=0)),
            ('positive_count_boolean', lambda n, r, c: r['positive_acceptance']['cloak_audio'].update(passed_count=True)),
            ('positive_orphan_evidence', lambda n, r, c: r['positive_acceptance']['cloak_audio']['evidence'].clear()),
            ('positive_unsupported_as_pass', lambda n, r, c: r['positive_acceptance']['masked_webgl'].update(status='observed_pass')),
            ('positive_claim_full', lambda n, r, c: r.update(full_strategy_acceptance=True, partial=False)),
            ('positive_partial_string', lambda n, r, c: r.update(partial='true')),
            ('positive_skip_without_reason', lambda n, r, c: r['not_tested'][0].update(reason='')),
            ('positive_skip_wrong_strategy', lambda n, r, c: r['not_tested'][0].update(acceptance_group='cloak_audio')),
            ('positive_skip_hidden_optional', lambda n, r, c: r['not_tested'][0].update(optional_candidate=True)),
            ('cap_schema', lambda n, r, c: c.update(schema_version=2)),
            ('cap_boolean_schema', lambda n, r, c: c.update(schema_version=True)),
            ('cap_variant', lambda n, r, c: c.update(build_variant='other')),
            ('cap_version', lambda n, r, c: c.update(chromium_version='154.0.0.0')),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundle = root / 'bundle'
                bundle.mkdir()
                (bundle / 'chrome.exe').write_bytes(b'exe')
                (bundle / 'chrome.dll').write_bytes(b'dll')
                report = write_fingerprint_evidence(root, bundle)
                native = json.loads((root / 'modes-native.json').read_text())
                runtime = json.loads((root / 'modes-runtime.json').read_text())
                capabilities = json.loads((root / 'fingerprint-modes-capabilities.json').read_text())
                mutate(native, runtime, capabilities)
                for process in native['processes']:
                    group = {'config-legacy': 'legacy', 'config-chromix': 'chromix',
                             'production-helpers-and-config': 'production'}[process['label']]
                    output = '\n'.join(json.dumps(value) for value in native['groups'][group]) + '\n'
                    process.update(stdout=output, stdout_sha256=hashlib.sha256(output.encode()).hexdigest())
                for filename, value in [('modes-native.json', native), ('modes-runtime.json', runtime),
                                        ('fingerprint-modes-capabilities.json', capabilities)]:
                    (root / filename).write_text(json.dumps(value))
                # Cap mutations stay byte-consistent with the bundle; the
                # schema/variant checks must reject them even with valid hashes.
                (bundle / 'fingerprint-modes-capabilities.json').write_bytes(
                    (root / 'fingerprint-modes-capabilities.json').read_bytes())
                rebind_fingerprint_fixture(root)
                with self.assertRaises(ValueError):
                    candidate.validate_fingerprint_tests(report, bundle)

    def test_partial_positive_coverage_is_preserved_without_claiming_full_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / 'bundle'
            bundle.mkdir()
            (bundle / 'chrome.exe').write_bytes(b'exe')
            (bundle / 'chrome.dll').write_bytes(b'dll')
            report = write_fingerprint_evidence(root, bundle)
            result = candidate.validate_fingerprint_tests(report, bundle)
            self.assertEqual(result['status'], 'passed')
            self.assertTrue(result['partial'])
            self.assertFalse(result['full_strategy_acceptance'])
            self.assertEqual(result['positive_acceptance']['masked_webgl']['status'], 'not_tested')
            self.assertEqual(result['positive_acceptance']['cloak_audio']['status'], 'observed_pass')
            self.assertGreater(result['native_executed_counts']['production.font_allowlist_checks'], 0)
            self.assertGreater(result['native_executed_counts']['production.audio_default_checks'], 0)

    def test_caps_bundle_and_source_receipt_are_independently_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / 'bundle'
            bundle.mkdir()
            (bundle / 'chrome.exe').write_bytes(b'exe')
            (bundle / 'chrome.dll').write_bytes(b'dll')
            report = write_fingerprint_evidence(root, bundle)
            (bundle / 'fingerprint-modes-capabilities.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'capabilities'):
                candidate.validate_fingerprint_tests(report, bundle)
        names = ['build/google-services-win155.json', 'build/fingerprint-modes-capabilities.json',
                 'patches/google-services/0001-restore-google-services.json',
                 'patches/google-services/0002-windows-native-passkey-discovery.patch',
                 'patches/chromium155-windows/0233-renderer-timezone-webstring-api.patch']
        names.extend(json.loads((ROOT / names[0]).read_text()).get('restoration_overlays', []))
        receipt = {'variant': 'google-services-self-use', 'build_variant': FP,
                   'chromium_version': checkpoint.VERSION,
                   'patch_sha256': candidate.sha256(ROOT / 'patches/google-services/0001-restore-google-services.patch'),
                   'preparation_inputs': {name: candidate.sha256(ROOT / name) for name in names}}
        candidate.validate_source_recipe(receipt, copy.deepcopy(receipt), build_variant=FP)
        for value in (None, '0' * 64):
            changed = copy.deepcopy(receipt)
            if value is None:
                del changed['preparation_inputs']['build/fingerprint-modes-capabilities.json']
            else:
                changed['preparation_inputs']['build/fingerprint-modes-capabilities.json'] = value
            with self.assertRaisesRegex(ValueError, 'fingerprint-modes-capabilities'):
                candidate.validate_source_recipe(changed, copy.deepcopy(changed), build_variant=FP)

    def test_full_candidate_keeps_google_checks_and_rejects_mixed_build_receipts(self):
        for mutation in (None, 'commit', 'inventory', 'mode-report'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                artifact, reports, bundle = root / 'artifact', root / 'reports', root / 'bundle'
                for path in (artifact, reports, bundle):
                    path.mkdir()
                source = {'repository': 'fixture/chromix', 'run_id': 50, 'artifact_id': 12,
                          'head_sha': 'a' * 40, 'artifact_name': 'fingerprint-modes-win155-x64'}
                names = ['build/google-services-win155.json',
                         'build/fingerprint-modes-capabilities.json',
                         'patches/google-services/0001-restore-google-services.json',
                         'patches/google-services/0002-windows-native-passkey-discovery.patch',
                         'patches/chromium155-windows/0233-renderer-timezone-webstring-api.patch']
                names.extend(json.loads((ROOT / names[0]).read_text()).get('restoration_overlays', []))
                receipt = {'variant': 'google-services-self-use', 'build_variant': FP,
                           'chromium_version': checkpoint.VERSION,
                           'patch_sha256': candidate.sha256(ROOT / 'patches/google-services/0001-restore-google-services.patch'),
                           'preparation_sha256': 'b' * 64,
                           'preparation_inputs': {name: candidate.sha256(ROOT / name) for name in names}}
                for path in (bundle, artifact):
                    (path / 'google-services-source.json').write_text(json.dumps(receipt))
                (bundle / 'chrome.exe').write_bytes(b'exe')
                (bundle / 'chrome.dll').write_bytes(b'dll')
                (artifact / 'chromix-win-x64.zip').write_bytes(b'original archive')
                (artifact / 'native-passkey-unittests.json').write_text(json.dumps({
                    'per_iteration_data': [{name: [{'status': 'SUCCESS'}] for name in candidate.NATIVE_PASSKEY_TESTS}]}))
                write_fingerprint_evidence(artifact, bundle)
                build = {'build_variant': FP, 'source_commit': source['head_sha'], 'run_id': 50,
                         'repository': source['repository'], 'patch_sha256': receipt['patch_sha256'],
                         'preparation_sha256': receipt['preparation_sha256'], 'files': candidate.inventory(bundle)}
                if mutation == 'commit':
                    build['source_commit'] = '0' * 40
                if mutation == 'inventory':
                    build['files']['chrome.dll'] = '0' * 64
                if mutation == 'mode-report':
                    (artifact / 'modes-runtime.json').write_text('changed')
                (bundle / 'google-services-build.json').write_text(json.dumps(build))
                static = {'bundle_dir': str(bundle), 'arch': 'x64',
                          'archive': {'sha256': candidate.sha256(artifact / 'chromix-win-x64.zip')}}
                def google_probe(command, **_kwargs):
                    self.assertIn('verify_google_services_runtime.py', str(command[4]))
                    (reports / 'google-webauthn.json').write_text(json.dumps({
                        'status': 'passed', 'browser_sha256': candidate.sha256(bundle / 'chrome.exe'),
                        'chrome_dll_sha256': candidate.sha256(bundle / 'chrome.dll'),
                        'checks': [{'name': name, 'passed': True} for name in candidate.REQUIRED_CHECKS]}))
                    return {'exit_code': 0, 'timed_out': False, 'cleanup_errors': []}
                with mock.patch.object(candidate, 'extract_archive', return_value=static), \
                        mock.patch.object(candidate, 'runtime_smoke', return_value={'runtime': {'status': 'passed'}}), \
                        mock.patch.object(candidate, 'run_command', side_effect=google_probe) as probe:
                    result = {'status': 'failed'}
                    if mutation:
                        with self.assertRaises(ValueError):
                            candidate.verify_candidate(artifact, source, reports, result, build_variant=FP)
                        self.assertNotEqual(result['status'], 'passed')
                    else:
                        candidate.verify_candidate(artifact, source, reports, result, build_variant=FP)
                        self.assertEqual(result['status'], 'passed')
                        self.assertEqual(probe.call_count, 1)
                        self.assertFalse(result['compiler_invoked'])
                        self.assertEqual(result['fingerprint_modes']['status'], 'passed')

    def test_success_requires_all_reports_and_the_exact_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / 'bundle'
            bundle.mkdir()
            (bundle / 'chrome.exe').write_bytes(b'exe')
            (bundle / 'chrome.dll').write_bytes(b'dll')
            report = write_fingerprint_evidence(root, bundle)
            self.assertEqual(candidate.validate_fingerprint_tests(report, bundle)['status'], 'passed')
            original = json.loads(report.read_text())
            for change in ({'status': 'failed'}, {'files': {}}, {'browser_sha256': '0' * 64},
                           {'chrome_dll_sha256': '0' * 64},
                           {'files': {**original['files'], '../escaped.json': 'b' * 64}}):
                report.write_text(json.dumps({**original, **change}))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    candidate.validate_fingerprint_tests(report, bundle)
            report.write_text(json.dumps(original))
            (root / 'modes-runtime.json').write_text('changed evidence')
            with self.assertRaises(ValueError):
                candidate.validate_fingerprint_tests(report, bundle)

    def test_wrong_artifact_name_cannot_be_relabelled_as_fingerprint_candidate(self):
        source = {'repository': 'fixture/chromix', 'run_id': 1, 'artifact_id': 2,
                  'head_sha': 'a' * 40, 'artifact_name': 'google-services-win155-x64'}
        candidate.validate_source_run(source)
        with self.assertRaises(ValueError):
            candidate.validate_source_run(source, build_variant=FP)
        candidate.validate_source_run({**source, 'artifact_name': 'fingerprint-modes-win155-x64'}, build_variant=FP)

    def test_build_record_adds_variant_without_replacing_google_source_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, work = root / 'bundle', root / 'work'
            bundle.mkdir()
            work.mkdir()
            (bundle / 'chrome.exe').write_bytes(b'exe')
            args = root / 'args.gn'
            args.write_text('is_debug = false\n')
            source = {'variant': 'google-services-self-use', 'build_variant': FP,
                      'chromium_version': checkpoint.VERSION, 'patch_sha256': '1' * 64,
                      'preparation_sha256': '2' * 64}
            (work / 'google-services-source.json').write_text(json.dumps(source))
            environment = {**os.environ, 'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '42',
                           'GITHUB_REPOSITORY': 'fixture/chromix', 'GITHUB_RUN_ATTEMPT': '1'}
            process = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/write_google_services_build_record.py'),
                                      '--bundle', str(bundle), '--work', str(work), '--gn-args', str(args)],
                                     env=environment, capture_output=True, text=True, timeout=20)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            record = json.loads((bundle / 'google-services-build.json').read_text())
            self.assertEqual(record['build_variant'], FP)
            self.assertEqual(record['patch_sha256'], source['patch_sha256'])
            self.assertEqual(record['files'], {'chrome.exe': candidate.sha256(bundle / 'chrome.exe')})


class WorkflowIsolationTest(unittest.TestCase):
    @staticmethod
    def workflow(name):
        return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)

    def test_manual_new_variant_never_cancels_or_changes_the_old_namespace(self):
        flow = self.workflow('build-fingerprint-modes-win155.yml')
        self.assertEqual(set(flow['on']), {'workflow_dispatch', 'workflow_call'})
        call = flow['on']['workflow_call']['inputs']
        self.assertEqual(set(call), {'operation', 'source_run', 'allow_bootstrap_donor', 'max_stages'})
        self.assertEqual(call['allow_bootstrap_donor']['default'], 'false')
        self.assertEqual(flow['concurrency'], {'group': 'fingerprint-modes-win155-${{ github.ref }}',
                                              'cancel-in-progress': 'false'})
        inputs = flow['on']['workflow_dispatch']['inputs']
        self.assertEqual(inputs['operation']['default'], 'verify')
        self.assertEqual(inputs['source_run']['default'], checkpoint.BOOTSTRAP_DONOR['run_id'])
        self.assertEqual(inputs['allow_bootstrap_donor']['default'], 'false')
        self.assertEqual(flow['jobs']['build-1']['with']['allow_bootstrap_donor'], '${{ inputs.allow_bootstrap_donor }}')
        for stage in range(2, 5):
            job = flow['jobs'][f'build-{stage}']
            self.assertNotIn('allow_bootstrap_donor', job['with'])
            self.assertEqual(job['with']['resume_run'], '${{ needs.build-' + str(stage - 1) + '.outputs.resume_source }}')
        verify = json.dumps(flow['jobs']['verify-candidate'])
        self.assertNotIn('build-google-services.ps1', verify)
        self.assertNotIn('ensure-windows-sdk', verify)
        self.assertIn('--build-variant fingerprint-modes-win155', verify)
        old = self.workflow('build-google-services-win155.yml')
        self.assertEqual(old['concurrency']['group'], 'google-services-win155-${{ github.ref }}')
        self.assertEqual(old['on']['workflow_dispatch']['inputs']['build_variant']['default'],
                         'google-services-self-use')
        route = old['jobs']['fingerprint-modes']
        self.assertEqual(route['uses'], './.github/workflows/build-fingerprint-modes-win155.yml')
        self.assertIn("inputs.build_variant == 'fingerprint-modes-win155'", route['if'])
        self.assertEqual(route['with']['allow_bootstrap_donor'], '${{ inputs.allow_bootstrap_donor }}')
        for name, job in old['jobs'].items():
            if name != 'fingerprint-modes':
                self.assertIn("inputs.build_variant != 'fingerprint-modes-win155'", job['if'])

    def test_worker_retains_native_tests_and_delivery_guard_with_new_artifacts(self):
        worker = self.workflow('fingerprint-modes-win155-worker.yml')
        steps = worker['jobs']['build']['steps']
        by_id = {step['id']: step for step in steps if 'id' in step}
        self.assertIn('fingerprint-modes-build-session.json', steps[0]['run'])
        self.assertIn('-AdditionalNinjaTargets uxr_surface_modes_tests', by_id['build']['run'])
        self.assertIn('-FingerprintValidationScript build/windows/verify-fingerprint-modes.ps1', by_id['build']['run'])
        self.assertIn('-BuildVariant fingerprint-modes-win155', by_id['restore']['run'])
        self.assertTrue(any(step.get('uses') == 'actions/setup-node@v4' and step['with']['node-version'] == '24'
                            for step in steps))
        names = [step['with']['name'] for step in steps
                 if step.get('uses') == 'actions/upload-artifact@v4']
        self.assertTrue(all(name.startswith('fingerprint-modes-') for name in names))
        self.assertIn('native-passkey-unittests.json', by_id['candidate']['with']['path'])
        for name in (*candidate.FINGERPRINT_REPORTS, 'fingerprint-modes-validation.json'):
            self.assertIn(name, by_id['candidate']['with']['path'])
        self.assertIn("steps.candidate.outcome != 'success'", by_id['checkpoint']['if'])
        self.assertLess(steps.index(by_id['candidate']), steps.index(by_id['checkpoint']))
        for part in range(1, 5):
            self.assertIn(f"steps.part{part}.outcome == 'success'", by_id['manifest']['if'])
        self.assertIn('--build-variant fingerprint-modes-win155', by_id['continuation']['run'])
        script = (ROOT / 'build/windows/build-google-services.ps1').read_text()
        self.assertIn('run-native-passkey-tests.ps1', script)
        self.assertIn('--native-tests-json $nativeTests', script)
        self.assertIn('verify_google_services_runtime.py', script)
        self.assertIn('--fingerprint-tests-json $modesReport', script)
        self.assertIn('--ensure-manifest', script)
        self.assertEqual(script.count("tools\\prepare_google_services.py"), 1)
        self.assertEqual(script.count('$seven x $first'), 1)
        self.assertIn('$nextTelemetrySeconds += 60', script)
        self.assertIn("elseif (-not $isFingerprint", script)

    @unittest.skipUnless(shutil.which('pwsh') or shutil.which('powershell'), 'PowerShell parser')
    def test_shared_build_script_and_new_workflow_shell_parse_without_running_builds(self):
        worker = self.workflow('fingerprint-modes-win155-worker.yml')
        main = self.workflow('build-fingerprint-modes-win155.yml')
        snippets = [step['run'] for flow in (worker, main) for job in flow['jobs'].values()
                    for step in job.get('steps', []) if 'run' in step]
        # GitHub expands ${{ }} before PowerShell parses a step.
        import re
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, text in enumerate(snippets):
                # Quoted GitHub expressions may themselves contain quotes.
                text = re.sub(r'\$\{\{.*?\}\}', 'fixture', text)
                (root / f'step-{index}.ps1').write_text(text)
            command = r'''
$ErrorActionPreference = 'Stop'
$files = @((Join-Path $env:FP_REPO 'build/windows/build-google-services.ps1')) + @(Get-ChildItem -LiteralPath $env:FP_FIXTURES -Filter '*.ps1' | ForEach-Object FullName)
foreach ($file in $files) {
  $tokens = $null; $parseErrors = $null
  $null = [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$parseErrors)
  if ($parseErrors.Count) { throw "$file : $($parseErrors | Out-String)" }
}
'''
            run = subprocess.run([shutil.which('pwsh') or shutil.which('powershell'), '-NoProfile',
                                  '-NonInteractive', '-Command', command],
                                 env={**os.environ, 'FP_REPO': str(ROOT), 'FP_FIXTURES': str(root)},
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
