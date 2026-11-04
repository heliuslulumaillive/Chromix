"""Finish or verify the explicit Lite product profile without replaying history."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    from . import lite_patch_stack as stack
    from .google_services_inputs import owned, write_if_changed
except ImportError:
    import lite_patch_stack as stack
    from google_services_inputs import owned, write_if_changed


def prepare(repo: Path, work: Path, *, migration=False):
    try:
        from .prepare_google_services import (pinned_inverse_rules, restore_domains,
            verify_prepared_sources, validate_fingerprint_identity)
        from .restored_source_resources import restore, verify
        from .patch_selection import preparation_key, source_version
    except ImportError:
        from prepare_google_services import (pinned_inverse_rules, restore_domains,
            verify_prepared_sources, validate_fingerprint_identity)
        from restored_source_resources import restore, verify
        from patch_selection import preparation_key, source_version
    data = stack.load(repo)
    config = data['runtime']
    src = owned(work, 'src')
    if source_version(src) != config['chromium_version']:
        raise ValueError('Product source version mismatch')
    marker = owned(work, 'google-services-source.json')
    blocker = owned(work, '.google-services-preparing')
    if blocker.exists():
        raise ValueError('Product preparation was interrupted; restore a verified checkpoint')
    saved = json.loads(marker.read_text('utf-8-sig')) if marker.is_file() else None
    inputs = stack.input_hashes(repo)
    recipe = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    rules_path = owned(work, 'tooling/ungoogled-chromium/domain_regex.list')
    rules = pinned_inverse_rules(rules_path)
    capabilities = json.loads(owned(repo, 'build/fingerprint-modes-capabilities.json').read_text('utf-8'))
    variant = validate_fingerprint_identity(config, capabilities)
    checks = {'require_security': True, 'require_lite_retirement': True, 'product': True}
    if migration:
        try:
            from .security_migration import registered_rule, check_receipt
        except ImportError:
            from security_migration import registered_rule, check_receipt
        origin = json.loads(owned(work, '.security-origin/checkpoint.json').read_text('utf-8-sig'))
        transition = json.loads(owned(work, 'security-migration.json').read_text('utf-8'))
        rule = registered_rule(repo, origin)
        if (saved is None or transition.get('status') != 'source-admitted'
                or hashlib.sha256(marker.read_bytes()).hexdigest() != origin['source_receipt_sha256']
                or transition.get('policy_sha256') != stack.digest(owned(repo, 'build/checkpoint-migrations.json').read_bytes())):
            raise ValueError('Product migration requires the exact admitted donor receipt')
        check_receipt(saved, rule, 'source')
        expected_source = data['stack_sha256'] if saved.get('patch_kind') == stack.KIND else data['legacy_equivalence']['aggregate_sha256']
        if saved['patch_sha256'] != expected_source:
            raise ValueError('Only a reviewed byte-equivalent source can migrate without source writes')
        # Check every final file before changing even a resource or receipt.
        stack.verify_source(repo, src, inverse_rules=rules)
        verify(src, repo, config)
    elif saved:
        if (saved.get('patch_kind') != stack.KIND or saved.get('patch_sha256') != data['stack_sha256']
                or saved.get('chromium_version') != config['chromium_version']
                or saved.get('variant') != 'google-services-self-use' or saved.get('build_variant') != variant):
            raise ValueError('Existing source needs an explicitly registered product migration')
        stack.verify_source(repo, src, inverse_rules=rules)
        verify(src, repo, config)
        verify_prepared_sources(src, repo, config, **checks)
        if saved.get('preparation_inputs') == inputs and saved.get('preparation_sha256') == recipe:
            return saved
    else:
        expected = preparation_key(repo, 'windows', 'windows', profile='lite')
        if owned(src, '.chromix-patches').read_text('ascii').strip() != expected:
            raise ValueError('Fresh product preparation requires the complete selected Lite patch stack')
    write_if_changed(blocker, data['stack_sha256'].encode())
    restored = restore(src, repo, config)
    stack.verify_source(repo, src, inverse_rules=rules, phase='before-domains' if not saved else 'final')
    # A registered equivalent donor has already restored domains. Preserve all
    # bytes and mtimes; ordinary fresh sources still scan the pinned full list.
    domains = {'strategy': 'verified-equivalent-donor', 'files_changed': 0}
    if not migration:
        domains = restore_domains(src, None, rules_path,
            owned(work, 'tooling/ungoogled-chromium-windows/domain_substitution.list'),
            data['domain_full_files'], pinned=True)
    stack.verify_source(repo, src, inverse_rules=rules)
    checked = verify_prepared_sources(src, repo, config, **checks)
    if stack.input_hashes(repo) != inputs:
        raise ValueError('Product inputs changed during preparation')
    if migration:
        # Re-identify layers only after the donor and every final source byte
        # were verified. No source/object file is rewritten during compaction.
        key = preparation_key(repo, 'windows', 'windows', profile='lite')
        for name in ('.chromix-patches', '.chromix-patch-selection'):
            write_if_changed(owned(src, name), (key + '\n').encode())
        ready = '|'.join((config['chromium_version'], config['core_commit'], config['windows_commit'], key))
        write_if_changed(owned(src, '.chromix-source-ready'), (ready + '\n').encode())
    report = {'variant': 'google-services-self-use', 'build_variant': variant,
              'chromium_version': config['chromium_version'], 'patch_kind': stack.KIND,
              'patch_sha256': data['stack_sha256'], 'preparation_sha256': recipe,
              'preparation_inputs': inputs, 'source_checks': checked,
              'security_restoration': config['security_restoration'],
              'restored_pruned_resources': restored, 'domain_restoration': domains,
              'unchanged_source_timestamps_preserved': len(data['source_files']) if migration else 0,
              'google_account_and_cloud_access': 'not_tested', 'credentials': 'runtime_environment_only'}
    write_if_changed(marker, (json.dumps(report, indent=2) + '\n').encode())
    blocker.unlink()
    return report
