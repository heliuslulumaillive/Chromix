#!/usr/bin/env python3
"""Select committed checkpoints and transfer independent volumes concurrently."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import subprocess
import time

VERSION = '155.0.8059.39'
MANIFEST_NAME = re.compile(r'google-win155-checkpoint-s([1-9][0-9]*)\Z')


def gh(arguments, timeout=120):
    run = subprocess.run(['gh', *arguments], capture_output=True, text=True,
                         encoding='utf-8', errors='replace', timeout=timeout,
                         env={**os.environ, 'GH_DEBUG': ''},
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if run.returncode:
        error = re.sub(r'(https?://[^\s?]+)\?\S+', r'\1?[redacted]', run.stderr)
        raise RuntimeError('GitHub transfer failed: ' + error[-1500:])
    return run.stdout


def select_manifest(artifacts, source_run, current_run, stage):
    candidates = []
    for item in artifacts:
        match = MANIFEST_NAME.fullmatch(item.get('name', ''))
        if match and not item.get('expired', True):
            index = int(match[1])
            if str(source_run) != str(current_run) or index < stage:
                candidates.append((index, item))
    if not candidates:
        raise ValueError('No completed preceding checkpoint; compilation cannot start')
    latest = max(index for index, _ in candidates)
    selected = [item for index, item in candidates if index == latest]
    if len(selected) != 1:
        raise ValueError('Checkpoint manifest is ambiguous')
    return latest, selected[0]


def validate_manifest(manifest, source, artifacts, selected_stage):
    if (manifest.get('format_version') != 1
            or manifest.get('chromium_version') != VERSION
            or manifest.get('stage') != selected_stage
            or str(manifest.get('run_id')) != str(source['id'])
            or not re.fullmatch(r'[0-9a-f]{40}', str(manifest.get('commit', '')))
            or manifest['commit'] != source['head_sha']):
        raise ValueError('Checkpoint version, run, stage or commit does not match')
    parts = manifest.get('parts')
    if not isinstance(parts, list) or not 1 <= len(parts) <= 4:
        raise ValueError('Invalid checkpoint volume count')
    selected = []
    for index, part in enumerate(parts, 1):
        expected = f'tree.7z.{index:03d}'
        if (part.get('name') != expected or type(part.get('bytes')) is not int
                or not 0 < part['bytes'] <= 8 * 1024**3):
            raise ValueError('Invalid checkpoint volume path or size')
        name = f'google-win155-tree-s{selected_stage}-part{index}'
        matching = [item for item in artifacts if item.get('name') == name
                    and not item.get('expired', True)]
        if len(matching) != 1:
            raise ValueError('Completed checkpoint is missing a unique volume: ' + name)
        selected.append({**part, 'artifact_name': name,
                         'artifact_id': matching[0]['id']})
    expected_hashes = {}
    for field in ('source_patch_sha256', 'source_preparation_sha256', 'gn_args_sha256'):
        if field in manifest:
            if not re.fullmatch(r'[0-9a-f]{64}', str(manifest[field])):
                raise ValueError('Invalid checkpoint identity hash: ' + field)
            expected_hashes[field] = manifest[field]
    return selected, expected_hashes


def download_parts(selection, destination: Path, download=gh):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    def transfer(part):
        directory = destination / ('artifact-' + str(part['artifact_id']))
        directory.mkdir(exist_ok=False)
        download(['run', 'download', str(selection['source_run']), '--repo',
                  selection['repository'], '--name', part['artifact_name'],
                  '--dir', str(directory)], timeout=3600)
        files = list(directory.iterdir())
        if (len(files) != 1 or files[0].name != part['name']
                or not files[0].is_file() or files[0].is_symlink()
                or files[0].stat().st_size != part['bytes']):
            raise ValueError('Downloaded checkpoint volume does not match: ' + part['name'])
        return {'name': part['name'], 'path': str(files[0]), 'bytes': part['bytes']}

    # Each gh process has its own destination. Keep concurrency bounded to avoid
    # multiplying temporary ZIP/disk usage on the hosted Windows runner.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(transfer, selection['parts']))
    # 7-Zip expects adjacent volumes. Move only verified, individually named
    # files within this transfer directory; never move or delete whole trees.
    for part in results:
        source = Path(part['path']).resolve()
        target = (destination / part['name']).resolve()
        if not source.is_relative_to(destination) or target.parent != destination:
            raise ValueError('Checkpoint volume escapes transfer directory')
        if target.exists():
            raise FileExistsError(target)
        source.rename(target)
    return {'status': 'passed', 'volumes': len(results), 'parallel_downloads': 2,
            'bytes': sum(item['bytes'] for item in results),
            'seconds': round(time.monotonic() - started, 2),
            'first_volume': str(destination / 'tree.7z.001')}


def continuation_plan(build_outcome, candidate_ready, candidate_outcome,
                      cache_changed, checkpoint_outcome, source_run, current_run):
    saved = checkpoint_outcome == 'success'
    recovery = str(current_run) if saved else str(source_run)
    delivered = candidate_outcome == 'success'
    # Never retry a compiler/test/packaging error with the same source. Only a
    # successful budget stop or an unconfirmed artifact delivery may continue.
    retry = build_outcome == 'success' and not delivered
    if cache_changed and not saved:
        retry = False
    return {'resume_needed': retry, 'resume_source': recovery if retry else '',
            'recovery_source': recovery,
            'reason': ('delivery' if candidate_ready else 'budget') if retry else 'stop'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    select = sub.add_parser('select')
    select.add_argument('--repository', required=True)
    select.add_argument('--source-run', required=True)
    select.add_argument('--current-run', required=True)
    select.add_argument('--stage', type=int, required=True)
    select.add_argument('--output', type=Path, required=True)
    download = sub.add_parser('download')
    download.add_argument('--selection', type=Path, required=True)
    download.add_argument('--destination', type=Path, required=True)
    download.add_argument('--report', type=Path, required=True)
    resume = sub.add_parser('continue')
    resume.add_argument('--build-outcome', required=True)
    resume.add_argument('--candidate-ready', required=True)
    resume.add_argument('--candidate-outcome', required=True)
    resume.add_argument('--cache-changed', required=True)
    resume.add_argument('--checkpoint-outcome', required=True)
    resume.add_argument('--source-run', required=True)
    resume.add_argument('--current-run', required=True)
    args = parser.parse_args()
    if args.operation == 'select':
        if (not re.fullmatch(r'[\w.-]+/[\w.-]+', args.repository)
                or not re.fullmatch(r'[1-9][0-9]*', args.source_run)
                or not re.fullmatch(r'[1-9][0-9]*', args.current_run)
                or not 1 <= args.stage <= 4):
            raise ValueError('Invalid checkpoint selection input')
        source = json.loads(gh(['api', f'repos/{args.repository}/actions/runs/{args.source_run}']))
        if str(source.get('id')) != args.source_run:
            raise ValueError('Source run identity does not match the requested run')
        artifacts = json.loads(gh(['api', f'repos/{args.repository}/actions/runs/{args.source_run}/artifacts?per_page=100']))['artifacts']
        stage, item = select_manifest(artifacts, args.source_run, args.current_run, args.stage)
        directory = args.output.parent / 'checkpoint-selection-manifest'
        directory.mkdir(parents=True, exist_ok=False)
        gh(['run', 'download', args.source_run, '--repo', args.repository,
            '--name', item['name'], '--dir', str(directory)])
        manifest = json.loads((directory / 'checkpoint.json').read_text(encoding='utf-8-sig'))
        parts, expected_hashes = validate_manifest(manifest, source, artifacts, stage)
        result = {'schema_version': 1, 'repository': args.repository,
                  'source_run': args.source_run, 'stage': stage,
                  'checkpoint_commit': source['head_sha'], 'parts': parts,
                  'expected_hashes': expected_hashes}
        args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'checkpoint_selected': True, 'source_run': args.source_run,
                          'stage': stage, 'volumes': len(parts),
                          'bytes': sum(part['bytes'] for part in parts)}))
    elif args.operation == 'download':
        selection = json.loads(args.selection.read_text(encoding='utf-8-sig'))
        result = download_parts(selection, args.destination)
        args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result))
    else:
        result = continuation_plan(args.build_outcome, args.candidate_ready == 'true',
                                   args.candidate_outcome, args.cache_changed == 'true',
                                   args.checkpoint_outcome, args.source_run, args.current_run)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
                for name, value in result.items():
                    stream.write(f'{name}={str(value).lower() if isinstance(value, bool) else value}\n')
        print(json.dumps(result))


if __name__ == '__main__':
    main()
