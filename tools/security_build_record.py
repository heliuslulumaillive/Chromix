"""Credential-free, exact build-input evidence for the isolated Windows 156 lane."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re

VERSION = '156.0.8078.79'
VARIANT = 'security-win156'
CORE = '28a211ab4ab6b83d919966a4f58a6df23cf4fb29'
WINDOWS = '8492884eb30fdd9417ea7816aa92def805c97c74'
REQUIRED_FLAGS = {'safe_browsing_mode': '1', 'is_official_build': 'true',
                  'is_debug': 'false', 'is_component_build': 'false',
                  'target_cpu': '"x64"', 'target_os': '"win"'}
SENSITIVE = re.compile(r'api_?key|client_?(?:secret|id)|password|credential|access_?token|private_?key', re.I)


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plain(path: Path) -> Path:
    absolute = path.absolute()
    for entry in (absolute, *absolute.parents):
        if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
            raise ValueError('Linked build input is forbidden: ' + str(entry))
    return absolute


def recipe(repo: Path) -> str:
    # Include every patch input, not just the generated service aggregate.
    files = list((repo / 'patches').rglob('*'))
    files += list((repo / 'build/security-resources').rglob('*'))
    files += list((repo / 'build').glob('patch-*.json'))
    files += [repo / name for name in (
        'build/ungoogled-revisions.psd1', 'CHROMIUM_WINDOWS_VERSION',
        'UNGOOGLED_WINDOWS_VERSION', 'build/args.windows.gn', 'build/args.security.gn',
        'build/google-services-win155.json', 'tools/prepare_google_services.py',
        'build/fingerprint-modes-capabilities.json', 'build/gpu-binding-capabilities.json',
        'tools/patch_selection.py', 'tools/restored_source_resources.py',
        'tools/build_google_services_patch.py', 'tools/verify_google_services_source.py',
        'build/windows/prepare-ungoogled.ps1')]
    entries = {p.relative_to(repo).as_posix(): sha256(plain(p))
               for p in sorted(set(files)) if p.is_file()}
    for name in ('build/args.security.gn', 'build/google-services-win155.json'):
        if name not in entries:
            raise ValueError('Missing security recipe input: ' + name)
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def inspect_gn(path: Path) -> dict:
    raw = plain(path).read_bytes()
    text = raw.decode('utf-8-sig')
    flags = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*(?:#.*)?', stripped)
        if not match:
            raise ValueError('Only literal GN assignments may enter the build record')
        key, value = match.groups()
        if key in flags:
            raise ValueError('Duplicate GN assignment: ' + key)
        if SENSITIVE.search(key) and value not in ('""', "''", 'false'):
            raise ValueError('Nonempty credential GN assignment refused; value not logged')
        # Keep exact text only after rejecting secrets and executable GN expressions.
        if not re.fullmatch(r'true|false|-?\d+|"[^"\r\n]*"|\[\s*\]', value):
            raise ValueError('Nonliteral GN assignment refused: ' + key)
        flags[key] = value
    for key, expected in REQUIRED_FLAGS.items():
        if flags.get(key) != expected:
            raise ValueError('Required security GN value mismatch: ' + key)
    for key in ('is_asan', 'is_tsan', 'is_msan'):
        if flags.get(key) == 'true':
            raise ValueError('Diagnostic sanitizer build is not this release lane: ' + key)
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
            'content': text, 'assignments': flags, 'credential_values_present': False}


def write_record(repo: Path, work: Path, destination: Path) -> dict:
    src = work / 'src'
    fields = dict(re.findall(r'(?m)^(MAJOR|MINOR|BUILD|PATCH)=(\d+)\s*$',
                             (src / 'chrome/VERSION').read_text()))
    if '.'.join(fields.get(k, '') for k in ('MAJOR', 'MINOR', 'BUILD', 'PATCH')) != VERSION:
        raise ValueError('Prepared Chromium version mismatch')
    receipt = work / 'google-services-source.json'
    metadata = json.loads(receipt.read_text(encoding='utf-8-sig'))
    if metadata.get('chromium_version') != VERSION or metadata.get('build_variant') != 'fingerprint-modes-win156':
        raise ValueError('Google/fingerprint restoration has not completed for this version')
    gn = inspect_gn(src / 'out/Default/args.gn')
    tools = {}
    for relative in ('out/Default/gn.exe', 'third_party/ninja/ninja.exe',
                     'third_party/node/win/node.exe',
                     'third_party/llvm-build/Release+Asserts/bin/clang-cl.exe',
                     'third_party/rust-toolchain/bin/rustc.exe'):
        path = src / relative
        if path.is_file():
            tools[relative] = {'bytes': path.stat().st_size, 'sha256': sha256(plain(path))}
    record = {'schema_version': 1, 'build_variant': VARIANT, 'chromium_version': VERSION,
              'payload_build_variant': 'fingerprint-modes-win156',
              'core_commit': CORE, 'windows_commit': WINDOWS,
              'source_commit': os.environ.get('GITHUB_SHA', ''),
              'run_id': os.environ.get('GITHUB_RUN_ID', ''),
              'recipe_sha256': recipe(repo), 'source_receipt_sha256': sha256(receipt),
              'gn': gn, 'tools': tools,
              'security_acceptance': 'Build configuration evidence only; runtime acceptance is separate.'}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = write_record(args.repo, args.work, args.output)
    print(json.dumps({'gn_sha256': result['gn']['sha256'],
                      'recipe_sha256': result['recipe_sha256']}))


if __name__ == '__main__':
    main()
