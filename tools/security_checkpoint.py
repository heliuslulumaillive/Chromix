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
    return parts


def verify_volume(path, part):
    plain(path)
    if not path.is_file() or path.stat().st_size != part['bytes'] or sha256(path) != part['sha256']:
        raise ValueError('Checkpoint volume content mismatch: ' + part['name'])


def make_manifest(repo, work, parts, stage, output, resume_safe):
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
    if resume_safe:
        validate_manifest(manifest, source_commit=manifest['source_commit'],
                          repository=manifest['repository'], source_run=manifest['run_id'],
                          expected_recipe=recipe(repo))
    output.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


def restore(repo, destination, source_run, current_stage):
    repository = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid repository')
    if not re.fullmatch(r'[1-9][0-9]*', source_run):
        raise ValueError('Numeric source run required')
    base = f'repos/{repository}/actions/runs/{source_run}'
    run = json.loads(gh(['api', base]))
    if str(run.get('id')) != source_run:
        raise ValueError('Run identity mismatch')
    artifacts = json.loads(gh(['api', base + '/artifacts?per_page=100']))['artifacts']
    pattern = re.compile(re.escape(VARIANT) + r'-checkpoint-s([1-9][0-9]*)\Z')
    candidates = [(int(m[1]), a) for a in artifacts
                  if not a.get('expired') and (m := pattern.fullmatch(a['name']))
                  and (source_run != os.environ.get('GITHUB_RUN_ID') or int(m[1]) < current_stage)]
    if not candidates:
        raise ValueError('No complete preceding Windows 156 checkpoint; never substitute a 155 donor')
    stage = max(n for n, _ in candidates)
    selected = [a for n, a in candidates if n == stage]
    if len(selected) != 1:
        raise ValueError('Ambiguous checkpoint manifest')
    plain(destination)
    destination.mkdir(parents=True, exist_ok=False)
    manifest_dir = destination / 'manifest'
    gh(['run', 'download', source_run, '--repo', repository, '--name', selected[0]['name'],
        '--dir', str(manifest_dir)])
    if {p.name for p in manifest_dir.iterdir()} != {'checkpoint.json'}:
        raise ValueError('Unexpected checkpoint manifest inventory')
    manifest = json.loads(plain(manifest_dir / 'checkpoint.json').read_text(encoding='utf-8-sig'))
    parts = validate_manifest(manifest, source_commit=run['head_sha'], repository=repository,
                              source_run=source_run, expected_recipe=recipe(repo))
    if manifest['stage'] != stage:
        raise ValueError('Manifest artifact/stage mismatch')

    def download(pair):
        index, part = pair
        name = f'{VARIANT}-s{stage}-part{index}'
        matching = [a for a in artifacts if a['name'] == name and not a.get('expired')]
        if len(matching) != 1:
            raise ValueError('Missing or ambiguous checkpoint volume: ' + name)
        directory = destination / f'part{index}'
        gh(['run', 'download', source_run, '--repo', repository, '--name', name, '--dir', str(directory)])
        if {p.name for p in directory.iterdir()} != {part['name']}:
            raise ValueError('Unexpected volume artifact inventory')
        path = directory / part['name']
        verify_volume(path, part)
        target = destination / part['name']
        if path.resolve().parent != directory.resolve() or target.resolve().parent != destination.resolve():
            raise ValueError('Volume move escapes exact transfer root')
        path.rename(target)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(download, enumerate(parts, 1)))
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


def validate_archive_listing(lines):
    entries = 0
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
    if not entries:
        raise ValueError('Archive has no validated work-tree entries')
    return entries


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['create', 'restore', 'verify-tree', 'verify-archive'])
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
    a = p.parse_args()
    if a.operation == 'create':
        make_manifest(a.repo, a.work, a.parts, a.stage, a.output, a.resume_safe)
    elif a.operation == 'restore':
        restore(a.repo, a.destination, a.run, a.stage)
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
            validate_archive_listing(line.decode('utf-8', errors='strict') for line in listing)
    print('Windows 156 checkpoint operation passed: ' + a.operation)


if __name__ == '__main__':
    main()
