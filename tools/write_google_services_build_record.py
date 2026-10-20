#!/usr/bin/env python3
"""Bind a Google-service candidate to its source recipe and immutable file set."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--gn-args', type=Path, required=True)
    args = parser.parse_args()
    source = json.loads((args.work / 'google-services-source.json').read_text(encoding='utf-8-sig'))
    if source.get('variant') != 'google-services-self-use':
        raise ValueError('Expected a prepared Google-service source receipt')
    commit = os.environ.get('GITHUB_SHA', '')
    run = os.environ.get('GITHUB_RUN_ID', '')
    if not re.fullmatch('[0-9a-f]{40}', commit) or not re.fullmatch('[0-9]+', run):
        raise ValueError('Build commit and Actions run are required for a candidate receipt')
    previous = args.work / 'google-services-checkpoint-source.json'
    record = {
        'schema_version': 1,
        'repository': os.environ['GITHUB_REPOSITORY'],
        'source_commit': commit,
        'run_id': int(run),
        'run_attempt': int(os.environ.get('GITHUB_RUN_ATTEMPT', '1')),
        'chromium_version': source['chromium_version'],
        'patch_sha256': source['patch_sha256'],
        'preparation_sha256': source['preparation_sha256'],
        'checkpoint_source': json.loads(previous.read_text(encoding='utf-8-sig')) if previous.exists() else None,
        # Never include args.gn contents or process credentials in an artifact.
        'gn_args_sha256': sha256(args.gn_args),
        'google_account_and_cloud_access': 'not_tested',
        'files': {},
    }
    if 'build_variant' in source:
        if source['build_variant'] != 'fingerprint-modes-win155':
            raise ValueError('Unsupported explicit build variant')
        record['build_variant'] = source['build_variant']
    destination = args.bundle / 'google-services-build.json'
    for path in sorted(args.bundle.rglob('*')):
        if path.is_file() and path != destination:
            record['files'][path.relative_to(args.bundle).as_posix()] = sha256(path)
    destination.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
