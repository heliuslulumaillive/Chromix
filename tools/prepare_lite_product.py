"""Verify and record the native Chromium 158 product after the selected patches."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    from . import lite_patch_stack as stack
    from .google_services_inputs import owned, write_if_changed
    from .patch_selection import preparation_key, source_version
    from .prepare_google_services import verify_prepared_sources, validate_fingerprint_identity
except ImportError:
    import lite_patch_stack as stack
    from google_services_inputs import owned, write_if_changed
    from patch_selection import preparation_key, source_version
    from prepare_google_services import verify_prepared_sources, validate_fingerprint_identity


def prepare(repo: Path, work: Path, *, migration=False):
    if migration:
        raise ValueError('The native 158 lane accepts fresh or identical-recipe sources')
    data = stack.load(repo)
    config = data['runtime']
    src = owned(work, 'src')
    if source_version(src) != config['chromium_version']:
        raise ValueError('Product source version mismatch')
    marker = owned(work, 'google-services-source.json')
    if owned(work, '.google-services-preparing').exists():
        raise ValueError('Product preparation was interrupted')
    previous = json.loads(marker.read_text('utf-8-sig')) if marker.is_file() else None
    if previous is not None and (previous.get('variant') != 'google-services-self-use'
                                 or previous.get('build_variant') != 'fingerprint-modes-win158'
                                 or previous.get('patch_sha256') != data['stack_sha256']
                                 or previous.get('patch_kind') != stack.KIND
                                 or previous.get('chromium_version') != config['chromium_version']):
        raise ValueError('Existing receipt belongs to a different native Chromium source or recipe')
    expected = preparation_key(repo, 'windows', 'windows', profile='lite')
    if any(owned(src, name).exists() for name in ('.chromix-upstream-restored.json', '.chromix-domain-substituted')):
        raise ValueError('Expected original Chromium source with the native 158 policy')
    if any(owned(src, name).read_text('ascii').strip() != expected for name in
           ('.chromix-patches', '.chromix-native-layer-key')):
        raise ValueError('Product source does not have the reviewed 158 patch identity')
    inputs = stack.input_hashes(repo)
    recipe = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    caps = json.loads(owned(repo, 'build/fingerprint-modes-capabilities.json').read_bytes())
    variant = validate_fingerprint_identity(config, caps)
    stack.verify_source(repo, src)
    checked = verify_prepared_sources(src, repo, config, product=True,
                                      require_security=True, require_lite_retirement=True)
    report = {'variant': 'google-services-self-use', 'build_variant': variant,
              'chromium_version': config['chromium_version'], 'patch_kind': stack.KIND,
              'patch_sha256': data['stack_sha256'], 'preparation_sha256': recipe,
              'preparation_inputs': inputs, 'source_checks': checked,
              'security_restoration': config['security_restoration'],
              'restored_pruned_resources': [],
              'domain_restoration': {'strategy': 'native-chromium-domains', 'files_changed': 0},
              'google_account_and_cloud_access': 'not_tested', 'credentials': 'runtime_environment_only'}
    if previous is not None:
        if previous != report:
            raise ValueError('Existing receipt belongs to a different native Chromium source or recipe')
        return report
    if stack.input_hashes(repo) != inputs:
        raise ValueError('Product inputs changed during preparation')
    write_if_changed(marker, (json.dumps(report, indent=2)+'\n').encode())
    return report
