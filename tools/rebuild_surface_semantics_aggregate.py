#!/usr/bin/env python3
"""Compose new Windows 155 semantic overlays into the actual CI aggregate.

The admitted base is fixed. Existing selected patches and restoration overlays
remain byte-identical. Only the newly selected overlay target sections of the
aggregate are regenerated, after exact forward/reverse source replay. This is
source evidence, not a browser build, runtime acceptance, or activation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import difflib
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import uuid
from urllib.request import Request, urlopen

# Existing build helpers use sibling imports in CLI mode. Keep the same source
# directory visible when this module is imported by the unit-test package.
_TOOLS = str(Path(__file__).resolve().parent)
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
from build_google_services_patch import patch_series, prune_selected_sources, sections
from patch_selection import select

BASE_COMMIT = '75599a0755ebeb1f57129d2a118c0d1f9ed2e636'
BASE_AGGREGATE_SHA256 = '6d01b4985fd5c03fe4f8818d99b31d1bf42b29c01923278c11e79bcd56385115'
CHROMIUM_VERSION = '155.0.8059.39'
CORE_COMMIT = 'ffeb45a2bb88bc56a1dfed31410aeb10e9efcd5a'
WINDOWS_COMMIT = '989b4382364baaddb8b3180e1eb1030ca96a7273'
AGGREGATE = 'patches/google-services/0001-restore-google-services.patch'
CONFIG = 'build/google-services-win155.json'
MANIFEST = 'patches/fingerprint-modes/manifest.json'
INTEGRATION_KEY = 'surface_semantics_integration'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def owned(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or '\\' in name or ':' in name
            or any(p in ('', '.', '..') for p in name.split('/'))):
        raise ValueError('Unsafe repository path: ' + name)
    path = root.joinpath(*relative.parts).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('Path escapes repository: ' + name)
    return path


def git_bytes(repo: Path, name: str) -> bytes:
    owned(repo, name)
    result = subprocess.run(['git', 'show', f'{BASE_COMMIT}:{name}'], cwd=repo,
                            capture_output=True, check=False)
    if result.returncode:
        raise ValueError('The exact base object is required locally: ' + name)
    return result.stdout


def verify_selector_bytes(name: str, baseline: bytes, working: bytes):
    # These two tracked inputs use text=auto and are checked out as CRLF on
    # Windows. Patch bytes and explicitly LF-pinned selectors stay exact.
    automatic_eol = name in {'build/ungoogled-revisions.psd1', 'CHROMIUM_WINDOWS_VERSION'}
    normalize = lambda value: value.replace(b'\r\n', b'\n') if automatic_eol else value
    if normalize(baseline) != normalize(working):
        raise ValueError('The original patch selection recipe changed: ' + name)
    return {'path': name, 'base_blob_sha256': sha(baseline),
            'working_sha256': sha(working),
            'comparison': 'git auto-EOL text normalized to LF' if automatic_eol else 'exact bytes'}


def parse_sections(data: bytes) -> dict[str, str]:
    result = {}
    for name, _, text in sections(data.decode('utf-8')):
        if name in result:
            raise ValueError('Duplicate aggregate target: ' + name)
        result[name] = text
    if not result:
        raise ValueError('Patch has no unified source sections')
    return result


def validate_overlay_names(names: list[str], existing: list[str]) -> list[str]:
    if not names or len(names) != len(set(names)):
        raise ValueError('Choose at least one unique new overlay explicitly')
    numbers = []
    for name in names:
        match = re.fullmatch(r'patches/fingerprint-modes/(\d{4})-[a-z0-9-]+\.patch', name)
        if not match or int(match[1]) <= 10 or name in existing:
            raise ValueError('Only explicit overlays numbered after 0010 are admitted: ' + name)
        numbers.append(int(match[1]))
    if numbers != sorted(set(numbers)):
        raise ValueError('Overlay numbers must be unique and increasing')
    return names


def ensure_config_prefix(base, current, current_manifest):
    """Refuse unrelated manual config edits or an untracked previous aggregate."""
    declared = current_manifest.get(INTEGRATION_KEY)
    previous = [] if declared is None else declared.get('overlays', [])
    if declared is not None and (declared.get('schema_version') != 1
            or declared.get('base_commit') != BASE_COMMIT
            or declared.get('base_aggregate_sha256') != BASE_AGGREGATE_SHA256):
        raise ValueError('Previous candidate integration belongs to another base')
    if not isinstance(previous, list) or any(not isinstance(v, str) for v in previous):
        raise ValueError('Invalid previous candidate overlay list')
    expected = copy.deepcopy(base)
    expected['restoration_overlays'] += previous
    if current != expected:
        raise ValueError('Current restoration config has unrelated or untracked edits')
    return declared


def ensure_manifest_base(base, current):
    mutable = {'ordered_overlays', 'combined_patch_sha256',
               'combined_patch_changed_files', INTEGRATION_KEY}
    if {k: v for k, v in base.items() if k not in mutable} != {
            k: v for k, v in current.items() if k not in mutable}:
        raise ValueError('Current mode manifest has unrelated edits')
    if current.get('ordered_overlays', [])[:len(base['ordered_overlays'])] != base['ordered_overlays']:
        raise ValueError('Original mode manifest entries changed')


def ensure_receipt_base(base, current, aggregate, section_names, previous_targets):
    mutable = {'patch_sha256', 'changed_files', 'source_inputs'}
    if {k: v for k, v in base.items() if k not in mutable} != {
            k: v for k, v in current.items() if k not in mutable}:
        raise ValueError('Current aggregate receipt has unrelated edits')
    if (current.get('patch_sha256') != sha(aggregate)
            or current.get('changed_files') != sorted(section_names)):
        raise ValueError('Current aggregate receipt does not describe its bytes')
    expected = {item['path']: item['sha256'] for item in base['source_inputs']}
    actual = {item['path']: item['sha256'] for item in current.get('source_inputs', [])}
    if len(actual) != len(current.get('source_inputs', [])):
        raise ValueError('Current source receipt contains duplicate inputs')
    if any(actual.get(name) != digest for name, digest in expected.items()):
        raise ValueError('Original upstream source identity changed')
    if not (set(actual) - set(expected)).issubset(previous_targets):
        raise ValueError('Current receipt has untracked upstream source inputs')


def fetch(url: str, limit=32 * 1024**2) -> bytes:
    request = Request(url, headers={'User-Agent': 'Chromix-155-source-replay'})
    with urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Pinned source response exceeded its bounded budget')
    return data


def pinned_dependency(work: Path, repository: str, commit: str):
    """Acquire only source patch definitions, never a compiler or binary."""
    destination = work / 'dependencies' / repository
    destination.mkdir(parents=True, exist_ok=False)
    url = f'https://codeload.github.com/ungoogled-software/{repository}/tar.gz/{commit}'
    archive = fetch(url)
    files = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as source:
        roots = set()
        for member in source:
            path = PurePosixPath(member.name)
            if not path.parts or path.is_absolute() or '..' in path.parts:
                raise ValueError('Unsafe pinned source archive path')
            roots.add(path.parts[0])
            if len(roots) != 1 or not (member.isdir() or member.isfile()):
                raise ValueError('Pinned source archive contains a link or unexpected root')
            if len(path.parts) == 1 or member.isdir():
                continue
            relative = '/'.join(path.parts[1:])
            target = owned(destination, relative)
            total += member.size
            if member.size < 0 or total > 128 * 1024**2 or len(files) > 10000:
                raise ValueError('Pinned source archive exceeded its extraction budget')
            stream = source.extractfile(member)
            if stream is None:
                raise ValueError('Missing pinned source member')
            data = stream.read(member.size + 1)
            if len(data) != member.size:
                raise ValueError('Pinned source member length mismatch')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            files.append({'path': relative, 'sha256': sha(data), 'bytes': len(data)})
    receipt = {'repository': 'ungoogled-software/' + repository, 'commit': commit,
               'url': url, 'archive_sha256': sha(archive), 'files': files}
    (destination / 'replay-source-receipt.json').write_bytes(json_bytes(receipt))
    return destination, receipt


def supplied_dependency(root: Path, commit: str):
    result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root,
                            capture_output=True, text=True, check=False)
    if result.returncode or result.stdout.strip() != commit:
        raise ValueError('Supplied dependency must be a checkout at the exact pin')
    state = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=all'],
                           cwd=root, capture_output=True, text=True, check=True)
    if state.stdout.strip():
        raise ValueError('Supplied dependency checkout is not clean')
    return root, {'path': str(root), 'commit': commit, 'clean_checkout': True}


def apply(tree: Path, work: Path, patch_bin: str, data: bytes, label: str, reverse=False):
    if not data.strip():
        return
    path = work / 'patches' / label
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    command = [patch_bin, '--binary', '--batch', '--fuzz=0', '-p1',
               '--reverse' if reverse else '--forward', '--no-backup-if-mismatch',
               '--input', str(path)]
    for extra in (['--dry-run'], []):
        result = subprocess.run(command + extra, cwd=tree, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=90)
        if result.returncode:
            raise RuntimeError(f'{label}: {result.stdout}{result.stderr}')


def contents(tree: Path, names: set[str]):
    return {name: owned(tree, name).read_bytes() if owned(tree, name).is_file() else None
            for name in names}


def compose(base_sections, baseline, after, targets):
    result = dict(base_sections)
    for name in sorted(targets):
        a, b = baseline[name], after[name]
        if b is None:
            raise ValueError('Semantic overlays may not delete source files: ' + name)
        text = ''.join(difflib.unified_diff(
            a.decode('utf-8').splitlines(True) if a is not None else [],
            b.decode('utf-8').splitlines(True),
            fromfile='a/' + name if a is not None else '/dev/null', tofile='b/' + name))
        if text:
            result[name] = text
        else:
            result.pop(name, None)
    for name, text in base_sections.items():
        if name not in targets and result.get(name) != text:
            raise ValueError('Unrelated aggregate section changed: ' + name)
    return result


def atomic_write(path: Path, data: bytes):
    temporary = path.with_name(path.name + '.semantic-rebuild-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def publish(repo: Path, files: dict[str, bytes], history: dict[str, bytes]):
    marker = owned(repo, 'patches/fingerprint-modes/.surface-semantics-rebuild-in-progress.json')
    if marker.exists():
        raise ValueError('An earlier candidate metadata transaction needs inspection')
    for digest, data in history.items():
        if sha(data) != digest:
            raise ValueError('Aggregate history digest mismatch')
        path = owned(repo, f'patches/google-services/history/{digest}.patch')
        if path.exists() and path.read_bytes() != data:
            raise ValueError('Existing aggregate history must never be overwritten')
    original = {name: owned(repo, name).read_bytes() for name in files}
    with marker.open('xb') as stream:
        stream.write(json_bytes({'schema_version': 1, 'paths': list(files)}))
    try:
        for digest, data in history.items():
            path = owned(repo, f'patches/google-services/history/{digest}.patch')
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        for name, data in files.items():
            atomic_write(owned(repo, name), data)
        for name, data in files.items():
            if owned(repo, name).read_bytes() != data:
                raise ValueError('Candidate metadata readback mismatch: ' + name)
    except Exception:
        for name, data in original.items():
            if owned(repo, name).read_bytes() != data:
                atomic_write(owned(repo, name), data)
        raise
    else:
        marker.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--work', type=Path, required=True, help='New disposable source replay directory')
    parser.add_argument('--patch-bin', required=True)
    parser.add_argument('--overlay', action='append', required=True, help='Explicit new overlay path, in order')
    parser.add_argument('--core', type=Path, help='Optional clean checkout at the pinned core commit')
    parser.add_argument('--windows', type=Path, help='Optional clean checkout at the pinned Windows commit')
    parser.add_argument('--write', action='store_true', help='Publish validated candidate metadata; never activate')
    args = parser.parse_args(argv)
    repo, work = args.repo.resolve(), args.work.resolve()
    if work.exists() or work == repo or work.is_relative_to(repo):
        raise ValueError('Use a new scratch directory outside the source checkout')
    if bool(args.core) != bool(args.windows):
        raise ValueError('Supply both pinned dependency checkouts or neither')
    base_aggregate = git_bytes(repo, AGGREGATE)
    if sha(base_aggregate) != BASE_AGGREGATE_SHA256:
        raise ValueError('The admitted aggregate base does not match')
    base_config = json.loads(git_bytes(repo, CONFIG))
    base_manifest = json.loads(git_bytes(repo, MANIFEST))
    base_receipt = json.loads(git_bytes(repo, str(PurePosixPath(AGGREGATE).with_suffix('.json'))))
    if (base_config['chromium_version'] != CHROMIUM_VERSION
            or base_config['core_commit'] != CORE_COMMIT
            or base_config['windows_commit'] != WINDOWS_COMMIT
            or base_receipt['patch_sha256'] != BASE_AGGREGATE_SHA256):
        raise ValueError('The base preparation recipe is not the admitted Windows 155 recipe')
    receipt_name = str(PurePosixPath(AGGREGATE).with_suffix('.json'))
    current_metadata = {name: owned(repo, name).read_bytes()
                        for name in (AGGREGATE, receipt_name, CONFIG, MANIFEST)}
    current_aggregate = current_metadata[AGGREGATE]
    current_config = json.loads(current_metadata[CONFIG])
    current_manifest = json.loads(current_metadata[MANIFEST])
    current_receipt = json.loads(current_metadata[receipt_name])
    ensure_manifest_base(base_manifest, current_manifest)
    previous = ensure_config_prefix(base_config, current_config, current_manifest)
    if sha(current_aggregate) != current_manifest['combined_patch_sha256']:
        raise ValueError('Current aggregate and manifest disagree')
    if previous is None and current_aggregate != base_aggregate:
        raise ValueError('Current aggregate has untracked changes')
    base_sections, current_sections = parse_sections(base_aggregate), parse_sections(current_aggregate)
    previous_targets = set(previous.get('target_paths', [])) if previous else set()
    ensure_receipt_base(base_receipt, current_receipt, current_aggregate,
                        current_sections, previous_targets)
    for name in set(base_sections) | set(current_sections):
        if name not in previous_targets and base_sections.get(name) != current_sections.get(name):
            raise ValueError('Current aggregate has an unrelated source change: ' + name)
    overlays = validate_overlay_names(args.overlay, base_config['restoration_overlays'])
    selected_manifest, selected = select(repo, 'windows')
    protected = []
    for name, data in [*selected, *[(p, owned(repo, p).read_bytes())
                                  for p in base_config['restoration_overlays']]]:
        if data != git_bytes(repo, name):
            raise ValueError('An original selected patch changed: ' + name)
        protected.append({'path': name, 'sha256': sha(data)})
    if len(selected) != 231 or len(protected) != 242:
        raise ValueError('Original selected patch chain is incomplete')
    selector_identities = []
    for name in ['patches/series', 'build/ungoogled-revisions.psd1',
                 'CHROMIUM_WINDOWS_VERSION', 'tools/patch_selection.py']:
        selector_identities.append(verify_selector_bytes(
            name, git_bytes(repo, name), owned(repo, name).read_bytes()))
    overlay_bytes = [(name, owned(repo, name).read_bytes()) for name in overlays]
    targets = {p for _, data in overlay_bytes for p, _, _ in sections(data.decode('utf-8'))}
    if not targets:
        raise ValueError('Selected overlays do not modify any source')
    work.mkdir(parents=True, exist_ok=False)
    if args.core:
        core, core_receipt = supplied_dependency(args.core.resolve(), CORE_COMMIT)
        windows, windows_receipt = supplied_dependency(args.windows.resolve(), WINDOWS_COMMIT)
    else:
        core, core_receipt = pinned_dependency(work, 'ungoogled-chromium', CORE_COMMIT)
        windows, windows_receipt = pinned_dependency(work, 'ungoogled-chromium-windows', WINDOWS_COMMIT)
    core_patches = list(patch_series(core))
    windows_patches = list(patch_series(windows))
    all_patches = ([('core', name, data) for name, data in core_patches]
                   + [('windows', name, data) for name, data in windows_patches]
                   + [('chromix', name, data.decode('utf-8')) for name, data in selected])
    created = {p for _, _, data in all_patches for p, new, _ in sections(data)
               if new and p in targets}
    created.update(p for p, new, _ in sections(base_aggregate.decode('utf-8')) if new)
    created.update(p for _, data in overlay_bytes for p, new, _ in sections(data.decode('utf-8')) if new)
    tree = work / 'tree'
    tree.mkdir()
    known_sources = {item['path']: item['sha256'] for item in base_receipt['source_inputs']}
    def fetch_source(name):
        data = fetch(f'https://raw.githubusercontent.com/chromium/chromium/{CHROMIUM_VERSION}/{name}', 16 * 1024**2)
        if name in known_sources and sha(data) != known_sources[name]:
            raise ValueError('Pinned upstream bytes changed: ' + name)
        path = owned(tree, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {'path': name, 'sha256': sha(data)}
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(fetch_source, sorted(targets - created)))
    pruning = (core / 'pruning.list').read_bytes().replace(b'\r\n', b'\n')
    if sha(pruning) != base_receipt['pruning_list_sha256']:
        raise ValueError('Pinned pruning list differs from the base receipt')
    pruned = {line.strip() for line in pruning.decode().splitlines()
              if line.strip() and not line.startswith('#')}
    prune_selected_sources(tree, targets & pruned)
    replayed = []
    for layer, name, data in all_patches:
        fragments = ''.join(text for p, _, text in sections(data) if p in targets)
        if fragments:
            label = f'{len(replayed):04d}-{layer}-{Path(name).name}'
            apply(tree, work, args.patch_bin, fragments.encode(), label)
            replayed.append({'layer': layer, 'patch': name, 'fragment_sha256': sha(fragments.encode())})
    baseline = contents(tree, targets)
    old_fragment = ''.join(base_sections[name] for name in sorted(targets) if name in base_sections).encode()
    apply(tree, work, args.patch_bin, old_fragment, 'aggregate-base.patch')
    before_overlays = contents(tree, targets)
    for index, (name, data) in enumerate(overlay_bytes):
        before = contents(tree, targets)
        apply(tree, work, args.patch_bin, data, f'overlay-{index:02d}.patch')
        after = contents(tree, targets)
        if before == after:
            raise ValueError('An overlay made no source change: ' + name)
        apply(tree, work, args.patch_bin, data, f'overlay-{index:02d}-reverse.patch', reverse=True)
        if contents(tree, targets) != before:
            raise ValueError('Overlay reverse replay mismatch: ' + name)
        apply(tree, work, args.patch_bin, data, f'overlay-{index:02d}-reapply.patch')
        if contents(tree, targets) != after:
            raise ValueError('Overlay repeat replay mismatch: ' + name)
    final = contents(tree, targets)
    updated_sections = compose(base_sections, baseline, final, targets)
    updated = ''.join(updated_sections[name] for name in sorted(updated_sections)).encode('utf-8')
    for index, (_, data) in reversed(list(enumerate(overlay_bytes))):
        apply(tree, work, args.patch_bin, data, f'reset-overlay-{index:02d}.patch', reverse=True)
    if contents(tree, targets) != before_overlays:
        raise ValueError('Sequential overlay reversal mismatch')
    apply(tree, work, args.patch_bin, old_fragment, 'reset-base.patch', reverse=True)
    if contents(tree, targets) != baseline:
        raise ValueError('Base aggregate reversal mismatch')
    replacement = ''.join(updated_sections[name] for name in sorted(targets) if name in updated_sections).encode()
    for label, reverse, expected_state in [('composed.patch', False, final),
                                          ('composed-reverse.patch', True, baseline),
                                          ('composed-reapply.patch', False, final)]:
        apply(tree, work, args.patch_bin, replacement, label, reverse=reverse)
        if contents(tree, targets) != expected_state:
            raise ValueError('Composed aggregate replay mismatch: ' + label)
    config = copy.deepcopy(base_config)
    config['restoration_overlays'] += overlays
    receipt = copy.deepcopy(base_receipt)
    receipt['patch_sha256'] = sha(updated)
    receipt['changed_files'] = sorted(updated_sections)
    known_sources.update({item['path']: item['sha256'] for item in fetched})
    receipt['source_inputs'] = [{'path': p, 'sha256': known_sources[p]} for p in sorted(known_sources)]
    manifest = copy.deepcopy(base_manifest)
    manifest['ordered_overlays'] += [{'path': name, 'sha256': sha(data),
        'files': len(parse_sections(data))} for name, data in overlay_bytes]
    manifest['combined_patch_sha256'] = sha(updated)
    manifest['combined_patch_changed_files'] = len(updated_sections)
    manifest[INTEGRATION_KEY] = {'schema_version': 1, 'base_commit': BASE_COMMIT,
        'base_aggregate_sha256': BASE_AGGREGATE_SHA256, 'overlays': overlays,
        'target_paths': sorted(targets), 'requires_new_exact_dll_acceptance': True}
    changed = sorted(p for p in set(base_sections) | set(updated_sections)
                     if base_sections.get(p) != updated_sections.get(p))
    if not set(changed).issubset(targets):
        raise ValueError('Unrelated aggregate sections were changed')
    files = {AGGREGATE: updated, str(PurePosixPath(AGGREGATE).with_suffix('.json')): json_bytes(receipt),
             CONFIG: json_bytes(config), MANIFEST: json_bytes(manifest)}
    staged = work / 'staged'
    for name, data in files.items():
        path = owned(staged, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    report = {'schema_version': 1, 'base_commit': BASE_COMMIT,
        'base_aggregate_sha256': BASE_AGGREGATE_SHA256, 'previous_aggregate_sha256': sha(current_aggregate),
        'candidate_aggregate_sha256': sha(updated), 'chromium_version': CHROMIUM_VERSION,
        'overlays': manifest['ordered_overlays'][len(base_manifest['ordered_overlays']):],
        'target_paths': sorted(targets), 'changed_aggregate_sections': changed,
        'unrelated_aggregate_sections_unchanged': True, 'protected_patches': protected,
        'protected_patch_count': len(protected), 'protected_selection_recipe': selector_identities,
        'dependency_receipts': [core_receipt, windows_receipt],
        'fetched_upstream_sources': fetched, 'baseline_fragments_replayed': replayed,
        'overlay_forward_reverse_replay': True, 'aggregate_forward_reverse_replay': True,
        'composed_matches_sequential_overlays': True, 'metadata_written': False,
        'browser_compiled': False, 'runtime_accepted': False, 'production_activated': False}
    if args.write:
        # Recheck shared source paths immediately before publishing metadata.
        for entry in protected:
            if sha(owned(repo, entry['path']).read_bytes()) != entry['sha256']:
                raise ValueError('A protected patch changed during replay: ' + entry['path'])
        for entry in selector_identities:
            if sha(owned(repo, entry['path']).read_bytes()) != entry['working_sha256']:
                raise ValueError('A patch selector changed during replay: ' + entry['path'])
        for name, data in overlay_bytes:
            if owned(repo, name).read_bytes() != data:
                raise ValueError('A new overlay changed during replay: ' + name)
        for name, data in current_metadata.items():
            if owned(repo, name).read_bytes() != data:
                raise ValueError('Candidate metadata changed concurrently: ' + name)
        publish(repo, files, {BASE_AGGREGATE_SHA256: base_aggregate, sha(current_aggregate): current_aggregate})
        report['metadata_written'] = True
    (work / 'aggregate-replay-report.json').write_bytes(json_bytes(report))
    print(json.dumps({'report': str(work / 'aggregate-replay-report.json'),
                      'aggregate_sha256': sha(updated), 'changed_sections': len(changed),
                      'protected_patches': len(protected), 'metadata_written': args.write}))


if __name__ == '__main__':
    main()
