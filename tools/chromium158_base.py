"""Prepare the retained Windows policy patches for the pinned, original Chromium archive."""
import argparse
import configparser
import hashlib
import json
from pathlib import Path
import subprocess

try:
    from .google_services_inputs import owned, write_if_changed
    from .restoration_rules import patch_series, sections
except ImportError:
    from google_services_inputs import owned, write_if_changed
    from restoration_rules import patch_series, sections

POLICY = 'build/chromium158-base.json'
ARCHIVE = 'build/chromium158-source.ini'
LINUX_SYSROOTS = (
    'build/linux/debian_bullseye_amd64-sysroot',
    'build/linux/debian_bullseye_i386-sysroot',
    'third_party/llvm-build-tools/debian_bullseye_amd64_sysroot',
    'third_party/llvm-build-tools/debian_bullseye_arm_sysroot',
    'third_party/llvm-build-tools/debian_bullseye_arm64_sysroot',
    'third_party/llvm-build-tools/debian_bullseye_i386_sysroot',
    'third_party/llvm-build-tools/debian_trixie_riscv64_sysroot',
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_policy(repo):
    policy = json.loads(owned(repo, POLICY).read_bytes())
    config = json.loads(owned(repo, 'build/lite-windows158.json').read_bytes())
    if digest(owned(repo, POLICY).read_bytes()) != config['chromium_base_sha256']:
        raise ValueError('Original Chromium source policy differs from the product manifest')
    version = policy['chromium_version']
    if version != '158.0.8127.36' or config['runtime']['chromium_version'] != version:
        raise ValueError('Incorrect native source version')
    download = configparser.ConfigParser(interpolation=None)
    download.read(owned(repo, ARCHIVE), encoding='utf-8')
    archive = download['chromium']
    filename = 'chromium-' + version + '.tar.xz'
    if (archive['url'] != 'https://commondatastorage.googleapis.com/chromium-browser-official/' + filename
            or archive['download_filename'] != filename or archive['sha256'] != policy['original_source_archive_sha256']
            or archive['strip_leading_dirs'] != 'chromium-' + version):
        raise ValueError('Incorrect release archive input')
    return policy, archive


def unpack_windows_source(repo, work, tar):
    policy, spec = source_policy(repo)
    archive = owned(work, 'download_cache/' + spec['download_filename'])
    destination = owned(work, 'src')
    if not destination.is_dir() or any(destination.iterdir()):
        raise ValueError('The original Chromium archive requires an empty source directory')
    with archive.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != spec['sha256']:
            raise ValueError('Original Chromium archive digest mismatch')
    # These Linux target sysroots contain multi-hop POSIX links. The Windows
    # build uses the host SDK and keeps all Chromium source/tooling around them.
    prefix = spec['strip_leading_dirs']
    excludes = ['--exclude=' + prefix + '/' + name for name in LINUX_SYSROOTS]
    subprocess.run([str(tar), '-x', *excludes, '--strip-components', '1', '-C',
                    str(destination), '-f', str(archive), prefix], check=True)
    version_file = owned(destination, 'chrome/VERSION').read_text('ascii')
    fields = dict(line.split('=', 1) for line in version_file.splitlines())
    if '.'.join(fields[key] for key in ('MAJOR','MINOR','BUILD','PATCH')) != policy['chromium_version']:
        raise ValueError('Unpacked source version differs from the pinned release')
    return {'archive_sha256': spec['sha256'], 'excluded_linux_sysroots': list(LINUX_SYSROOTS)}


def render(repo, work, core, windows):
    policy, _ = source_policy(repo)
    version = policy['chromium_version']
    pristine = set(policy['use_chromium_files'])
    if len(pristine) != len(policy['use_chromium_files']):
        raise ValueError('Duplicate native source policy path')
    output = owned(work, 'native158-base')
    resources = policy['preserve_resources']
    prefs = policy['preserve_pruned_sources']
    if resources.keys() & prefs.keys():
        raise ValueError('Duplicate source and resource preservation entry')
    originals = resources | prefs
    receipt = {'source': version, 'layers': {}, 'retained_resources': sorted(resources),
               'retained_pruned_sources': sorted(prefs)}
    seen = set()
    previous_pruning = None
    for layer, tooling in (('core', core), ('windows', windows)):
        pinned = policy['layers'][layer]
        entries = patch_series(tooling)
        keys = [(name, digest(raw.encode())) for name, raw in entries]
        if len(entries) != pinned['patch_count'] or digest(json.dumps(keys, separators=(',', ':')).encode()) != pinned['patch_list_sha256']:
            raise ValueError('Changed upstream patch inventory: ' + layer)
        skipped = set(policy['skip_patches'][layer])
        if not skipped.issubset(dict(entries)):
            raise ValueError('A designated upstream omission was not present')
        kept, omitted, retained = [], [], 0
        for name, text in entries:
            parts = sections(text)
            if not parts:
                raise ValueError('Upstream patch has no source sections: ' + name)
            seen.update(parts)
            selected = [section for path, section in parts.items() if path not in pristine]
            if name in skipped and selected:
                raise ValueError('Designated omission has an unaudited source path: ' + name)
            if not selected:
                omitted.append(name)
                continue
            kept.append(name)
            retained += len(selected)
            data = text if len(selected) == len(parts) else ''.join(selected)
            write_if_changed(owned(output, layer + '/' + name), data.encode())
        write_if_changed(owned(output, layer + '/series'), ('\n'.join(kept)+'\n').encode())
        lines = owned(tooling, 'pruning.list').read_text('utf-8').splitlines(keepends=True)
        if digest(''.join(lines).encode()) != pinned['pruning_sha256']:
            raise ValueError('Changed upstream pruning inventory: ' + layer)
        preserved = set(originals)
        active = {line.strip() for line in lines if line.strip() and not line.lstrip().startswith('#')}
        if not preserved.issubset(active) or (previous_pruning is not None and active != previous_pruning):
            raise ValueError('Windows and core pruning inputs disagree on the reviewed resources')
        previous_pruning = active
        if layer == 'core':
            write_if_changed(owned(output,'pruning.list'), ''.join(line for line in lines if line.strip() not in preserved).encode())
        receipt['layers'][layer] = {'retained_patches': len(kept), 'omitted_patches': omitted, 'retained_sections': retained}
    if not pristine.issubset(seen):
        raise ValueError('Unexpected path in the native source policy')
    for name, spec in originals.items():
        raw = owned(owned(work, 'src'), name).read_bytes()
        if len(raw) != spec['size'] or digest(raw) != spec['sha256']:
            raise ValueError('The original archive lacks the reviewed resource: ' + name)
    write_if_changed(owned(output,'receipt.json'),(json.dumps(receipt,indent=2)+'\n').encode())
    return receipt


def verify_base(repo, work):
    try:
        from .lite_patch_stack import load
    except ImportError:
        from lite_patch_stack import load
    src = owned(work, 'src')
    rows = load(repo)['source_files']
    for row in rows:
        path = owned(src, row['path'])
        if path.exists() and not path.is_file():
            raise ValueError('Unexpected native source file type: ' + row['path'])
        actual = digest(path.read_bytes().replace(b'\r\n', b'\n')) if path.is_file() else None
        if row['before_sha256'] != actual:
            raise ValueError('Native source base differs at ' + row['path'])
    return {'verified_base_paths':len(rows)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('repo','work'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--core', type=Path)
    parser.add_argument('--windows', type=Path)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument('--unpack-with', type=Path, metavar='BSDTAR')
    operation.add_argument('--verify-base', action='store_true')
    args = parser.parse_args()
    if args.unpack_with:
        print(json.dumps(unpack_windows_source(args.repo, args.work, args.unpack_with)))
    elif args.verify_base:
        print(json.dumps(verify_base(args.repo, args.work)))
    elif not (args.core and args.windows):
        parser.error('Selection requires both pinned tooling directories')
    else:
        print(json.dumps(render(args.repo, args.work, args.core, args.windows)))
