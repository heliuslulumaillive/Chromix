#!/usr/bin/env python3
"""Bind a Google-service candidate to its source recipe and immutable file set."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

FINGERPRINT_VERSIONS = {
    'fingerprint-modes-win155': '155.0.8059.39',
    'fingerprint-modes-win157': '157.0.8103.38',
}


def validate_source_pair(source):
    if not isinstance(source, dict) or source.get('variant') != 'google-services-self-use':
        raise ValueError('Expected a prepared Google-service source receipt')
    build_variant = source.get('build_variant')
    if 'build_variant' in source and (not isinstance(build_variant, str) or build_variant not in FINGERPRINT_VERSIONS):
        raise ValueError('Unsupported explicit build variant')
    expected = FINGERPRINT_VERSIONS.get(build_variant, '155.0.8059.39')
    if source.get('chromium_version') != expected:
        raise ValueError('Source build variant and Chromium version do not match')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def native_test_identity(gn_args):
    """The focused test executable must come from the same selected output."""
    args = gn_args.absolute()
    executable = args.with_name('native_passkey_unittests.exe')
    if args.name != 'args.gn':
        raise ValueError('Windows 156 packaging requires the selected Out/args.gn')
    for path in (args, executable):
        if (not path.is_file() or any(item.is_symlink() or
                (hasattr(item, 'is_junction') and item.is_junction())
                for item in (path, *path.parents))):
            raise ValueError('Windows 156 packaging requires plain GN and native test files in the selected output')
    before = executable.stat()
    if before.st_size <= 0:
        raise ValueError('Compiled native test executable is empty')
    digest = sha256(executable)
    after = executable.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('Compiled native test executable changed during packaging')
    return {'name': executable.name, 'sha256': digest, 'bytes': before.st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--gn-args', type=Path, required=True)
    args = parser.parse_args()
    source = json.loads((args.work / 'google-services-source.json').read_text(encoding='utf-8-sig'))
    validate_source_pair(source)
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
        record['build_variant'] = source['build_variant']
    if 'patch_kind' in source:
        record['patch_kind'] = source['patch_kind']
    if source.get('build_variant') == 'fingerprint-modes-win157':
        record['native_test_executable'] = native_test_identity(args.gn_args)
    destination = args.bundle / 'google-services-build.json'
    for path in sorted(args.bundle.rglob('*')):
        if path.is_file() and path != destination:
            record['files'][path.relative_to(args.bundle).as_posix()] = sha256(path)
    if source.get('build_variant') == 'fingerprint-modes-win157':
        record['created_at_utc'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    destination.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
