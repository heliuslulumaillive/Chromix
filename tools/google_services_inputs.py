"""Content-bound aggregate inputs and read-only reuse of pinned source caches."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

HEX = re.compile(r'[a-f0-9]{64}\Z')
GENERATOR_INPUTS = (
    'build/google-services-win155.json', 'build/ungoogled-revisions.psd1',
    'CHROMIUM_WINDOWS_VERSION', 'UNGOOGLED_WINDOWS_VERSION', 'patches/series',
    'tools/build_google_services_patch.py', 'tools/google_services_inputs.py',
    'tools/patch_selection.py', 'tools/platform_pins.py',
    'tools/restored_source_resources.py',
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def plain(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
            raise ValueError('Source input or output cannot use a redirected path: ' + str(candidate))
    return path


def relative_path(name: str) -> str:
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or any(ord(c) < 32 for c in name)
            or PurePosixPath(name).is_absolute()
            or any(p in ('', '.', '..') for p in name.split('/'))):
        raise ValueError('Unsafe source input path')
    return name


def owned(root: Path, name: str) -> Path:
    return plain(plain(root).joinpath(*relative_path(name).split('/')))


def write_if_changed(path: Path, data: bytes) -> bool:
    """Atomic writes; verified identical bytes retain their original timestamp."""
    path = plain(path)
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        plain(path)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def native_browser_input_names(config: dict) -> tuple[str, ...]:
    if 'native_browser' not in config:
        return ()
    expected = {'version': 1, 'capabilities': 'build/native-browser-capabilities.json',
                'source_gate_required': True}
    if (config['native_browser'] != expected
            or type(config['native_browser']['version']) is not int
            or config['native_browser']['source_gate_required'] is not True):
        raise ValueError('Native browser source/capability contract is invalid')
    return (expected['capabilities'], 'tools/verify_native_browser_source.py',
            'tools/verify_native_environment_controls_source.py',
            'tools/verify_history_retention_source.py', 'tools/build_history_retention_patch.py',
            'tools/verify_translation_init_source.py', 'tools/build_translation_init_patch.py',
            'tools/build_lite_retirement_patch.py')


def aggregate_inputs(repo: Path, config: dict | None = None, selection=None) -> dict:
    """Do not include the generated aggregate/receipt, which would be circular."""
    try:
        from .patch_selection import select
    except ImportError:
        from patch_selection import select
    if config is None:
        config = json.loads(owned(repo, 'build/google-services-win155.json').read_text('utf-8'))
    if selection is None:
        selection, _ = select(repo, 'windows')
    overlays = config.get('restoration_overlays', [])
    if not isinstance(overlays, list) or len(overlays) != len(set(overlays)):
        raise ValueError('Restoration overlays must be an ordered unique list')
    names = set(GENERATOR_INPUTS)
    names.update(native_browser_input_names(config))
    names.update(relative_path(name) for name in overlays)
    names.update(relative_path(item['path']) for item in selection['patches'])
    names.update(relative_path(item['repository_path'])
                 for item in config.get('restore_pruned_resources', {}).values())
    files = {name: sha256(owned(repo, name).read_bytes()) for name in sorted(names)}
    return {'schema_version': 1, 'chromium_version': config['chromium_version'],
            'core_commit': config['core_commit'], 'windows_commit': config['windows_commit'],
            'ordered_overlays': overlays, 'selected_baseline': selection, 'files': files}


def verify_aggregate_inputs(repo: Path, report: dict, config: dict | None = None) -> dict:
    expected = aggregate_inputs(repo, config)
    if (report.get('generation_inputs') != expected
            or report.get('generation_inputs_sha256') != identity(expected)):
        raise ValueError('Restoration aggregate is stale or lacks bound generation inputs; regenerate it from the current config, overlays and generator')
    return expected


def preparation_input_hashes(repo: Path) -> dict:
    """One inventory for source preparation and packaged-candidate admission."""
    try:
        from .restored_source_resources import resources
    except ImportError:
        from restored_source_resources import resources
    config = json.loads(owned(repo, 'build/google-services-win155.json').read_text('utf-8'))
    resources(repo, config)
    names = {
        'tools/prepare_google_services.py', 'tools/verify_google_services_source.py',
        'tools/restored_source_resources.py', 'tools/google_services_inputs.py',
        'tools/build_google_services_patch.py', 'build/google-services-win155.json',
        'patches/google-services/0001-restore-google-services.patch',
        'patches/google-services/0001-restore-google-services.json',
        'patches/google-services/0002-windows-native-passkey-discovery.patch',
        'patches/chromium155-windows/0233-renderer-timezone-webstring-api.patch',
    }
    overlays = config.get('restoration_overlays', [])
    names.update(native_browser_input_names(config))
    names.update(relative_path(name) for name in overlays)
    names.update(relative_path(spec['repository_path'])
                 for spec in config.get('restore_pruned_resources', {}).values())
    if config.get('security_restoration'):
        names.add('build/args.security.gn')
    if config.get('lite_retirement'):
        names.update(('tools/lite_retirement.py', 'patches/lite-win156/retirement-manifest.json'))
    if any(name.startswith(('patches/fingerprint-modes/', 'patches/fingerprint-modes-win156/'))
           for name in overlays):
        names.add('build/fingerprint-modes-capabilities.json')
    if owned(repo, 'build/gpu-binding-capabilities.json').is_file():
        names.add('build/gpu-binding-capabilities.json')
    if 'patches/fingerprint-modes/0011-pixel-semantic-preservation.patch' in overlays:
        names.update('tools/tests/fixtures/pixel-semantics-v1/' + name
                     for name in ('cases.tsv', 'pixels.bin', 'provenance.json'))
    return {name: sha256(owned(repo, name).read_bytes()) for name in sorted(names)}


class PinnedSourceCache:
    """Receipts authorize individual bytes, never arbitrary existing cache files.

    Cache directories are read-only. The generated work tree is a separate output;
    missing/unlisted inputs are fetched from the caller's fixed upstream URL.
    """

    def __init__(self, version: str, caches=()):
        self.entries = {}
        self.deps_hash = None
        self.roots = []
        for directory, receipt in caches:
            root = plain(Path(directory))
            self.roots.append(root)
            report = json.loads(plain(Path(receipt)).read_text(encoding='utf-8-sig'))
            if report.get('version') != version:
                raise ValueError('Source cache receipt belongs to another Chromium version')
            rows = report.get('source_inputs', report.get('files'))
            if not isinstance(rows, list) or not rows:
                raise ValueError('Source cache receipt needs exact source_inputs')
            seen = set()
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError('Invalid source cache entry')
                name = relative_path(row.get('path'))
                digest = row.get('sha256')
                if name in seen or not HEX.fullmatch(str(digest)):
                    raise ValueError('Duplicate or unhashed source cache entry: ' + name)
                seen.add(name)
                prior = self.entries.setdefault(name, [])
                if prior and prior[0][1]['sha256'] != digest:
                    raise ValueError('Source cache receipts disagree for: ' + name)
                prior.append((root, row))
            deps_hashes = {pin.get('chromium_deps_sha256')
                           for pin in report.get('external_source_pins', {}).values()}
            if report.get('chromium_deps_sha256') is not None:
                deps_hashes.add(report['chromium_deps_sha256'])
            for digest in deps_hashes:
                if not HEX.fullmatch(str(digest)) or self.deps_hash not in (None, digest):
                    raise ValueError('Source cache receipts disagree on pinned Chromium DEPS')
                self.deps_hash = digest

    def _records(self, name, external):
        records = self.entries.get(relative_path(name), [])
        for _, row in records:
            actual = (row.get('upstream_repository'), row.get('upstream_revision'))
            expected = (external['repository'], external['revision']) if external else (None, None)
            if actual != expected:
                raise ValueError('Cached source has a different upstream revision: ' + name)
        return records

    def materialize(self, root: Path, name: str, download, *, external=None) -> bytes:
        records = self._records(name, external)
        target = owned(root, name)
        self._writable_output(target)
        expected = records[0][1]['sha256'] if records else None
        if target.is_file() and expected:
            data = target.read_bytes()
            if sha256(data) != expected:
                raise ValueError('Existing pristine source differs from its receipt: ' + name)
            return data
        for cache_root, row in records:
            source = owned(cache_root, name)
            if not source.is_file():
                continue
            data = source.read_bytes()
            if sha256(data) != row['sha256']:
                raise ValueError('Cached source differs from its receipt: ' + name)
            write_if_changed(target, data)
            return data
        data = download()
        if expected and sha256(data) != expected:
            raise ValueError('Downloaded source differs from its pinned receipt: ' + name)
        write_if_changed(target, data)
        return data

    def _writable_output(self, target: Path):
        if any(plain(target).is_relative_to(root) for root in self.roots):
            raise ValueError('Source cache is read-only; choose a separate pristine output directory')

    def deps(self, target: Path, download, cached: Path | None = None) -> bytes:
        self._writable_output(target)
        if cached is not None:
            if not self.deps_hash:
                raise ValueError('DEPS cache needs a receipt with its exact SHA256')
            data = plain(cached).read_bytes()
            if sha256(data) != self.deps_hash:
                raise ValueError('Cached DEPS differs from the pinned receipt')
        elif plain(target).is_file() and self.deps_hash:
            data = target.read_bytes()
            if sha256(data) != self.deps_hash:
                raise ValueError('Existing DEPS differs from the pinned receipt')
        else:
            data = download()
            if self.deps_hash and sha256(data) != self.deps_hash:
                raise ValueError('Downloaded DEPS differs from the pinned receipt')
        write_if_changed(target, data)
        return data
