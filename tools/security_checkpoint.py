"""Strict Windows 156 checkpoint manifests and content-verified restoration."""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

try:
    from .security_build_record import VERSION, VARIANT, CORE, WINDOWS, plain, recipe, sha256
except ImportError:
    from security_build_record import VERSION, VARIANT, CORE, WINDOWS, plain, recipe, sha256

IDENTITY = {'schema_version': 1, 'build_variant': VARIANT, 'chromium_version': VERSION,
            'core_commit': CORE, 'windows_commit': WINDOWS, 'arch': 'x64'}
HEX = re.compile(r'[a-f0-9]{64}\Z')


def gh(args):
    result = subprocess.run(['gh', *args], capture_output=True, text=True,
                            encoding='utf-8', timeout=3600,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('GitHub checkpoint operation failed; credentials and signed URLs omitted')
    return result.stdout


def validate_manifest(manifest, *, source_commit, repository, source_run, expected_recipe):
    for key, expected in IDENTITY.items():
        if manifest.get(key) != expected:
            raise ValueError('Checkpoint identity mismatch: ' + key)
    if (manifest.get('repository') != repository or str(manifest.get('run_id')) != str(source_run)
            or manifest.get('source_commit') != source_commit
            or not re.fullmatch(r'[a-f0-9]{40}', source_commit)
            or manifest.get('recipe_sha256') != expected_recipe):
        raise ValueError('Checkpoint source/run/recipe mismatch; cross-version or unreviewed migration refused')
    if manifest.get('resume_safe') is not True:
        raise ValueError('Partial failed preparation was retained for diagnosis, not admitted for resume')
    for key in ('gn_args_sha256', 'source_receipt_sha256'):
        if not HEX.fullmatch(str(manifest.get(key, ''))):
            raise ValueError('Missing prepared-source identity: ' + key)
    parts = manifest.get('parts')
    if not isinstance(parts, list) or not 1 <= len(parts) <= 4:
        raise ValueError('Checkpoint must contain one to four volumes')
    for index, part in enumerate(parts, 1):
        if (part.get('name') != f'tree.7z.{index:03d}'
                or type(part.get('bytes')) is not int or not 0 < part['bytes'] <= 8 * 1024**3
                or not HEX.fullmatch(str(part.get('sha256', '')))):
            raise ValueError('Invalid or incomplete checkpoint volume identity')
    if type(manifest.get('stage')) is not int or not 1 <= manifest['stage'] <= 99:
        raise ValueError('Invalid checkpoint stage')
    if 'tree_bytes' in manifest and (type(manifest['tree_bytes']) is not int
                                     or not 0 < manifest['tree_bytes'] <= 1024**4):
        raise ValueError('Invalid uncompressed checkpoint size')
    return parts


def verify_volume(path, part):
    plain(path)
    if not path.is_file() or path.stat().st_size != part['bytes'] or sha256(path) != part['sha256']:
        raise ValueError('Checkpoint volume content mismatch: ' + part['name'])


def make_manifest(repo, work, parts, stage, output, resume_safe, tree_bytes=None):
    volumes = sorted(parts.glob('tree.7z.*'))
    if not 1 <= len(volumes) <= 4:
        raise ValueError('Invalid checkpoint volume count')
    entries = []
    for index, p in enumerate(volumes, 1):
        plain(p)
        if p.name != f'tree.7z.{index:03d}' or not 0 < p.stat().st_size <= 8 * 1024**3:
            raise ValueError('Invalid checkpoint volume')
        entries.append({'name': p.name, 'bytes': p.stat().st_size, 'sha256': sha256(p)})
    gn = work / 'src/out/Default/args.gn'
    receipt = work / 'google-services-source.json'
    manifest = {**IDENTITY, 'repository': os.environ['GITHUB_REPOSITORY'],
                'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
                'source_commit': os.environ['GITHUB_SHA'], 'stage': stage,
                'recipe_sha256': recipe(repo), 'resume_safe': resume_safe,
                'gn_args_sha256': sha256(gn) if gn.is_file() else None,
                'source_receipt_sha256': sha256(receipt) if receipt.is_file() else None,
                'parts': entries}
    if tree_bytes is not None:
        manifest['tree_bytes'] = tree_bytes
    if resume_safe:
        validate_manifest(manifest, source_commit=manifest['source_commit'],
                          repository=manifest['repository'], source_run=manifest['run_id'],
                          expected_recipe=recipe(repo))
    output.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


def artifact_identity(artifact):
    if (type(artifact.get('id')) is not int or artifact['id'] <= 0
            or artifact.get('expired') is not False
            or type(artifact.get('size_in_bytes')) is not int or artifact['size_in_bytes'] <= 0):
        raise ValueError('Expired or invalid checkpoint artifact')
    return {key: artifact.get(key) for key in ('id', 'name', 'size_in_bytes', 'digest', 'created_at')}


def artifact_inventory(base):
    result = []
    for page in range(1, 21):
        response = json.loads(gh(['api', base + f'/artifacts?per_page=100&page={page}']))
        result.extend(response['artifacts'])
        if len(response['artifacts']) < 100:
            return result
    raise ValueError('Unexpectedly large checkpoint artifact inventory')


def validated_parts(repo, manifest, run, repository, source_run, allow_migration):
    expected = recipe(repo)
    if allow_migration and manifest.get('recipe_sha256') == expected:
        raise ValueError('Identical source recipes must use resume, not upgrade')
    if manifest.get('recipe_sha256') != expected:
        if not allow_migration:
            raise ValueError('Checkpoint source recipe differs; use an explicitly reviewed upgrade')
        try:
            from .security_migration import registered_rule
        except ImportError:
            from security_migration import registered_rule
        rule = registered_rule(repo, manifest)
        expected = rule['source_recipe_sha256']
    parts = validate_manifest(manifest, source_commit=run['head_sha'], repository=repository,
                              source_run=source_run, expected_recipe=expected)
    if str(manifest.get('run_attempt')) != str(run.get('run_attempt')):
        raise ValueError('Checkpoint belongs to a different run attempt')
    return parts


def read_small_manifest(repository, source_run, name):
    with tempfile.TemporaryDirectory(prefix='security-manifest-') as temporary:
        directory = Path(temporary)
        gh(['run', 'download', source_run, '--repo', repository, '--name', name,
            '--dir', str(directory)])
        if {p.name for p in directory.iterdir()} != {'checkpoint.json'}:
            raise ValueError('Unexpected checkpoint manifest inventory')
        path = plain(directory / 'checkpoint.json')
        if path.stat().st_size > 65536:
            raise ValueError('Oversized checkpoint manifest')
        return json.loads(path.read_text(encoding='utf-8-sig'))


def preflight(repo, source_run, current_stage, source_stage=0, allow_migration=False):
    """Read the tiny manifest and reject mismatches before downloading any volumes."""
    repository = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid repository')
    if not re.fullmatch(r'[1-9][0-9]*', source_run):
        raise ValueError('Numeric source run required')
    base = f'repos/{repository}/actions/runs/{source_run}'
    run = json.loads(gh(['api', base]))
    if str(run.get('id')) != source_run:
        raise ValueError('Run identity mismatch')
    artifacts = artifact_inventory(base)
    pattern = re.compile(re.escape(VARIANT) + r'-checkpoint-s([1-9][0-9]*)\Z')
    candidates = [(int(m[1]), a) for a in artifacts
                  if not a.get('expired') and (m := pattern.fullmatch(a['name']))
                  and (not source_stage or int(m[1]) == source_stage)
                  and (source_run != os.environ.get('GITHUB_RUN_ID') or int(m[1]) < current_stage)]
    if not candidates:
        raise ValueError('No complete preceding Windows 156 checkpoint; never substitute a 155 donor')
    stage = max(n for n, _ in candidates)
    selected = [a for n, a in candidates if n == stage]
    if len(selected) != 1:
        raise ValueError('Ambiguous checkpoint manifest')
    manifest = read_small_manifest(repository, source_run, selected[0]['name'])
    parts = validated_parts(repo, manifest, run, repository, source_run, allow_migration)
    if manifest['stage'] != stage:
        raise ValueError('Manifest artifact/stage mismatch')
    volumes = []
    for index, part in enumerate(parts, 1):
        name = f'{VARIANT}-s{stage}-part{index}'
        matching = [a for a in artifacts if a['name'] == name and not a.get('expired')]
        if len(matching) != 1:
            raise ValueError('Missing or ambiguous checkpoint volume: ' + name)
        volumes.append(artifact_identity(matching[0]))
    return {'schema_version': 1, 'repository': repository, 'source_run': source_run,
            'source_commit': run['head_sha'], 'run_attempt': run['run_attempt'],
            'stage': stage, 'manifest': manifest, 'manifest_artifact': artifact_identity(selected[0]),
            'volume_artifacts': volumes, 'target_recipe_sha256': recipe(repo),
            'allow_migration': allow_migration}


def validate_preflight(repo, record, source_run, current_stage, source_stage, allow_migration,
                       *, check_manifest=True):
    repository = os.environ['GITHUB_REPOSITORY']
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
            or not re.fullmatch(r'[1-9][0-9]*', source_run)):
        raise ValueError('Invalid donor repository/run')
    if (record.get('schema_version') != 1 or record.get('repository') != repository
            or record.get('source_run') != source_run
            or record.get('allow_migration') is not allow_migration
            or record.get('target_recipe_sha256') != recipe(repo)):
        raise ValueError('Checkpoint preflight does not belong to this request/recipe')
    stage = record.get('stage')
    if (type(stage) is not int or not 1 <= stage <= 99
            or source_stage and stage != source_stage
            or source_run == os.environ.get('GITHUB_RUN_ID') and stage >= current_stage):
        raise ValueError('Checkpoint stage changed after preflight')
    base = f'repos/{repository}/actions/runs/{source_run}'
    run = json.loads(gh(['api', base]))
    if (str(run.get('id')) != source_run or run.get('head_sha') != record.get('source_commit')
            or run.get('run_attempt') != record.get('run_attempt')):
        raise ValueError('Donor run changed after preflight')
    parts = validated_parts(repo, record['manifest'], run, repository, source_run, allow_migration)
    if record['manifest'].get('stage') != stage or len(record.get('volume_artifacts', [])) != len(parts):
        raise ValueError('Preflight volume/stage inventory mismatch')
    if record.get('manifest_artifact', {}).get('name') != f'{VARIANT}-checkpoint-s{stage}':
        raise ValueError('Unexpected manifest artifact name')
    for index, entry in enumerate(record['volume_artifacts'], 1):
        if entry.get('name') != f'{VARIANT}-s{stage}-part{index}':
            raise ValueError('Unexpected volume artifact name/order')
    artifacts = artifact_inventory(base)
    for expected in [record['manifest_artifact'], *record['volume_artifacts']]:
        matches = [a for a in artifacts if a['name'] == expected.get('name') and not a.get('expired')]
        if len(matches) != 1 or artifact_identity(matches[0]) != expected:
            raise ValueError('Donor artifact replaced/removed after preflight')
    if check_manifest and read_small_manifest(repository, source_run,
                                              record['manifest_artifact']['name']) != record['manifest']:
        raise ValueError('Local preflight manifest differs from the authenticated donor artifact')
    return parts


def restore(repo, destination, source_run, current_stage, source_stage=0,
            allow_migration=False, preflight_file=None):
    record = (json.loads(plain(preflight_file).read_text(encoding='utf-8')) if preflight_file
              else preflight(repo, source_run, current_stage, source_stage, allow_migration))
    parts = validate_preflight(repo, record, source_run, current_stage, source_stage, allow_migration)
    manifest = record['manifest']
    repository = record['repository']
    stage = record['stage']
    plain(destination)
    destination.mkdir(parents=True, exist_ok=True)
    owner = destination / '.security-transfer.json'
    identity = json.dumps(record, sort_keys=True, separators=(',', ':'))
    if owner.exists():
        if plain(owner).read_text(encoding='utf-8') != identity:
            raise ValueError('Transfer directory belongs to another checkpoint')
    elif any(destination.iterdir()):
        raise ValueError('Nonempty transfer directory has no matching ownership record')
    else:
        owner.write_text(identity, encoding='utf-8')

    def download(pair):
        index, part = pair
        target = destination / part['name']
        if target.exists():
            verify_volume(target, part)
            return  # A retried transfer reuses completed, reverified volumes.
        with tempfile.TemporaryDirectory(prefix=f'part{index}-', dir=destination) as temporary:
            directory = Path(temporary)
            name = record['volume_artifacts'][index - 1]['name']
            gh(['run', 'download', source_run, '--repo', repository, '--name', name, '--dir', str(directory)])
            if {p.name for p in directory.iterdir()} != {part['name']}:
                raise ValueError('Unexpected volume artifact inventory')
            path = directory / part['name']
            verify_volume(path, part)
            if path.resolve().parent != directory.resolve() or target.resolve().parent != destination.resolve():
                raise ValueError('Volume move escapes exact transfer root')
            path.rename(target)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(download, enumerate(parts, 1)))
    # gh downloads by name; bind the immutable IDs before AND after transfer.
    validate_preflight(repo, record, source_run, current_stage, source_stage, allow_migration,
                       check_manifest=False)
    (destination / 'checkpoint.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def verify_tree(work, manifest):
    for relative, field in (('src/out/Default/args.gn', 'gn_args_sha256'),
                             ('google-services-source.json', 'source_receipt_sha256')):
        if sha256(plain(work / relative)) != manifest[field]:
            raise ValueError('Extracted checkpoint input changed: ' + relative)
    fields = dict(re.findall(r'(?m)^(MAJOR|MINOR|BUILD|PATCH)=(\d+)\s*$',
                             (work / 'src/chrome/VERSION').read_text()))
    if '.'.join(fields.get(k, '') for k in ('MAJOR', 'MINOR', 'BUILD', 'PATCH')) != VERSION:
        raise ValueError('Extracted source is not Windows 156 target')


def inspect_archive_listing(lines):
    entries = 0
    uncompressed_bytes = 0
    contents = False
    for line in lines:
        line = line.rstrip('\r\n')
        if line.startswith('----------'):
            contents = True
        if not contents:
            continue
        if line.startswith(('Symbolic Link =', 'Hard Link =')):
            raise ValueError('Archive links are forbidden')
        if line.startswith('Path = '):
            name = line[7:].replace('\\', '/')
            path = PurePosixPath(name)
            if (not name or ':' in name or path.is_absolute() or '..' in path.parts
                    or path.parts[0] != 'chromix' or any(ord(c) < 32 for c in name)):
                raise ValueError('Archive entry escapes the owned work directory')
            entries += 1
        if line.startswith('Size = '):
            size = line[7:]
            if not size.isdecimal() or int(size) > 1024**4:
                raise ValueError('Invalid archive member size')
            uncompressed_bytes += int(size)
            if uncompressed_bytes > 1024**4:
                raise ValueError('Archive exceeds the bounded Windows source workspace')
    if not entries:
        raise ValueError('Archive has no validated work-tree entries')
    return {'entries': entries, 'uncompressed_bytes': uncompressed_bytes,
            'method': '7z directory listing only; no test decompression'}


def validate_archive_listing(lines):
    return inspect_archive_listing(lines)['entries']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['create', 'preflight', 'restore', 'verify-tree', 'verify-archive'])
    p.add_argument('--repo', type=Path)
    p.add_argument('--work', type=Path)
    p.add_argument('--parts', type=Path)
    p.add_argument('--stage', type=int, default=1)
    p.add_argument('--output', type=Path)
    p.add_argument('--resume-safe', action='store_true')
    p.add_argument('--run')
    p.add_argument('--destination', type=Path)
    p.add_argument('--manifest', type=Path)
    p.add_argument('--archive', type=Path)
    p.add_argument('--sevenzip')
    p.add_argument('--source-stage', type=int, default=0)
    p.add_argument('--allow-migration', action='store_true')
    p.add_argument('--preflight-file', type=Path)
    p.add_argument('--tree-bytes', type=int)
    a = p.parse_args()
    if not 0 <= a.source_stage <= 99:
        p.error('--source-stage must be 0 (latest) or 1..99')
    if a.operation == 'create':
        make_manifest(a.repo, a.work, a.parts, a.stage, a.output, a.resume_safe, a.tree_bytes)
    elif a.operation == 'restore':
        restore(a.repo, a.destination, a.run, a.stage, a.source_stage,
                a.allow_migration, a.preflight_file)
    elif a.operation == 'preflight':
        result = preflight(a.repo, a.run, a.stage, a.source_stage, a.allow_migration)
        if a.output:
            plain(a.output).parent.mkdir(parents=True, exist_ok=True)
            a.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'source_run': a.run, 'stage': result['stage'],
                          'volumes': len(result['volume_artifacts']), 'large_transfer_started': False}))
    elif a.operation == 'verify-tree':
        verify_tree(a.work, json.loads(a.manifest.read_text(encoding='utf-8-sig')))
    else:
        with tempfile.TemporaryFile('w+b') as listing:
            result = subprocess.run([a.sevenzip, 'l', '-slt', '-sccUTF-8', str(a.archive)], stdout=listing,
                                    stderr=subprocess.PIPE, timeout=600,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ValueError('Cannot inspect checkpoint archive')
            listing.seek(0)
            summary = inspect_archive_listing(line.decode('utf-8', errors='strict') for line in listing)
            if a.output:
                plain(a.output).parent.mkdir(parents=True, exist_ok=True)
                a.output.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print('Windows 156 checkpoint operation passed: ' + a.operation)


if __name__ == '__main__':
    main()
