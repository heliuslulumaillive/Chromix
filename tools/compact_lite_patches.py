"""One-time, reproducible compaction of the accepted 156 replay, without building.

The replay cache is read-only. Historical patches and receipts are never edited.
New product patches are the editable source of truth after this bootstrap.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

try:
    from .patch_selection import select_legacy
    from .apply_restored_patches import transform_patch
    from .google_services_inputs import owned, write_if_changed
except ImportError:
    from patch_selection import select_legacy
    from apply_restored_patches import transform_patch
    from google_services_inputs import owned, write_if_changed

ACCEPTED_COMMIT = '0e5d5dc79e7b0b2c900273b909bfbeca3b6046d0'
ACCEPTED_AGGREGATE = '2ff6f9cae432da837dd1b1e7f7025660fdb8a5202393561fe6456cf0e4c7b36c'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def topic(name):
    """Each file has one owner; patches commute and contain no corrective layers."""
    if name.endswith(('/BUILD.gn', '.gni')) or name == 'BUILD.gn':
        return '01-build-integration'
    if 'shardx_cookie' in name:
        return '02-cookie-import'
    if '/sessions/' in name or 'session_restore' in name or 'browser_close' in name or 'process_singleton' in name:
        return '03-session-lifecycle'
    if '/history/' in name or 'history_retention' in name:
        return '04-history'
    if '/translate/' in name or '/translate_' in name:
        return '05-translation'
    if '/webauth' in name or '/fido/' in name or '/credentialmanagement/' in name:
        return '06-passkeys'
    if name.startswith('net/') or '/network/' in name or 'socks' in name:
        return '07-network-and-proxy'
    if 'webrtc' in name or 'peerconnection' in name:
        return '08-webrtc'
    if 'timezone' in name:
        return '09-timezone'
    if 'webgl' in name or 'gpu_fingerprint' in name or 'gpu_info' in name:
        return '10-webgl'
    if 'webgpu' in name:
        return '11-webgpu'
    if any(token in name for token in ('canvas', 'image_data_buffer', 'image_encoder', 'graphics_noise', 'pixel_', 'mask_first')):
        return '12-canvas-and-pixels'
    if '/fonts/' in name or 'font_' in name or 'font-' in name:
        return '13-fonts'
    if 'audio' in name:
        return '14-audio'
    if '/media' in name or 'codec' in name:
        return '15-media'
    if any(token in name for token in ('screen', 'display', 'visual_properties', 'render_widget', 'remote_frame', 'frame_connector')):
        return '16-display'
    if 'speech' in name or 'voice' in name:
        return '17-speech'
    if 'safe_browsing' in name or 'safety_' in name or '/security_' in name or '/download' in name or 'signed_web_bundle' in name:
        return '18-browser-protection'
    if name.startswith('base/') or name.startswith('components/ungoogled/'):
        return '19-shared-policy'
    if '/devtools/' in name or name.startswith('v8/'):
        return '20-devtools-and-clock'
    if name.startswith('third_party/blink/'):
        return '21-web-platform-surfaces'
    if name.startswith('content/') or name.startswith('chrome/app/'):
        return '22-process-and-startup'
    if 'shardx' in name or '/profiles/' in name or '/startup/' in name or '/ui/views/frame/' in name:
        return '23-native-profile'
    return '24-google-services-and-browser'


def blob(root, name):
    path = owned(root, name)
    return path.read_bytes().replace(b'\r\n', b'\n') if path.is_file() else None


def diff(name, old, new, *, git=False):
    lines = list(difflib.unified_diff(
        (old or b'').decode('utf-8').splitlines(keepends=True),
        (new or b'').decode('utf-8').splitlines(keepends=True),
        fromfile='a/' + name if old is not None else '/dev/null',
        tofile='b/' + name if new is not None else '/dev/null'))
    if not lines:
        return b''
    prefix = ''
    if git:
        prefix = f'diff --git a/{name} b/{name}\n'
        if old is None:
            prefix += 'new file mode 100644\n'
        elif new is None:
            prefix += 'deleted file mode 100644\n'
    # Git requires the explicit marker for a source without a final newline.
    body = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in lines)
    return (prefix + body).encode('utf-8')


def compact(repo, replay, scratch):
    report = json.loads(owned(repo, 'patches/google-services/0001-restore-google-services.json').read_text('utf-8'))
    if report['patch_sha256'] != ACCEPTED_AGGREGATE:
        raise ValueError('Bootstrap requires the exact accepted 156 aggregate')
    for item in report['source_inputs']:
        if digest(owned(replay / 'pristine', item['path']).read_bytes()) != item['sha256']:
            raise ValueError('Changed pristine replay input: ' + item['path'])
    config = json.loads(owned(repo, 'build/google-services-win155.json').read_text('utf-8'))
    before, final = replay / 'baseline', replay / 'restored'
    names = sorted({path.relative_to(tree).as_posix() for tree in (before, final)
                    for path in tree.rglob('*') if path.is_file()})
    binaries = config['restore_pruned_resources']
    aggregate = b''.join(diff(name, blob(before, name), blob(final, name))
                         for name in names if name not in binaries)
    if digest(aggregate) != ACCEPTED_AGGREGATE:
        raise ValueError('Replay trees differ from the accepted aggregate')
    _, patches = select_legacy(repo, 'windows')
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    scratch = scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='compact-', dir=scratch) as temporary:
        root = Path(temporary).resolve()
        if not root.is_relative_to(scratch) or root == scratch:
            raise ValueError('Scratch directory escaped the owned root')
        tree = root / 'src'
        tree.mkdir()
        env['GIT_CEILING_DIRECTORIES'] = str(root)
        for name in names:
            original = owned(before, name)
            data = original.read_bytes() if original.is_file() else None
            if data is not None:
                write_if_changed(tree / name, data)
        for name, raw in reversed(patches):
            patch = root / 'current.patch'
            patch.write_bytes(transform_patch(raw, set(), [])[0])
            subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', '--no-index',
                            '--whitespace=nowarn', '--reverse', str(patch)], cwd=tree,
                           env=env, check=True, capture_output=True)
        groups = collections.defaultdict(list)
        files = []
        for name in names:
            old, new = blob(tree, name), blob(final, name)
            files.append({'path': name, 'before_sha256': digest(old) if old is not None else None,
                          'after_sha256': digest(new) if new is not None else None})
            if old != new and name not in binaries:
                groups[topic(name)].append((name, diff(name, old, new, git=True)))
        entries = []
        for group, changes in sorted(groups.items()):
            name = 'patches/lite-windows156/' + group + '.patch'
            data = b''.join(raw for _, raw in changes)
            write_if_changed(owned(repo, name), data)
            entries.append({'path': name, 'sha256': digest(data), 'files': [n for n, _ in changes]})
            patch = root / 'current.patch'
            patch.write_bytes(data)
            subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', '--no-index',
                            '--whitespace=nowarn', str(patch)], cwd=tree,
                           env=env, check=True, capture_output=True)
        for name, spec in binaries.items():
            write_if_changed(tree / name, owned(repo, spec['repository_path']).read_bytes())
        for name in names:
            actual_path, expected_path = owned(tree, name), owned(final, name)
            actual = actual_path.read_bytes() if actual_path.is_file() else None
            expected = expected_path.read_bytes() if expected_path.is_file() else None
            if actual != expected:
                raise ValueError('Product replay changed accepted bytes: ' + name)
    runtime = {key: config[key] for key in ('chromium_version', 'core_commit', 'windows_commit',
               'native_browser', 'native_cookie', 'security_restoration', 'lite_retirement',
               'restore_pruned_resources', 'domain_rules_sha256')}
    manifest = {'schema_version': 1, 'kind': 'lite-windows156-product', 'arch': 'x64',
                'runtime': runtime, 'patches': entries, 'source_files': files,
                'domain_full_files': report['changed_files'],
                'legacy_equivalence': {'source_commit': ACCEPTED_COMMIT,
                    'aggregate_sha256': ACCEPTED_AGGREGATE, 'baseline_patches': len(patches),
                    'overlays': len(config['restoration_overlays']),
                    'source_files_checked': len(files), 'result': 'byte-identical'}}
    manifest['stack_sha256'] = digest(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode())
    write_if_changed(owned(repo, 'build/lite-windows156.json'),
                     (json.dumps(manifest, indent=2) + '\n').encode())
    print(json.dumps({'patches': len(entries), 'source_files_checked': len(files),
                      'result': 'byte-identical', 'stack_sha256': manifest['stack_sha256']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--replay', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    compact(args.repo.resolve(), args.replay.resolve(), args.scratch.resolve())
