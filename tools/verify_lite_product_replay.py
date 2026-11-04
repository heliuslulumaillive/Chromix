"""Exercise raw/substituted product preparation using authenticated replay files.

Only a fresh, caller-owned scratch directory is written. No browser or compiler
is launched and no installed package, original cache, or profile is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

try:
    from . import lite_patch_stack as stack, apply_restored_patches as arp
    from .compact_lite_patches import blob
    from .patch_selection import select_legacy, preparation_key
    from .prepare_google_services import apply_patch, pinned_inverse_rules, restore_domains
    from .prepare_lite_product import prepare
    from .google_services_inputs import write_if_changed
except ImportError:
    import lite_patch_stack as stack, apply_restored_patches as arp
    from compact_lite_patches import blob
    from patch_selection import select_legacy, preparation_key
    from prepare_google_services import apply_patch, pinned_inverse_rules, restore_domains
    from prepare_lite_product import prepare
    from google_services_inputs import write_if_changed


def snapshot(tree):
    return {p.relative_to(tree).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in tree.rglob('*') if p.is_file()}


def raw_blob(tree, name):
    path = tree / name
    return path.read_bytes() if path.is_file() else None


def exercise(repo, replay, core, windows, work, patch_bin):
    repo, replay, core, windows, work = (p.resolve() for p in (repo, replay, core, windows, work))
    if work.exists() or any(root.is_relative_to(work) or work.is_relative_to(root)
                            for root in (repo, replay, core, windows)):
        raise ValueError('Use a fresh scratch directory separate from all inputs')
    product = stack.load(repo)
    if (core / 'chromium_version.txt').read_text().strip() != product['runtime']['chromium_version']:
        raise ValueError('Replay tooling version mismatch')
    rules = pinned_inverse_rules(core / 'domain_regex.list')
    work.mkdir(parents=True)
    base = work / 'ungoogled'
    base.mkdir()
    for row in product['source_files']:
        raw = raw_blob(replay / 'baseline', row['path'])
        if raw is not None:
            write_if_changed(base / row['path'], raw)
    _, legacy = select_legacy(repo, 'windows')
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_CEILING_DIRECTORIES=str(work))
    for name, raw in reversed(legacy):
        current = work / 'current.patch'
        current.write_bytes(arp.transform_patch(raw, set(), [])[0])
        result = subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', '--no-index',
                                 '--reverse', '--whitespace=nowarn', str(current)], cwd=base,
                                env=env, capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError('Cannot reconstruct authenticated upstream baseline: ' + name)
    for row in product['source_files']:
        raw = blob(base, row['path'])
        if (stack.digest(raw) if raw is not None else None) != row['before_sha256']:
            raise ValueError('Upstream baseline differs: ' + row['path'])
    version = product['runtime']['chromium_version'].split('.')
    write_if_changed(base / 'chrome/VERSION', ''.join(f'{k}={v}\n' for k, v in zip(
        ('MAJOR', 'MINOR', 'BUILD', 'PATCH'), version)).encode())
    listed = {line for line in (windows / 'domain_substitution.list').read_text().splitlines() if line}
    forward_rules = arp._rules((core / 'domain_regex.list').read_bytes())
    reports = {}
    for label in ('raw', 'substituted'):
        stage = work / label
        src = stage / 'src'
        shutil.copytree(base, src)
        for source, target in ((core / 'domain_regex.list', stage / 'tooling/ungoogled-chromium/domain_regex.list'),
                               (windows / 'domain_substitution.list', stage / 'tooling/ungoogled-chromium-windows/domain_substitution.list')):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        if label == 'substituted':
            for name in listed:
                path = src / name
                if path.is_file():
                    text, encoding = arp._decode(path.read_bytes())
                    write_if_changed(path, arp._substitute(text, forward_rules).encode(encoding))
            arp.run_apply(src, repo, core, windows, 'windows', patch_bin, profile='lite')
        else:
            for row in product['patches']:
                apply_patch(src, patch_bin, repo / row['path'])
            for name in listed:
                path = src / name
                if path.is_file():
                    text, encoding = arp._decode(path.read_bytes())
                    write_if_changed(path, arp._substitute(text, forward_rules).encode(encoding))
        write_if_changed(src / '.chromix-patches',
                         (preparation_key(repo, 'windows', 'windows', profile='lite') + '\n').encode())
        result = prepare(repo, stage)
        stack.verify_source(repo, src, inverse_rules=rules)
        before = snapshot(src)
        receipt_before = (stage / 'google-services-source.json').stat().st_mtime_ns
        prepare(repo, stage)
        if before != snapshot(src) or receipt_before != (stage / 'google-services-source.json').stat().st_mtime_ns:
            raise ValueError('Repeated preparation rewrote unchanged source or receipt')
        reports[label] = {'status': 'passed', 'source_files': len(before),
                          'source_checks': len(result['source_checks']), 'idempotent': True}
    # Compare every reviewed source byte, including the comments left in their
    # original domain form by the existing preparation policy.
    for row in product['source_files']:
        name = row['path']
        if raw_blob(work / 'raw/src', name) != raw_blob(work / 'substituted/src', name):
            raise ValueError('Raw and cached preparation diverge: ' + name)
    # Apply the old domain restoration policy to the accepted selected source
    # in a disposable copy, so this comparison includes its final domain form.
    accepted = work / 'accepted-final'
    shutil.copytree(replay / 'restored', accepted)
    for name in listed:
        path = accepted / name
        if path.is_file():
            text, encoding = arp._decode(path.read_bytes())
            write_if_changed(path, arp._substitute(text, forward_rules).encode(encoding))
    restore_domains(accepted, None, core / 'domain_regex.list', windows / 'domain_substitution.list',
                    product['domain_full_files'], pinned=True)
    for row in product['source_files']:
        if raw_blob(accepted, row['path']) != raw_blob(work / 'raw/src', row['path']):
            raise ValueError('Product source differs from the accepted preparation policy: ' + row['path'])
    report = {'status': 'passed', 'patch_count': len(product['patches']), 'source_files': len(product['source_files']),
              'stack_sha256': product['stack_sha256'], 'paths': reports, 'accepted_source': 'byte-identical',
              'compiled': False, 'runtime_acceptance': 'separate'}
    write_if_changed(work / 'report.json', (json.dumps(report, indent=2) + '\n').encode())
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'replay', 'core', 'windows', 'work'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--patch-bin', required=True)
    args = parser.parse_args()
    exercise(args.repo, args.replay, args.core, args.windows, args.work, args.patch_bin)
