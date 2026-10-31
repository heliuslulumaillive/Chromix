#!/usr/bin/env python3
"""Verify an existing Actions candidate; never restore source or invoke a compiler."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import zlib

from fingerprint_subprocess import run_command
from verify_windows_bundle import extract_archive, runtime_smoke, sha256

REPO = Path(__file__).resolve().parents[1]
VERSION = '155.0.8059.39'
GOOGLE_VARIANT = 'google-services'
FINGERPRINT_VARIANT = 'fingerprint-modes-win155'
SECURITY_VARIANT = 'fingerprint-modes-win156'
VARIANT_VERSIONS = {
    GOOGLE_VARIANT: VERSION,
    FINGERPRINT_VARIANT: VERSION,
    SECURITY_VARIANT: '156.0.8078.79',
}
FINGERPRINT_VARIANTS = frozenset((FINGERPRINT_VARIANT, SECURITY_VARIANT))
ARTIFACT_NAMES = {
    GOOGLE_VARIANT: 'google-services-win155-x64',
    FINGERPRINT_VARIANT: 'fingerprint-modes-win155-x64',
    SECURITY_VARIANT: 'security-win156-x64',
}
FINGERPRINT_REPORTS = ('modes-native.json', 'modes-runtime.json', 'fingerprint-modes-capabilities.json')
FINGERPRINT_COLLECTOR_MODULES = (
    'fingerprint_audio_acceptance.mjs', 'fingerprint_font_acceptance.mjs',
    'fingerprint_reference_acceptance.mjs', 'fingerprint_renderbuffer_acceptance.mjs',
    'pixel_semantic_acceptance.mjs',
)
FINGERPRINT_RUNTIME_HARNESS = 'tools/verify_fingerprint_modes_runtime.mjs'
FINGERPRINT_RUNTIME_ORACLE = 'tools/tests/fixtures/fingerprint-runtime-v1/positive-oracles.json'
RETIREMENT_MANIFEST_INPUT = 'patches/lite-win156/retirement-manifest.json'
MASK_FIRST_ORACLE_REVISION = '9a06334258ed6039c597360038183f8693347e45'
MASK_FIRST_ORACLE_SHA256 = '2bcefcc4b7b8ab34b070b964a4fdd81c643e88052b48fd0c3a75cf18d8965465'
MASK_FIRST_COUNTS = {
    'mask_first_equivalence_cases': 93941,
    'mask_first_equivalence_bytes': 439779036,
    'mask_first_corpus_records': 3,
    'mask_first_mutation_controls': 8,
    'mask_first_mutation_rejections': 8,
}
FINGERPRINT_RUNTIME_SCENARIOS = {
    'native_off_control', 'all_native', 'legacy_seeded', 'canvas_shardx',
    'webgl_cloak', 'shard_surfaces', 'memory_custom', 'hardware_cloak', 'gpu_custom',
    'webgl_shardx', 'fonts_shard42', 'fonts_shard43', 'fonts_native10026', 'fonts_allowlists', 'fonts_empty',
    'cloak_voices', 'cloak_voices_no_local', 'cloak_offset', 'cloak_text_positive', 'audio_native_macos',
    'audio_defaults_44100', 'audio_defaults_32000', 'audio_default_mono', 'audio_default_stereo',
    'audio_default_32', 'gpu_masked_custom',
    'headless_custom_dpr2', 'headless_custom_fractional', 'headless_explicit_info',
    'headless_force_scale', 'headless_native_ignored', 'headless_off_ignored',
}
FINGERPRINT_POSITIVE_SCENARIOS = {
    'legacy_canvas': {'legacy_seeded'}, 'shard_canvas': {'canvas_shardx'}, 'cloak_canvas': {'hardware_cloak'},
    'shard_webgl': {'webgl_shardx'}, 'cloak_webgl': {'webgl_cloak'}, 'shard_audio': {'shard_surfaces'},
    'shard_rects': {'shard_surfaces'}, 'shard_text': {'shard_surfaces'}, 'shard_sensors': {'shard_surfaces'},
    'shard_media': {'shard_surfaces'}, 'memory_custom': {'memory_custom'}, 'hardware_profile': {'hardware_cloak'},
    'webgl_resource_limits': {'all_native', 'gpu_custom'}, 'webgpu_capabilities': {'gpu_custom', 'hardware_cloak'},
    'hardware_gpu_identity': {'hardware_cloak'}, 'masked_webgl': {'gpu_masked_custom'},
    'shard_fonts': {'fonts_shard42', 'fonts_shard43'}, 'native_fonts': {'fonts_native10026'},
    'font_allowlists': {'fonts_allowlists', 'fonts_empty'},
    'cloak_voices': {'cloak_voices', 'cloak_voices_no_local'}, 'cloak_offset': {'cloak_offset'},
    'cloak_text': {'cloak_text_positive'}, 'native_audio': {'audio_native_macos'},
    'audio_defaults': {'audio_defaults_44100', 'audio_defaults_32000'},
    'audio_channels': {'audio_defaults_44100', 'audio_defaults_32000', 'audio_default_mono',
                       'audio_default_stereo', 'audio_default_32'},
    'headless_screen': {'headless_custom_dpr2', 'headless_custom_fractional', 'headless_explicit_info',
                        'headless_force_scale', 'headless_native_ignored', 'headless_off_ignored'},
}
FINGERPRINT_EXTRA_SCOPES = {
    'all_native': ('fonts', 'reference', 'audio', 'renderbuffer', 'pixel_semantics'),
    'gpu_custom': ('renderbuffer',),
    'canvas_shardx': ('pixel_semantics',), 'webgl_shardx': ('pixel_semantics',),
    **{name: ('headless_screen',) for name in FINGERPRINT_POSITIVE_SCENARIOS['headless_screen']},
    **{name: ('fonts',) for name in ('fonts_shard42', 'fonts_shard43', 'fonts_native10026', 'fonts_allowlists', 'fonts_empty')},
    **{name: ('reference',) for name in ('cloak_voices', 'cloak_voices_no_local', 'cloak_offset', 'cloak_text_positive')},
    **{name: ('audio',) for name in ('audio_native_macos', 'audio_defaults_44100', 'audio_defaults_32000',
                                   'audio_default_mono', 'audio_default_stereo', 'audio_default_32')},
}
FINGERPRINT_BASIC_SCENARIOS = set(FINGERPRINT_EXTRA_SCOPES) - {'all_native', 'gpu_custom', 'canvas_shardx', 'webgl_shardx'}
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


def variant_version(build_variant):
    if not isinstance(build_variant, str) or build_variant not in VARIANT_VERSIONS:
        raise ValueError('Unsupported exact build variant')
    return VARIANT_VERSIONS[build_variant]


def validate_source_run(source, *, build_variant=GOOGLE_VARIANT):
    variant_version(build_variant)
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', str(source.get('repository', '')))
            or type(source.get('run_id')) is not int or source['run_id'] <= 0
            or type(source.get('artifact_id')) is not int or source['artifact_id'] <= 0
            or not re.fullmatch('[0-9a-f]{40}', str(source.get('head_sha', '')))
            or source.get('artifact_name') != ARTIFACT_NAMES[build_variant]):
        raise ValueError('Invalid source run or artifact identity')


def validate_source_recipe(inner, outer, repo=REPO, *, build_variant=GOOGLE_VARIANT):
    version = variant_version(build_variant)
    if inner != outer:
        raise ValueError('Packaged source receipt differs from the artifact receipt')
    if inner.get('variant') != 'google-services-self-use' or inner.get('chromium_version') != version:
        raise ValueError('Candidate is not the expected Google-service variant/version pair')
    if build_variant in FINGERPRINT_VARIANTS and inner.get('build_variant') != build_variant:
        raise ValueError('Candidate is not the fingerprint-modes build variant')
    if build_variant == GOOGLE_VARIANT and inner.get('build_variant') not in (None, GOOGLE_VARIANT):
        raise ValueError('Fingerprint receipt cannot be admitted as a Google-only variant')
    if inner.get('patch_sha256') != sha256(repo / 'patches/google-services/0001-restore-google-services.patch'):
        raise ValueError('Candidate was built with a different Google restoration patch')
    inputs = inner.get('preparation_inputs', {})
    if not isinstance(inputs, dict):
        raise ValueError('Candidate preparation inputs must be an object')
    names = ['build/google-services-win155.json',
             'patches/google-services/0001-restore-google-services.json',
             'patches/google-services/0002-windows-native-passkey-discovery.patch',
             'patches/chromium155-windows/0233-renderer-timezone-webstring-api.patch']
    if build_variant in FINGERPRINT_VARIANTS:
        names.append('build/fingerprint-modes-capabilities.json')
        config = read_object(repo / 'build/google-services-win155.json')
        for name in config.get('restoration_overlays', []):
            path = (repo / name).resolve()
            if not path.is_relative_to(repo.resolve()) or not path.is_file():
                raise ValueError('Invalid restoration overlay path')
            names.append(name)
        if 'patches/fingerprint-modes/0011-pixel-semantic-preservation.patch' in config.get('restoration_overlays', []):
            names.extend('tools/tests/fixtures/pixel-semantics-v1/' + name
                         for name in ('cases.tsv', 'pixels.bin', 'provenance.json'))
        if build_variant == SECURITY_VARIANT:
            if config.get('chromium_version') != version or not config.get('security_restoration'):
                raise ValueError('Security candidate source configuration has the wrong version or security scope')
            names.extend(('tools/prepare_google_services.py', 'tools/verify_google_services_source.py',
                          'tools/restored_source_resources.py', 'build/args.security.gn',
                          'patches/google-services/0001-restore-google-services.patch'))
            if config.get('lite_retirement'):
                names.extend(('tools/lite_retirement.py',
                              'patches/lite-win156/retirement-manifest.json'))
            for spec in config.get('restore_pruned_resources', {}).values():
                name = spec.get('repository_path') if isinstance(spec, dict) else None
                if not isinstance(name, str):
                    raise ValueError('Security resource input is missing its repository path')
                path = (repo / name).resolve()
                if not path.is_relative_to(repo.resolve()) or not path.is_file():
                    raise ValueError('Invalid security resource input path')
                names.append(name)
            if (repo / 'build/gpu-binding-capabilities.json').is_file():
                names.append('build/gpu-binding-capabilities.json')
    for name in dict.fromkeys(names):
        if inputs.get(name) != sha256(repo / name):
            raise ValueError('Candidate source inputs do not match: ' + name)
    if build_variant == SECURITY_VARIANT:
        if set(inputs) != set(names) or inner.get('preparation_sha256') != hashlib.sha256(
                json.dumps(inputs, sort_keys=True).encode()).hexdigest():
            raise ValueError('Security source recipe inventory or digest differs')


def validate_mode_native(report, payload):
    expected = {
        'config-legacy': ('legacy', {'configuration_checks'}, '--config-legacy'),
        'config-chromix': ('chromix', {'configuration_checks'}, '--config-chromix'),
        'production-helpers-and-config': ('production', {
            'graphics_cases', 'checks', 'gpu_checks', 'hardware_checks', 'font_allowlist_checks',
            'audio_default_checks', 'voice_compat_checks', 'configuration_checks', 'pixel_semantic_corpus_records',
            *MASK_FIRST_COUNTS}, None),
    }
    processes, groups = report.get('processes'), report.get('groups')
    if (report.get('status') != 'passed'
            or report.get('browser_sha256') != payload['chrome.exe']
            or report.get('chrome_dll_sha256') != payload['chrome.dll']
            or not isinstance(processes, list) or len(processes) != len(expected)
            or not isinstance(groups, dict) or set(groups) != {'legacy', 'chromix', 'production'}):
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
        if (not isinstance(arguments, list) or len(arguments) != 1
                or not isinstance(arguments[0], str) or not arguments[0]
                or (argument is not None and arguments != [argument])
                or (argument is None and not arguments[0].replace('\\', '/').endswith('/tools/tests/fixtures'))):
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
            counts[group + '.' + field] = values[0]
        if group == 'production':
            graphics = [record for record in records if 'graphics_cases' in record]
            nested = graphics[0].get('groups') if len(graphics) == 1 else None
            count = nested.get('pixel_semantic_properties') if isinstance(nested, dict) else None
            if type(count) is not int or count <= 0:
                raise ValueError('Native pixel semantic properties did not execute')
            counts['production.groups.pixel_semantic_properties'] = count
            equivalents = [record for record in records if 'mask_first_equivalence_cases' in record]
            if len(equivalents) != 1:
                raise ValueError('Native mask-first equivalence evidence is missing or duplicated')
            equivalent = equivalents[0]
            if (equivalent.get('mask_first_oracle_revision') != MASK_FIRST_ORACLE_REVISION
                    or equivalent.get('mask_first_oracle_sha256') != MASK_FIRST_ORACLE_SHA256):
                raise ValueError('Native mask-first oracle identity does not match the frozen baseline')
            if any(type(equivalent.get(name)) is not int or equivalent[name] != value
                   for name, value in MASK_FIRST_COUNTS.items()):
                raise ValueError('Native mask-first equivalence matrix or negative controls are incomplete')
            if (type(nested.get('pixel_mask_first_equivalence')) is not int
                    or nested['pixel_mask_first_equivalence'] != MASK_FIRST_COUNTS['mask_first_equivalence_cases']):
                raise ValueError('Native graphics test did not execute the complete mask-first matrix')
            counts['production.groups.pixel_mask_first_equivalence'] = nested['pixel_mask_first_equivalence']
        if not any(r.get('status') == 'passed' and r.get('configuration_checks', 0) > 0 for r in records):
            raise ValueError('Native mode configuration acceptance is missing')
    if len(binaries) != 1:
        raise ValueError('Native mode processes used different executables')
    return counts


def validate_positive_coverage(report, checks):
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
                     record.get('acceptance_group') not in {'cloak_text', 'shard_fonts', 'native_fonts', 'font_allowlists'}))):
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


def _semantic_bytes(value, count=None):
    if not isinstance(value, str) or not value or len(value) > 4 * 1024**2:
        raise ValueError('Pixel semantic bytes are absent or oversized')
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError('Pixel semantic bytes are not base64') from error
    if base64.b64encode(data).decode() != value or (count is not None and len(data) != count):
        raise ValueError('Pixel semantic byte count differs')
    return data


def _semantic_png(value, *, pixels=False):
    data = _semantic_bytes(value)
    if len(data) < 45 or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Screenshot/semantic PNG is absent')
    width = height = channels = None
    compressed = bytearray()
    end = False
    at = 8
    while at + 12 <= len(data):
        count = struct.unpack_from('>I', data, at)[0]
        if count > len(data) - at - 12:
            raise ValueError('Truncated PNG')
        kind, body = data[at + 4:at + 8], data[at + 8:at + 8 + count]
        if (zlib.crc32(kind + body) & 0xffffffff) != struct.unpack_from('>I', data, at + 8 + count)[0]:
            raise ValueError('PNG checksum mismatch')
        if kind == b'IHDR':
            if width is not None or count != 13:
                raise ValueError('Invalid PNG IHDR')
            width, height = struct.unpack_from('>II', body)
            if not (0 < width <= 4096 and 0 < height <= 4096):
                raise ValueError('PNG dimensions are outside bounded viewport')
            if pixels:
                if width > 256 or height > 256 or body[8] != 8 or body[9] not in (2, 6) or any(body[10:]):
                    raise ValueError('Pixel semantic PNG format differs')
                channels = 4 if body[9] == 6 else 3
        elif kind == b'IDAT':
            compressed.extend(body)
        elif kind == b'IEND':
            end = True
            break
        at += count + 12
    if not width or not end or not compressed:
        raise ValueError('PNG has no complete image')
    answer = {'width': width, 'height': height, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    if pixels:
        stride = width * channels
        expected = (stride + 1) * height
        inflater = zlib.decompressobj()
        packed = inflater.decompress(bytes(compressed), expected + 1)
        if len(packed) != expected or not inflater.eof or inflater.unconsumed_tail:
            raise ValueError('PNG decompression is incomplete or oversized')
        raw = bytearray(stride * height)
        for y in range(height):
            mode = packed[y * (stride + 1)]
            if mode > 4:
                raise ValueError('Invalid PNG filter')
            for x in range(stride):
                left = raw[y * stride + x - channels] if x >= channels else 0
                up = raw[(y - 1) * stride + x] if y else 0
                diagonal = raw[(y - 1) * stride + x - channels] if y and x >= channels else 0
                p = left + up - diagonal
                distances = (abs(p - left), abs(p - up), abs(p - diagonal))
                paeth = left if distances[0] <= min(distances[1:]) else up if distances[1] <= distances[2] else diagonal
                predictor = (0, left, up, (left + up) // 2, paeth)[mode]
                raw[y * stride + x] = (packed[y * (stride + 1) + x + 1] + predictor) & 255
        rgba = bytearray(width * height * 4)
        for i in range(width * height):
            rgba[i * 4:i * 4 + 3] = raw[i * channels:i * channels + 3]
            rgba[i * 4 + 3] = raw[i * channels + 3] if channels == 4 else 255
        answer['rgba'] = bytes(rgba)
    return answer


def _semantic_crop(data, width, height, rect):
    x, y, w, h = rect
    output = bytearray(w * h * 4)
    for row in range(h):
        for col in range(w):
            if 0 <= x + col < width and 0 <= y + row < height:
                begin = ((y + row) * width + x + col) * 4
                output[(row * w + col) * 4:(row * w + col + 1) * 4] = data[begin:begin + 4]
    return bytes(output)


def _semantic_crop_specs(width, height, canvas=False):
    values = [[0, 0, 1, 1], [width-1, 0, 1, 1], [0, height-1, 1, 1], [width-1, height-1, 1, 1],
              [1, 1, 1, 1], [0, 7, 1, 9], [width-1, 7, 1, 9], [8, 0, 9, 1], [8, height-1, 9, 1], [11, 13, 18, 16]]
    return values + ([[-1, -1, 3, 3]] if canvas else [])


def _semantic_positive(native, target):
    changed = [(i, abs(a-b)) for i, (a, b) in enumerate(zip(native, target)) if a != b]
    return len(native) == len(target) and 0 < len(changed) <= 256 and all(i % 4 != 3 and d == 1 for i, d in changed)


def validate_pixel_semantics(scenarios, report):
    required = set()
    records = {s['id']: s for s in scenarios}
    baseline = records['all_native']['extra']['pixel_semantics']
    for identity in ('all_native', 'canvas_shardx', 'webgl_shardx'):
        value = records[identity]['extra']['pixel_semantics']
        if value.get('errors') != [] or not isinstance(value.get('canvas'), dict) or set(value['canvas']) != {'flat', 'text', 'gradient', 'embedded'}:
            raise ValueError('Pixel semantic scenes are missing or failed')
        required.add((identity, 'pixel_semantics_no_errors'))
        for name in ('flat', 'text', 'gradient', 'embedded'):
            row = value['canvas'][name]
            w, h = (64 if name == 'flat' else 128), (96 if name == 'embedded' else 64)
            if not isinstance(row, dict) or row.get('width') != w or row.get('height') != h:
                raise ValueError('Canvas semantic dimensions are missing')
            full = _semantic_bytes(row.get('full'), w*h*4)
            if full != _semantic_bytes(row.get('repeat'), w*h*4):
                raise ValueError('Canvas semantic repeat differs')
            crops = row.get('crops')
            specs = _semantic_crop_specs(w, h, True)
            if not isinstance(crops, list) or not all(isinstance(c,dict) for c in crops) or [c.get('rect') for c in crops] != specs:
                raise ValueError('Canvas semantic edge crops are missing')
            for c in crops:
                if _semantic_crop(full, w, h, c['rect']) != _semantic_bytes(c.get('raw'), c['rect'][2]*c['rect'][3]*4):
                    raise ValueError('Canvas semantic crop differs')
            png = _semantic_png(row.get('png'), pixels=True)
            if (png['width'], png['height'], png['rgba']) != (w, h, full):
                raise ValueError('Canvas semantic PNG differs')
            prefix = 'semantic_canvas_' + name + '_'
            required.update((identity, prefix + check) for check in ('present', 'repeat', 'edge_crops', 'png'))
            if name == 'flat':
                if full != bytes([137, 73, 91, 255]) * (w*h):
                    raise ValueError('Flat canvas was changed')
                required.add((identity, prefix + 'exact'))
            elif identity == 'canvas_shardx':
                native = _semantic_bytes(baseline['canvas'][name].get('full'), w*h*4)
                if not _semantic_positive(native, full):
                    raise ValueError('Rendered canvas has no bounded positive perturbation')
                required.add((identity, prefix + 'rendered_positive'))
            if name == 'embedded':
                f = row.get('flatRegion', {})
                flat = _semantic_bytes(f.get('raw'), 36*28*4)
                if f.get('rect') != [66, 34, 36, 28] or flat != bytes([137, 73, 91, 255])*(36*28) or flat != _semantic_crop(full,w,h,f['rect']):
                    raise ValueError('Embedded flat canvas region was changed')
                required.add((identity,prefix+'flat_region_exact'))
        webgl = value.get('webgl')
        if not isinstance(webgl, dict) or set(webgl) != {'webgl', 'webgl2'}:
            raise ValueError('WebGL semantic probes missing')
        for kind in ('webgl','webgl2'):
            row, native = webgl[kind], baseline.get('webgl', {}).get(kind)
            prefix='semantic_'+kind+'_'
            if isinstance(native, dict) and native.get('supported') is False:
                if not isinstance(row, dict) or row.get('supported') is not False or not row.get('reason'):
                    raise ValueError('Unavailable semantic context lacks explicit evidence')
                required.add((identity,prefix+'unavailable_record'))
                if identity == 'webgl_shardx':
                    missing=[r for r in report['not_tested'] if r.get('scenario')==identity and r.get('name')==prefix+'positive' and r.get('acceptance_group')=='shard_webgl' and r.get('reason')=='Native WebGL context unavailable']
                    if len(missing)!=1: raise ValueError('Semantic WebGL unavailable result was hidden')
                continue
            if not isinstance(row, dict) or row.get('supported') is not True or row.get('error') != 0:
                raise ValueError('WebGL semantic context failed')
            full=_semantic_bytes(row.get('full'),16384)
            if full!=_semantic_bytes(row.get('repeat'),16384): raise ValueError('WebGL semantic repeat differs')
            clear=row.get('clear',{})
            if _semantic_bytes(clear.get('full'),16384)!=bytes([137,73,91,255])*4096 or clear.get('repeat')!=clear.get('full'):
                raise ValueError('WebGL clear pixels were changed')
            crops=row.get('crops');specs=_semantic_crop_specs(64,64)
            if not isinstance(crops,list) or not all(isinstance(c,dict) for c in crops) or [c.get('rect') for c in crops]!=specs: raise ValueError('WebGL semantic crops missing')
            for c in crops:
                if _semantic_crop(full,64,64,c['rect'])!=_semantic_bytes(c.get('raw'),c['rect'][2]*c['rect'][3]*4): raise ValueError('WebGL semantic crop differs')
            png=_semantic_png(row.get('png'),pixels=True)
            flipped=b''.join(full[y*256:(y+1)*256] for y in reversed(range(64)))
            if (png['width'],png['height'],png['rgba'])!=(64,64,flipped): raise ValueError('WebGL semantic PNG differs')
            required.update((identity,prefix+check) for check in ('present','clear_exact','repeat','edge_crops','png'))
            if identity=='webgl_shardx':
                if not _semantic_positive(_semantic_bytes(native.get('full'),16384),full): raise ValueError('Rendered WebGL has no bounded positive perturbation')
                required.add((identity,prefix+'rendered_positive'))
            if kind=='webgl2':
                pbo=row.get('pbo',{});state=pbo.get('state',{})
                packed=_semantic_bytes(pbo.get('raw'),18528)
                unpacked=bytearray();written=set()
                for y in range(64):
                    begin=16+(2+y)*280+3*4;unpacked.extend(packed[begin:begin+256]);written.update(range(begin,begin+256))
                if pbo.get('error')!=0 or bytes(unpacked)!=full or _semantic_bytes(pbo.get('unpacked'),16384)!=full:
                    raise ValueError('PBO semantic readback differs')
                if pbo.get('unwrittenUntouched') is not True or any(v!=165 for i,v in enumerate(packed) if i not in written): raise ValueError('PBO padding was changed')
                if state!={'alignment':8,'rowLength':69,'skipRows':2,'skipPixels':3,'pboBinding':True,'readFramebuffer':True,'readBuffer':True}: raise ValueError('PBO/PACK state was not restored')
                required.update((identity,prefix+check) for check in ('pbo_readback','pack_state_restored','pbo_padding_untouched'))
    return required


def validate_headless_screens(scenarios):
    required=set();records={s['id']:s for s in scenarios}
    targets={'headless_custom_dpr2':2,'headless_custom_fractional':1.25,'headless_explicit_info':1.5,'headless_force_scale':1.5}
    for identity in FINGERPRINT_POSITIVE_SCENARIOS['headless_screen']:
        value=records[identity]['extra']['headless_screen'];page=value.get('page',{});shot=value.get('screenshot',{})
        png=_semantic_png(shot.get('png'))
        if any(shot.get(k)!=png[k] for k in ('width','height','bytes','sha256')): raise ValueError('Headless PNG identity mismatch')
        viewport=value.get('layout',{}).get('cssVisualViewport',{})
        w,h=viewport.get('clientWidth',page.get('innerWidth')),viewport.get('clientHeight',page.get('innerHeight'))
        scale=targets.get(identity,page.get('dpr'))
        if any(type(v) not in (int,float) or not 0<v<65536 for v in (w,h,scale)) or abs(png['width']-w*scale)>1 or abs(png['height']-h*scale)>1:
            raise ValueError('Headless native raster does not match required scale')
        command=value.get('effectiveCommandLine')
        if not isinstance(command,str) or '--headless' not in command: raise ValueError('Effective headless command line is missing')
        required.update((identity,k) for k in ('headless_png_identity','headless_raster_scale','headless_effective_command_record'))
        if identity.startswith('headless_custom_'):
            screen=page.get('screen',{})
            if screen.get('width')!=640 or screen.get('height')!=480 or screen.get('availHeight')!=440 or page.get('dpr')!=scale:
                raise ValueError('Custom headless display differs from explicit input')
            if '--screen-info=' not in command or 'devicePixelRatio='+str(scale) not in command: raise ValueError('Native headless display was not injected')
            required.update((identity,k) for k in ('headless_declared_display','headless_native_display_injected'))
        elif identity=='headless_explicit_info':
            if '960x720 devicePixelRatio=1.5' not in command or command.count('--screen-info=')!=1: raise ValueError('Caller screen-info was replaced')
            required.add((identity,'headless_caller_screen_preserved'))
        elif identity=='headless_force_scale':
            if '--force-device-scale-factor=1.5' not in command or '--screen-info=' in command: raise ValueError('Caller force scale was replaced')
            required.add((identity,'headless_caller_scale_preserved'))
        else:
            if any(flag in command for flag in ('--screen-info=','--uxr-screen-width=','--uxr-device-pixel-ratio=')): raise ValueError('Native/global-off display was mapped')
            required.add((identity,'headless_native_off_not_mapped'))
    return required


def retirement_cases():
    cases=[]
    for prefix in ('fingerprint','uxr'):
        for surface in ('fonts','audio','media'):
            for value in ('chromix','cloak'):
                key=f'{prefix}-{surface}-mode'
                cases.append({'id':f'{prefix}_{surface}_{value}','argument':f'--{key}={value}',
                              'diagnostic':f'Unsupported Lite surface mode: {key}={value}'})
        for name,value in (('audio-render','native'),('font-policy','native'),('font-whitelist','Arial'),
                           ('windows-font-metrics','false'),('canvas-bridge','ws://127.0.0.1:65534'),
                           ('canvas-bridge-unsafe','false'),('devtools-runtime-suppression','false'),('keyboard-layout','native')):
            key=f'{prefix}-{name}'
            cases.append({'id':f"{prefix}_{name.replace('-', '_')}",'argument':f'--{key}={value}',
                          'diagnostic':f'Retired Lite kernel switch: {key}'})
    for name in ('synthetic-device-tests','webauthn-uvpaa','plugins'):
        key=f'uxr-{name}'
        cases.append({'id':key.replace('-','_'),'argument':f'--{key}=false',
                      'diagnostic':f'Retired Lite kernel switch: {key}'})
    return cases


def validate_retirement_rejections(value):
    expected=retirement_cases()
    if (not isinstance(value,dict) or value.get('version')!=1 or value.get('status')!='passed'
            or value.get('expected_cases')!=len(expected) or not isinstance(value.get('cases'),list)
            or len(value['cases'])!=len(expected)):
        raise ValueError('Native retired-input rejection matrix is missing or incomplete')
    for actual,wanted in zip(value['cases'],expected):
        if (not isinstance(actual,dict) or any(actual.get(k)!=v for k,v in wanted.items())
                or actual.get('passed') is not True or type(actual.get('exit_code')) is not int
                or actual['exit_code']!=1 or actual.get('signal') is not None
                or actual.get('timed_out') is not False or actual.get('spawn_error') is not None
                or not isinstance(actual.get('stderr'),str) or wanted['diagnostic'] not in actual['stderr']
                or actual.get('cleanup') is not None):
            raise ValueError('Retired input did not receive an explicit native rejection: '+wanted['id'])
    return len(expected)


def validate_runtime_collector_identity(report):
    for key, relative in (('harness_sha256', FINGERPRINT_RUNTIME_HARNESS),
                          ('oracle_sha256', FINGERPRINT_RUNTIME_ORACLE)):
        if report.get(key) != sha256(REPO / relative):
            raise ValueError('Fingerprint runtime collector source identity differs: ' + key)
    modules = report.get('collector_modules_sha256')
    if not isinstance(modules, dict) or set(modules) != set(FINGERPRINT_COLLECTOR_MODULES):
        raise ValueError('Fingerprint runtime collector module inventory differs from the selected source')
    for name in FINGERPRINT_COLLECTOR_MODULES:
        if modules[name] != sha256(REPO / 'tools' / name):
            raise ValueError('Fingerprint runtime collector module identity differs: ' + name)


def validate_mode_runtime(report, payload):
    validate_retirement_rejections(report.get("retirement_rejections"))
    validate_runtime_collector_identity(report)
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
    shard_probe = next(s for s in scenarios if s['id'] == 'webgl_shardx')['probe'].get('webgl', {})
    for kind in ('webgl', 'webgl2'):
        names = {('webgl_shardx', kind + '_v2_' + name) for name in (
            'sparse_positive', 'repeat', 'absolute_crop', 'export_matches_readback')}
        actual = shard_probe.get(kind) if isinstance(shard_probe, dict) else None
        unavailable = [row for row in report['not_tested'] if isinstance(row, dict)
                       and row.get('scenario') == 'webgl_shardx'
                       and row.get('name') == kind + '_positive'
                       and row.get('acceptance_group') == 'shard_webgl']
        # A candidate can retain an explicit unavailable-context observation,
        # but it must never manufacture positive WebGL acceptance from it.
        if isinstance(actual, dict) and actual.get('supported') is False:
            if (not isinstance(actual.get('reason'), str) or not actual['reason'].strip()
                    or len(unavailable) != 1
                    or unavailable[0].get('reason') != 'Native WebGL context unavailable'
                    or unavailable[0].get('optional_candidate') is not None
                    or names & executed):
                raise ValueError('ShardX WebGL unavailable evidence is missing or contradictory: ' + kind)
        else:
            if unavailable:
                raise ValueError('ShardX WebGL unavailable evidence contradicts the probe: ' + kind)
            required.update(names)
    baseline_audio=next(row for row in scenarios if row['id']=='all_native')['extra']['audio'].get('referenceCases')
    current_audio=next(row for row in scenarios if row['id']=='audio_native_macos')['extra']['audio'].get('referenceCases')
    if not isinstance(baseline_audio,list) or not isinstance(current_audio,list):
        raise ValueError('Native audio reference samples are missing')
    for name,length in (('match_5000',5000),('match_1000',1000),('match_44100',44100)):
        pairs=[[row for row in values if isinstance(row,dict) and row.get('id')==name]
               for values in (baseline_audio,current_audio)]
        if any(len(rows)!=1 for rows in pairs): raise ValueError('Native audio reference identity is missing or duplicated')
        a,b=pairs[0][0],pairs[1][0]
        for row in (a,b):
            digest=row.get('sha256')
            if (row.get('error') or row.get('sampleRate')!=44100 or row.get('numberOfChannels')!=1
                    or row.get('length')!=length or row.get('byteLength')!=length*4
                    or row.get('state')!='closed' or not isinstance(digest,str)
                    or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest)
                    or row.get('copySha256')!=digest or not isinstance(row.get('spec'),dict)):
                raise ValueError('Native audio reference samples are malformed')
        if a['sha256']!=b['sha256'] or a['spec']!=b['spec']:
            raise ValueError('Native audio changed after selecting native mode')
        required.add(('audio_native_macos',name+'_native_reference_bytes'))
    required.update(validate_pixel_semantics(scenarios, report))
    required.update(validate_headless_screens(scenarios))
    if not required.issubset(executed):
        raise ValueError('Runtime mode routing coverage is incomplete')
    validate_positive_coverage(report, checks)
    if report['positive_acceptance'].get('headless_screen',{}).get('status') != 'observed_pass':
        raise ValueError('Headless screen synchronization must execute, not remain untested')
    return len(checks)


def validate_packaged_retirement_contract(bundle, capabilities):
    declaration = capabilities.get('lite_retirement')
    if (not isinstance(declaration, dict) or type(declaration.get('version')) is not int
            or declaration['version'] != 1
            or declaration.get('manifest') != 'retirement-manifest.json'
            or declaration.get('requires_new_exact_dll_acceptance') is not True):
        raise ValueError('Windows 156 candidate lacks the required retirement contract declaration')
    path = bundle / declaration['manifest']
    source_path = bundle / 'google-services-source.json'
    for required in (path, source_path):
        if required.is_symlink() or not required.is_file():
            raise ValueError('Required packaged retirement/source receipt is missing or linked')
    expected = sha256(REPO / RETIREMENT_MANIFEST_INPUT)
    if sha256(path) != expected:
        raise ValueError('Packaged retirement manifest differs from the selected source')
    source = read_object(source_path)
    inputs = source.get('preparation_inputs')
    if (source.get('variant') != 'google-services-self-use'
            or source.get('build_variant') != SECURITY_VARIANT
            or source.get('chromium_version') != variant_version(SECURITY_VARIANT)
            or not isinstance(inputs, dict)
            or inputs.get(RETIREMENT_MANIFEST_INPUT) != expected
            or inputs.get('build/fingerprint-modes-capabilities.json') !=
                sha256(bundle / 'fingerprint-modes-capabilities.json')):
        raise ValueError('Packaged retirement contract is not bound to the prepared source receipt')
    return {'manifest_sha256': expected, 'source_receipt_sha256': sha256(source_path)}


def validate_fingerprint_tests(report_path, bundle, payload_hashes=None, *, build_variant=FINGERPRINT_VARIANT):
    """Validate executed evidence, without rerunning an unavailable native exe."""
    version = variant_version(build_variant)
    if build_variant not in FINGERPRINT_VARIANTS:
        raise ValueError('Fingerprint evidence requires an exact fingerprint build variant')
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
            or capabilities.get('build_variant') != build_variant
            or capabilities.get('chromium_version') != version
            or packaged_capabilities.is_symlink() or not packaged_capabilities.is_file()
            or sha256(packaged_capabilities) != files['fingerprint-modes-capabilities.json']
            or (build_variant == SECURITY_VARIANT and sha256(packaged_capabilities) !=
                sha256(REPO / 'build/fingerprint-modes-capabilities.json'))
            or runtime.get('capabilities_sha256') != files['fingerprint-modes-capabilities.json']):
        raise ValueError('Fingerprint capabilities identity differs from the packaged contract')
    retirement = (validate_packaged_retirement_contract(bundle, capabilities)
                  if build_variant == SECURITY_VARIANT else None)
    if (report.get('required_runtime_checks') != runtime_checks
            or report.get('not_tested') != runtime['not_tested']
            or report.get('coverage_limits') != runtime['coverage_limits']
            or report.get('positive_acceptance') != runtime['positive_acceptance']
            or report.get('full_strategy_acceptance') is not runtime['full_strategy_acceptance']
            or report.get('partial') is not runtime['partial']):
        raise ValueError('Fingerprint summary does not preserve actual runtime coverage')
    return {'status': 'passed', 'report_sha256': sha256(report_path), 'files': files,
            'retirement_contract': retirement,
            'native_executed_counts': native_counts, 'runtime_checks': runtime_checks,
            'not_tested': runtime['not_tested'],
            'positive_acceptance': runtime['positive_acceptance'],
            'full_strategy_acceptance': runtime['full_strategy_acceptance'], 'partial': runtime['partial'],
            'execution_scope': 'Validated build-time native/runtime receipts bound to these exact browser bytes',
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


def validate_security_runtime(report_path, source_record_path, bundle, payload_hashes=None):
    """Validate existing 156 security evidence only; importing the probe never runs it."""
    try:
        from . import verify_security_runtime as security
    except ImportError:
        import verify_security_runtime as security
    for path in (report_path, source_record_path):
        security.plain(path)
        if not path.is_file():
            raise ValueError('Mandatory Windows 156 security report/source record is missing')
    report = read_object(report_path, max_bytes=4 * 1024**2)
    checks = report.get('checks')
    if (type(report.get('schema_version')) is not int or report['schema_version'] != 1
            or report.get('status') != 'passed' or not isinstance(checks, list)
            or len(checks) != len(security.REQUIRED_CHECKS)
            or any(not isinstance(row, dict) or not isinstance(row.get('name'), str)
                   or row.get('passed') is not True for row in checks)):
        raise ValueError('Mandatory security runtime checks are missing, failed or malformed')
    names = [row['name'] for row in checks]
    if (set(names) != security.REQUIRED_CHECKS or len(names) != len(set(names))
            or not security.report_passed(report)):
        raise ValueError('Mandatory security runtime check inventory must be exact and unique')
    version = variant_version(SECURITY_VARIANT)
    if (report.get('browser_version') != 'Chrome/' + version
            or report.get('preferences_changed') is not False
            or report.get('trust_store_changed') is not False):
        raise ValueError('Security runtime version or read-only scope differs')
    if any(row['passed'] is not True for row in security.assess_pages(
            report.get('pages', {}), report.get('preferences', {}))):
        raise ValueError('Security page/default preference evidence contradicts its passed checks')
    payload = payload_hashes or {name: sha256(bundle / name) for name in ('chrome.exe', 'chrome.dll')}
    binding = report.get('binding')
    expected_binding = {
        'browser_sha256': payload['chrome.exe'], 'chrome_dll_sha256': payload['chrome.dll'],
        'verifier_sha256': sha256(REPO / 'tools/verify_security_runtime.py'),
        'recipe_tool_sha256': sha256(REPO / 'tools/security_build_record.py'),
        'requirements_sha256': sha256(REPO / 'tools/google-services-requirements.txt'),
    }
    if not isinstance(binding, dict) or any(binding.get(k) != v for k, v in expected_binding.items()):
        raise ValueError('Security runtime executable, DLL or verifier/tool identity differs')
    if not re.fullmatch('[a-f0-9]{64}', str(binding.get('python_sha256', ''))):
        raise ValueError('Security runtime producer Python identity is missing')
    locked = dict(re.findall(r'(?m)^(playwright|psutil)==([^\s#]+)$',
                            (REPO / 'tools/google-services-requirements.txt').read_text()))
    dependencies = binding.get('dependencies')
    if not isinstance(dependencies, dict) or set(dependencies) != {'playwright', 'psutil'}:
        raise ValueError('Security runtime dependency inventory is incomplete')
    for name, dependency in dependencies.items():
        if (not isinstance(dependency, dict) or dependency.get('version') != locked.get(name)
                or type(dependency.get('files')) is not int or dependency['files'] <= 0
                or not re.fullmatch('[a-f0-9]{64}', str(dependency.get('inventory_sha256', '')))):
            raise ValueError('Security runtime producer dependency identity differs: ' + name)
    source = security.source_identity(source_record_path, security.build_record.recipe(REPO))
    if report.get('source') != source:
        raise ValueError('Security runtime source-record SHA or recipe binding differs')
    source_record = read_object(source_record_path, max_bytes=4 * 1024**2)
    build = read_object(bundle / 'google-services-build.json')
    if (type(source_record.get('schema_version')) is not int
            or source_record.get('payload_build_variant') != SECURITY_VARIANT
            or source_record.get('core_commit') != security.build_record.CORE
            or source_record.get('windows_commit') != security.build_record.WINDOWS
            or build.get('build_variant') != SECURITY_VARIANT or build.get('chromium_version') != version
            or source_record.get('source_commit') != build.get('source_commit')
            or not re.fullmatch('[a-f0-9]{40}', str(source_record.get('source_commit', '')))
            or not re.fullmatch('[1-9][0-9]*', str(source_record.get('run_id', '')))
            or str(source_record['run_id']) != str(build.get('run_id'))
            or source['source_receipt_sha256'] != sha256(bundle / 'google-services-source.json')
            or source['gn_sha256'] != build.get('gn_args_sha256')):
        raise ValueError('Security source record does not match the packaged source/build receipt')
    gn = source_record.get('gn', {})
    content = gn.get('content')
    if (not isinstance(content, str) or type(gn.get('bytes')) is not int
            or len(content.encode('utf-8')) != gn['bytes']
            or hashlib.sha256(content.encode('utf-8')).hexdigest() != source['gn_sha256']):
        raise ValueError('Recorded exact GN contents do not match the compiled GN digest')
    for key, wanted in security.build_record.REQUIRED_FLAGS.items():
        values = re.findall(r'(?m)^\s*' + re.escape(key) + r'\s*=\s*([^#\r\n]+)', content)
        if len(values) != 1 or values[0].strip() != wanted:
            raise ValueError('Security GN contents contradict required flags: ' + key)
    return {'status': 'passed', 'report_sha256': sha256(report_path),
            'checks': sorted(names), 'binding': binding, 'source': source,
            'scope': 'Validated fresh-profile compiled pages/defaults tied to this artifact; no online threat verdict',
            'producer_python_and_dependencies': 'Identity metadata retained; not assumed equal to the verification host'}


def verify_candidate(artifact_dir, source, report_dir, record, *, build_variant=GOOGLE_VARIANT):
    version = variant_version(build_variant)
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
    if build_variant in FINGERPRINT_VARIANTS:
        build = read_object(bundle / 'google-services-build.json')
        if (build.get('build_variant') != build_variant
                or build.get('chromium_version') != version
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
            artifact_dir / 'fingerprint-modes-validation.json', bundle, before, build_variant=build_variant)
        if build_variant == SECURITY_VARIANT:
            record['security_runtime'] = validate_security_runtime(
                artifact_dir / 'security-runtime.json', artifact_dir / 'build-inputs.json', bundle, before)
    native = runtime_smoke(bundle_report, version, report_dir / 'native-smoke')
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
    if build_variant == SECURITY_VARIANT and (
            sha256(artifact_dir / 'security-runtime.json') != record['security_runtime']['report_sha256']
            or sha256(artifact_dir / 'build-inputs.json') != record['security_runtime']['source']['record_sha256']):
        raise ValueError('Mandatory security evidence changed during candidate verification')
    record.update(status='passed', files=before, runtime_checks=len(checks),
                  google_account_and_cloud_access='not_tested', windows_hello='not_tested',
                  google_password_manager_cloud_passkeys='not_tested')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--artifact-dir', type=Path)
    mode.add_argument('--native-tests-json', type=Path)
    mode.add_argument('--fingerprint-tests-json', type=Path)
    mode.add_argument('--security-tests-json', type=Path)
    parser.add_argument('--source-run-json', type=Path)
    parser.add_argument('--report-dir', type=Path)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--source-record', type=Path)
    parser.add_argument('--build-variant', choices=ARTIFACT_NAMES)
    args = parser.parse_args()
    if args.native_tests_json:
        executed = validate_native_passkey_tests(read_object(args.native_tests_json))
        print(json.dumps({'native_passkey_tests': 'passed', 'executed': len(executed)}))
        return 0
    if args.fingerprint_tests_json:
        if not args.bundle:
            parser.error('Fingerprint evidence verification requires --bundle')
        print(json.dumps(validate_fingerprint_tests(args.fingerprint_tests_json, args.bundle,
                         build_variant=args.build_variant or FINGERPRINT_VARIANT)))
        return 0
    if args.security_tests_json:
        if not args.bundle or not args.source_record or args.build_variant != SECURITY_VARIANT:
            parser.error('Security evidence requires --bundle, --source-record and the exact Windows 156 variant')
        print(json.dumps(validate_security_runtime(args.security_tests_json, args.source_record, args.bundle)))
        return 0
    if not args.source_run_json or not args.report_dir:
        parser.error('Artifact verification requires --source-run-json and --report-dir')
    args.report_dir.mkdir(parents=True, exist_ok=False)
    record = {'schema_version': 1, 'status': 'failed', 'compiler_invoked': False,
              'source_checkpoint_restored': False}
    try:
        verify_candidate(args.artifact_dir, read_object(args.source_run_json), args.report_dir, record,
                         build_variant=args.build_variant or GOOGLE_VARIANT)
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
