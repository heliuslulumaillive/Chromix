"""The current x64 product recipe, independent of historical patch machinery."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

try:
    from .google_services_inputs import owned
    from .platform_pins import load_pins
except ImportError:
    from google_services_inputs import owned
    from platform_pins import load_pins

MANIFEST = 'build/lite-windows158.json'
KIND = 'lite-windows158-product'
HEX = re.compile(r'[0-9a-f]{64}\Z')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(repo: Path):
    repo = Path(repo).absolute()
    data = json.loads(owned(repo, MANIFEST).read_text('utf-8'))
    payload = {key: value for key, value in data.items() if key != 'stack_sha256'}
    if (data.get('schema_version') != 1 or data.get('kind') != KIND
            or data.get('arch') != 'x64' or data.get('stack_sha256') != digest(
                json.dumps(payload, sort_keys=True, separators=(',', ':')).encode())):
        raise ValueError('Invalid or changed Lite product manifest')
    runtime = data['runtime']
    pins = load_pins(repo, 'windows')
    if owned(repo, 'UNGOOGLED_WINDOWS_VERSION').read_text('ascii').strip() != pins['UngoogledWindowsVersion']:
        raise ValueError('Windows packaging version file differs from the product pins')
    for key, pin in (('chromium_version', 'ChromiumVersion'), ('core_commit', 'UngoogledCommit'),
                     ('windows_commit', 'UngoogledWindowsCommit')):
        if runtime[key] != pins[pin]:
            raise ValueError('Product manifest/platform pins differ: ' + key)
    if runtime['chromium_version'] != '158.0.8127.36':
        raise ValueError('This reviewed product stack is limited to Chromium 158.0.8127.36')
    try:
        from .verify_windows158_product_port import load as load_port
    except ImportError:
        from verify_windows158_product_port import load as load_port
    port = load_port(repo)
    if (data.get('source_port_manifest_sha256') != digest(owned(repo, 'build/windows158-product-port.json').read_bytes())
            or data.get('chromium_base_sha256') != digest(owned(repo, 'build/ungoogled158-base.json').read_bytes())):
        raise ValueError('The full source port or ungoogled base policy changed')
    expected_runtime = json.loads(owned(repo, 'build/lite-windows157.json').read_bytes())['runtime']
    expected_runtime.update(chromium_version=pins['ChromiumVersion'], core_commit=pins['UngoogledCommit'],
                            windows_commit=pins['UngoogledWindowsCommit'])
    policy = json.loads(owned(repo, 'build/ungoogled158-base.json').read_bytes())
    if (runtime != expected_runtime or policy['skip_patches'] != {'core': [], 'windows': []}
            or policy['use_chromium_files'] or policy['preserve_resources'] or policy['preserve_pruned_sources']
            or [policy['layers'][k]['patch_count'] for k in ('core', 'windows')] != [110, 25]):
        raise ValueError('The full 157 feature contract and complete ungoogled layers must be retained')
    expected_patches = [{k: group[k] for k in ('path', 'sha256', 'files')} for group in port['groups']]
    adaptations = data.get('build_adaptations', {})
    donor_raw = owned(repo, 'build/checkpoint-sources/ungoogled158-donor-manifest.json').read_bytes()
    donor = json.loads(donor_raw)
    if (adaptations.get('donor_commit') != '02cb882b4924768730ebb7a17d333375ddfda69d'
            or adaptations.get('donor_manifest_sha256') != digest(donor_raw)
            or adaptations.get('source_paths') != ['build/toolchain/win/midl.py']
            or data['patches'][:-1] != expected_patches
            or data['patches'][-1]['path'] != 'patches/lite-windows158-build/01-windows-midl.patch'
            or data['patches'][-1]['files'] != adaptations['source_paths']):
        raise ValueError('The complete feature port or exact 158 build adaptation changed')
    declared = {row['path']: row for row in data['source_files']}
    for row in port['sourceFiles']:
        if declared.get(row['path']) != {'path': row['path'], 'before_sha256': row['before'], 'after_sha256': row['after']}:
            raise ValueError('A reviewed 157 feature changed during build wiring: ' + row['path'])
    port_names = {row['path'] for row in port['sourceFiles']}
    for row in data['source_files']:
        if row['path'] not in port_names | set(adaptations['source_paths']) and row['before_sha256'] != row['after_sha256']:
            raise ValueError('Unreviewed additional product source change: ' + row['path'])
    original_midl = next(row for row in donor['source_files'] if row['path'] == 'build/toolchain/win/midl.py')
    if declared['build/toolchain/win/midl.py']['after_sha256'] != original_midl['after_sha256']:
        raise ValueError('Windows MIDL repair differs from the compiled 158 donor')
    paths, targets = set(), set()
    for entry in data['patches']:
        name = entry['path']
        if (not re.fullmatch(r'patches/lite-windows(?:157|158|158-complete|158-build)/[0-9]{2}-[a-z0-9-]+\.patch', name)
                or name in paths or not entry['files']):
            raise ValueError('Invalid or duplicate product patch')
        paths.add(name)
        raw = owned(repo, name).read_bytes()
        if digest(raw) != entry['sha256']:
            raise ValueError('Changed product patch: ' + name)
        actual = re.findall(rb'^diff --git a/(\S+) b/\S+$', raw, re.M)
        actual = [name.decode('utf-8') for name in actual]
        if actual != entry['files'] or len(actual) != len(set(actual)) or targets.intersection(actual):
            raise ValueError('Product topics must own distinct, complete file sections')
        targets.update(actual)
    if 'base/uxr_private_font_packages_win.h' in targets:
        resources = runtime.get('private_font_packages', {})
        if (not isinstance(resources,dict) or resources.get('version') != 1 or resources.get('manifest') != 'build/private-font-packages.json'
                or resources.get('install_to_windows') is not False
                or resources.get('manifest_sha256') != digest(owned(repo, resources['manifest']).read_bytes())):
            raise ValueError('Private font resources differ from the pinned product contract')
        country = runtime.get('country_font_resources', {})
        if (country.get('version') != 3 or country.get('protocol') != 'signed-source-controlled-catalog-v3'
                or country.get('install_to_windows') is not False
                or country.get('trusted_key') != 'build/font-catalog-key.json'
                or country.get('trusted_key_sha256') != digest(owned(repo, country['trusted_key']).read_bytes())):
            raise ValueError('External font catalog trust anchor differs from the pinned product contract')
    actual_build = {p.relative_to(repo).as_posix() for p in owned(repo, 'patches/lite-windows158-build').glob('*.patch')}
    if actual_build != {'patches/lite-windows158-build/01-windows-midl.patch'}:
        raise ValueError('Unexpected Windows 158 build patch inventory')
    seen = set()
    for row in data['source_files']:
        owned(repo, row['path'])  # Validate paths even when not present in the repo.
        if row['path'] in seen:
            raise ValueError('Duplicate product source identity')
        seen.add(row['path'])
        for key in ('before_sha256', 'after_sha256'):
            if row[key] is not None and not HEX.fullmatch(row[key]):
                raise ValueError('Invalid product source digest')
    binaries = set(runtime['restore_pruned_resources'])
    changed = {row['path'] for row in data['source_files']
               if row['before_sha256'] != row['after_sha256']}
    if targets != changed - binaries or not set(data['domain_full_files']).issubset(seen):
        raise ValueError('Product source and patch inventories disagree')
    return data


def select(repo, *, src=None, core=None):
    repo = Path(repo).absolute()
    data = load(repo)
    runtime = data['runtime']
    if src is not None:
        fields = dict(re.findall(r'^(MAJOR|MINOR|BUILD|PATCH)=(\d+)$',
                                  owned(src, 'chrome/VERSION').read_text('ascii'), re.M))
        if '.'.join(fields.get(key, '') for key in ('MAJOR', 'MINOR', 'BUILD', 'PATCH')) != runtime['chromium_version']:
            raise ValueError('Product patches do not match the source version')
    if core is not None and owned(core, 'chromium_version.txt').read_text('ascii').strip() != runtime['chromium_version']:
        raise ValueError('Product patches do not match the ungoogled source version')
    patches = [(row['path'], owned(repo, row['path']).read_bytes()) for row in data['patches']]
    return {'series_sha256': data['stack_sha256'],
            'patches': [{'path': name, 'sha256': digest(raw)} for name, raw in patches],
            'selection': {'schema_version': 2, 'profile': 'lite', 'platform': 'windows',
                          'version': runtime['chromium_version'], 'arch': 'x64',
                          'core_commit': runtime['core_commit'], 'platform_commit': runtime['windows_commit'],
                          'manifest': MANIFEST, 'manifest_sha256': digest(owned(repo, MANIFEST).read_bytes())}}, patches


def input_hashes(repo):
    repo = Path(repo).absolute()
    data = load(repo)
    names = {MANIFEST, 'CHROMIUM_WINDOWS_VERSION', 'UNGOOGLED_WINDOWS_VERSION',
             'build/ungoogled-revisions.psd1', 'build/args.windows.gn', 'build/args.security.gn',
             'build/ungoogled158-base.json', 'build/windows158-product-port.json',
             'build/windows158-browser-feature-port.json', 'build/lite-windows157.json',
             'build/checkpoint-sources/ungoogled158-donor-manifest.json',
             'tools/verify_windows158_product_port.py', 'tools/verify_browser_feature_port.py',
             'tools/verify_browser_services158.py', 'tools/chromium158_base.py',
             'tools/archive_windows_files.py', 'tools/restoration_rules.py', 'build/chromium158-source.ini',
             'build/windows/build-crubit.ps1', 'build/windows/prep_rust_toolchain.py',
             'build/windows/prepare-ungoogled.ps1', 'tools/patch_selection.py',
             'build/windows/package-win.ps1',
             'tools/lite_patch_stack.py', 'tools/prepare_lite_product.py', 'tools/lite_product_migration.py',
             'tools/prepare_restored_build.py', 'tools/repair_windows_midl.py',
             'tools/install_windows_metagen_bindings.py',
             'tools/relocate_metagen_cache.py', 'tools/tests/test_metagen_cache_relocation.py',
             'tools/verify_native_browser_tests.py',
             'tools/tests/test_windows_font_cohorts.py',
             'tools/tests/test_windows_font_acceptance.mjs',
             'tools/tests/test_fingerprint_runtime_acceptance.mjs',
             'tools/tests/fixtures/windows-font-cohorts-v1.json',
             'tools/tests/fixtures/windows-font-packages-v2.json',
             'tools/tests/fixtures/windows-font-installations-v3.json',
             'tools/tests/fixtures/modern-windows-noto-v3.json',
             'tools/font_installation_profiles.mjs', 'tools/tests/test_font_installation_profiles.mjs',
             'tools/font_profile_selection_cli.mjs',
             'tools/capture_windows_font_environment.py', 'tools/capture_browser_font_installation.mjs',
             'tools/generate_windows_font_packages.py',
             'build/private-font-packages.json', 'build/font-catalog-key.json',
             'tools/signed_font_catalog_win.h.in',
             'tools/signed_font_catalog_v3_win.h.in', 'tools/catalog_native_view_win.h.in', 'tools/font_face_index_win.h',
             'tools/font_catalog_v3.mjs', 'tools/tests/test_font_catalog_v3.mjs',
             'tools/tests/test_signed_source_font_catalog.py',
             'tools/tests/test_signed_font_catalog.py', 'tools/tests/test_font_catalog.mjs',
             'tools/font_catalog.mjs',
             'tools/tests/test_country_font_resources.py', 'tools/private_font_packages_win.h.in',
             'tools/generate_private_font_packages.py', 'tools/stage_private_font_packages.py',
             'tools/tests/test_private_font_packages.py',
             'tools/verify_fingerprint_modes_runtime.mjs',
             'tools/fingerprint_font_acceptance.mjs',
             'tools/verify_google_services_candidate.py',
             'tools/native_compiled_evidence.py',
             'tools/prepare_google_services.py', 'tools/google_services_inputs.py',
             'tools/platform_pins.py', 'tools/restored_source_resources.py',
             'tools/apply_restored_patches.py', 'tools/verify_patch_stack.py',
             'tools/verify_google_services_source.py', 'tools/lite_retirement.py',
             'tools/verify_native_browser_source.py', 'tools/verify_native_environment_controls_source.py',
             'tools/build_lite_retirement_patch.py', 'tools/verify_history_retention_source.py',
             'tools/verify_translation_init_source.py', 'tools/verify_native_cookie_source.py',
             'build/native-browser-capabilities.json', 'build/native-cookie-capabilities.json',
             'build/fingerprint-modes-capabilities.json', 'build/gpu-binding-capabilities.json',
             'patches/lite-win156/retirement-manifest.json'}
    names.update(row['path'] for row in data['patches'])
    release = json.loads(owned(repo, 'build/lite-windows157.json').read_bytes())
    names.update(row['path'] for row in release['patches'])
    features = json.loads(owned(repo, 'build/windows158-browser-feature-port.json').read_bytes())
    names.update(group['path'] for group in features['groups'])
    names.add(features['featureConnections']['path'])
    names.update(p.relative_to(repo).as_posix() for p in owned(repo,'build/windows/metagen-python').rglob('*') if p.is_file())
    names.update(p.relative_to(repo).as_posix() for p in owned(repo,'tools/tests/fixtures/signed-font-catalog-v2').iterdir() if p.is_file())
    names.update(p.relative_to(repo).as_posix() for p in owned(repo,'tools/tests/fixtures/signed-font-catalog-v3').iterdir() if p.is_file())
    names.update('tools/' + name for name in (
        'webrtc_document_loopback_runtime.mjs', 'webrtc_document_loopback_probe.js',
        'webrtc_runtime_controller.mjs', 'webrtc_network_fixtures.mjs',
        'webrtc_document_loopback_acceptance.py', 'verify_webrtc_document_loopback_runtime.py',
        'build_webrtc_turn_fixture.py', 'webrtc-loopback-turn/main.go',
        'webrtc-loopback-turn/go.mod', 'webrtc-loopback-turn/go.sum'))
    names.update(row['repository_path'] for row in data['runtime']['restore_pruned_resources'].values())
    names.update('tools/tests/fixtures/pixel-semantics-v1/' + name
                 for name in ('cases.tsv', 'pixels.bin', 'provenance.json'))
    return {name: digest(owned(repo, name).read_bytes()) for name in sorted(names)}


def verify_source(repo, src, *, inverse_rules=(), phase='final'):
    """Check exact canonical file bytes; the known domain transform is reversible.

    Accept either raw or domain-substituted comments in donor trees, but never
    unrelated source edits. The preparation step restores executable domains.
    """
    return _verify_manifest_source(load(repo), src, inverse_rules=inverse_rules, phase=phase)


def _verify_manifest_source(data, src, *, inverse_rules=(), phase='final'):
    """Shared byte checker; callers must authenticate their expected manifest."""
    if phase not in ('before-domains', 'final'):
        raise ValueError('Unknown product source verification phase')
    full = set(data.get('domain_full_files', ()))
    for row in data['source_files']:
        path = owned(src, row['path'])
        if path.exists() and not path.is_file():
            raise ValueError('Product source path is not a regular file: ' + row['path'])
        raw = path.read_bytes().replace(b'\r\n', b'\n') if path.is_file() else None
        actual = digest(raw) if raw is not None else None
        if actual == row['after_sha256']:
            continue
        if raw is not None and inverse_rules:
            text = raw.decode('latin1')
            def inverse(value):
                for pattern, replacement in inverse_rules:
                    value = pattern.sub(replacement, value)
                return value
            if phase == 'before-domains':
                text = inverse(text)
            elif path.suffix in {'.cc', '.h', '.c', '.cpp', '.cxx', '.mm'} and row['path'] not in full:
                try:
                    from .prepare_google_services import CPP_TOKENS
                except ImportError:
                    from prepare_google_services import CPP_TOKENS
                text = CPP_TOKENS.sub(lambda token: inverse(token[0]) if token.group('comment') else token[0], text)
            if digest(text.encode('latin1')) == row['after_sha256']:
                continue
        raise ValueError('Product source differs from its reviewed final bytes: ' + row['path'])
    try:
        from .lite_retirement import REMOVED_FILES
    except ImportError:
        from lite_retirement import REMOVED_FILES
    for name in REMOVED_FILES:
        if owned(src, name).exists():
            raise ValueError('Retired product source still exists: ' + name)
    return [row['path'] for row in data['source_files']]
