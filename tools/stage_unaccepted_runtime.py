"""Preserve an immutable failed-verification runtime without granting acceptance."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import zipfile

try:
    from .security_build_record import VERSION, plain, recipe, sha256
except ImportError:
    from security_build_record import VERSION, plain, recipe, sha256

COMPLETED = ['Acquire', 'Extract', 'Prepare', 'Configure', 'Compile', 'Bundle']


def object_file(path):
    value = json.loads(plain(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Diagnostic input must be a JSON object')
    return value


def member(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or PurePosixPath(name).is_absolute()
            or any(part in ('', '.', '..') for part in name.split('/'))
            or any(ord(c) < 32 for c in name)):
        raise ValueError('Unsafe runtime inventory member')
    return name


def validate_snapshot(repo, bundle, evidence, state, run_id, source_commit):
    if (state.get('schema_version') != 2 or state.get('status') != 'failed'
            or state.get('phase') != 'Verify' or state.get('finished') is not False
            or state.get('completed') != COMPLETED
            or str(state.get('run_id')) != str(run_id)
            or state.get('source_commit') != source_commit
            or type(state.get('stage')) is not int or not 1 <= state['stage'] <= 99):
        raise ValueError('Unaccepted runtime requires this run to have completed Bundle and failed Verify')
    record_path = plain(bundle / 'google-services-build.json')
    if sha256(record_path) != state.get('build_hash'):
        raise ValueError('Pre-verification build record changed; refusing diagnostic runtime archive')
    record = object_file(record_path)
    if (record.get('schema_version') != 1
            or str(record.get('run_id')) != str(run_id) or record.get('source_commit') != source_commit
            or record.get('build_variant') != 'fingerprint-modes-win156'
            or record.get('chromium_version') != VERSION):
        raise ValueError('Staged runtime belongs to another build')
    files = record.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('Staged runtime has no immutable file inventory')
    expected = {member(name): digest for name, digest in files.items()}
    if not {'chrome.exe', 'chrome.dll', 'google-services-source.json'} <= expected.keys():
        raise ValueError('Diagnostic runtime is incomplete')
    actual = {}
    for path in plain(bundle).rglob('*'):
        plain(path)
        if path.is_file() and path != record_path:
            actual[path.relative_to(bundle).as_posix()] = sha256(path)
    if actual != expected:
        raise ValueError('Runtime changed during failed verification; inventory was not rebaselined')
    source_path = plain(evidence / 'build-inputs.json')
    source = object_file(source_path)
    if (source.get('schema_version') != 1
            or str(source.get('run_id')) != str(run_id) or source.get('source_commit') != source_commit
            or source.get('build_variant') != 'security-win156'
            or source.get('chromium_version') != VERSION
            or source.get('recipe_sha256') != recipe(repo)
            or source.get('source_receipt_sha256') != actual.get('google-services-source.json')):
        raise ValueError('Runtime/source evidence identity differs')
    expected['google-services-build.json'] = state['build_hash']
    return expected, source


def stage(repo, bundle, evidence, state_path, output, *, run_id, source_commit):
    repo, bundle, evidence, output = map(plain, (repo, bundle, evidence, output))
    state = object_file(state_path)
    expected, source = validate_snapshot(repo, bundle, evidence, state, run_id, source_commit)
    if output.exists():
        raise ValueError('Use a fresh diagnostic output; never overwrite prior evidence')
    output.mkdir(parents=True)
    archive_path = output / 'unaccepted-runtime.zip'
    copied_receipts = {}
    try:
        with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED,
                             compresslevel=1, allowZip64=True) as archive:
            for name, expected_hash in sorted(expected.items()):
                path = plain(bundle / name)
                digest = hashlib.sha256()
                with path.open('rb') as source_file, archive.open('chromix/' + name, 'w', force_zip64=True) as destination:
                    for block in iter(lambda: source_file.read(1024 * 1024), b''):
                        digest.update(block)
                        destination.write(block)
                if digest.hexdigest() != expected_hash:
                    raise ValueError('Runtime changed while archiving; diagnostic payload refused')
            # Top-level receipts/logs only. Never include temporary browser profile
            # directories or alter the immutable chromix/ inventory with diagnostics.
            for path in sorted(evidence.iterdir()):
                plain(path)
                if not path.is_file() or path.suffix.lower() not in ('.json', '.log', '.xml', '.txt'):
                    continue
                name = member(path.name)
                data = path.read_bytes()
                archive.writestr('evidence/' + name, data)
                copied_receipts[name] = hashlib.sha256(data).hexdigest()
            receipt = (bundle / 'google-services-source.json').read_bytes()
            if 'google-services-source.json' in copied_receipts:
                if copied_receipts['google-services-source.json'] != hashlib.sha256(receipt).hexdigest():
                    raise ValueError('Outer source receipt differs from the immutable bundle')
            else:
                archive.writestr('evidence/google-services-source.json', receipt)
            copied_receipts['google-services-source.json'] = hashlib.sha256(receipt).hexdigest()
        # Check again after streaming, including additions/removals and source identity.
        after, _ = validate_snapshot(repo, bundle, evidence, state, run_id, source_commit)
        if after != expected:
            raise ValueError('Runtime identity changed while preparing diagnostics')
        result = {'schema_version': 1, 'accepted': False, 'not_for_activation': True,
                  'status': 'verify-phase-failed', 'qualification': 'Diagnostic runtime only; no failed gate is waived.',
                  'repository': os.environ.get('GITHUB_REPOSITORY', ''), 'run_id': str(run_id),
                  'source_commit': source_commit, 'stage': state['stage'],
                  'recipe_sha256': source['recipe_sha256'],
                  'source_receipt_sha256': source['source_receipt_sha256'],
                  'pre_verification_build_record_sha256': state['build_hash'],
                  'runtime_inventory': expected, 'receipts': copied_receipts,
                  'archive': {'name': archive_path.name, 'bytes': archive_path.stat().st_size,
                              'sha256': sha256(archive_path)}}
        (output / 'diagnostic.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        return result
    except BaseException:
        # This file was created exclusively inside the new owned output above.
        archive_path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'bundle', 'evidence', 'state', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = stage(args.repo, args.bundle, args.evidence, args.state, args.output,
                   run_id=os.environ['GITHUB_RUN_ID'], source_commit=os.environ['GITHUB_SHA'])
    print(json.dumps({'accepted': False, 'status': result['status'],
                      'archive_sha256': result['archive']['sha256']}))


if __name__ == '__main__':
    main()
