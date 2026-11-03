"""Explicit same-version checkpoint transitions; never relax ordinary resume."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

try:
    from .security_build_record import VERSION, CORE, WINDOWS, plain, recipe, sha256
except ImportError:
    from security_build_record import VERSION, CORE, WINDOWS, plain, recipe, sha256

POLICY = 'build/checkpoint-migrations.json'
HEX = re.compile(r'[a-f0-9]{64}\Z')


def write_json(path: Path, value):
    plain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, sort_keys=True, indent=2) + '\n'
    temporary = path.with_name(path.name + '.tmp')
    plain(temporary)
    temporary.write_text(content, encoding='utf-8', newline='\n')
    temporary.replace(path)


def registered_rule(repo: Path, manifest: dict) -> dict:
    policy = json.loads(plain(repo / POLICY).read_text(encoding='utf-8'))
    if policy.get('schema_version') != 1 or not isinstance(policy.get('migrations'), list):
        raise ValueError('Invalid checkpoint migration registry')
    target = recipe(repo)
    matches = []
    for entry in policy['migrations']:
        if (entry.get('source_commit') == manifest.get('source_commit')
                and entry.get('source_recipe_sha256') == manifest.get('recipe_sha256')
                and entry.get('target_recipe_sha256') == target):
            matches.append(entry)
    if len(matches) != 1:
        raise ValueError('No unique reviewed transition from this donor to the current source recipe')
    rule = matches[0]
    for key, expected in [('chromium_version', VERSION), ('core_commit', CORE),
                          ('windows_commit', WINDOWS), ('arch', 'x64')]:
        if rule.get(key) != expected or manifest.get(key) != expected:
            raise ValueError('Migration cannot change the Chromium/platform identity: ' + key)
    for key in ('source_recipe_sha256', 'target_recipe_sha256',
                'source_preparation_sha256', 'target_preparation_sha256',
                'source_aggregate_sha256', 'target_aggregate_sha256'):
        if not HEX.fullmatch(str(rule.get(key, ''))) or rule[key] == '0' * 64:
            raise ValueError('Migration recipe has not been finalized: ' + key)
    if rule['source_recipe_sha256'] == rule['target_recipe_sha256']:
        raise ValueError('Identical payloads must use ordinary resume')
    history = repo / 'patches/google-services/history' / (rule['source_aggregate_sha256'] + '.patch')
    if sha256(plain(history)) != rule['source_aggregate_sha256']:
        raise ValueError('Exact donor source patch is unavailable for the reviewed transition')
    aggregate = repo / 'patches/google-services/0001-restore-google-services.patch'
    if sha256(plain(aggregate)) != rule['target_aggregate_sha256']:
        raise ValueError('Target aggregate differs from the reviewed transition')
    return rule


def check_receipt(receipt, rule, side):
    if (receipt.get('variant') != 'google-services-self-use'
            or receipt.get('build_variant') != 'fingerprint-modes-win156'
            or receipt.get('chromium_version') != VERSION
            or receipt.get('patch_sha256') != rule[side + '_aggregate_sha256']
            or receipt.get('preparation_sha256') != rule[side + '_preparation_sha256']):
        raise ValueError('Prepared source does not match the registered ' + side + ' recipe')


def verify_source(repo, work, manifest_path):
    manifest = json.loads(plain(manifest_path).read_text(encoding='utf-8-sig'))
    rule = registered_rule(repo, manifest)
    path = plain(work / 'google-services-source.json')
    if sha256(path) != manifest['source_receipt_sha256']:
        raise ValueError('Donor source receipt changed after checkpoint validation')
    receipt = json.loads(path.read_text(encoding='utf-8-sig'))
    check_receipt(receipt, rule, 'source')
    origin = work / '.security-origin'
    plain(origin)
    origin.mkdir(exist_ok=True)
    write_json(origin / 'checkpoint.json', manifest)
    original_receipt = plain(origin / 'source-receipt.json')
    original_receipt.write_bytes(path.read_bytes())
    record = {'schema_version': 1, 'status': 'source-admitted',
              'migration_id': rule['id'], 'donor_run': manifest['run_id'],
              'donor_stage': manifest['stage'], 'source_commit': manifest['source_commit'],
              'source_recipe_sha256': rule['source_recipe_sha256'],
              'target_recipe_sha256': rule['target_recipe_sha256'],
              'policy_sha256': sha256(repo / POLICY),
              'original_gn_sha256': manifest['gn_args_sha256'],
              'objects_preserved': True, 'native_acceptance': 'not_tested'}
    write_json(work / 'security-migration.json', record)
    return record


def verify_target(repo, work):
    manifest = json.loads(plain(work / '.security-origin/checkpoint.json').read_text())
    rule = registered_rule(repo, manifest)
    path = plain(work / 'security-migration.json')
    record = json.loads(path.read_text())
    if (record.get('status') not in ('source-admitted', 'source-transition-complete')
            or record.get('policy_sha256') != sha256(repo / POLICY)
            or record.get('target_recipe_sha256') != recipe(repo)):
        raise ValueError('The admitted migration changed during preparation')
    receipt_path = plain(work / 'google-services-source.json')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
    check_receipt(receipt, rule, 'target')
    if (work / '.google-services-preparing').exists():
        raise ValueError('Source transition is incomplete')
    record.update(status='source-transition-complete',
                  target_source_receipt_sha256=sha256(receipt_path),
                  unchanged_source_timestamps_preserved=receipt.get('unchanged_source_timestamps_preserved', 0))
    write_json(path, record)
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['verify-source', 'verify-target'])
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--manifest', type=Path)
    a = p.parse_args()
    if a.operation == 'verify-source':
        if not a.manifest:
            p.error('verify-source requires --manifest')
        result = verify_source(a.repo, a.work, a.manifest)
    else:
        result = verify_target(a.repo, a.work)
    print(json.dumps({'status': result['status'], 'native_acceptance': 'not_tested'}))


if __name__ == '__main__':
    main()
