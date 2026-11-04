"""Validate every legacy restoration rule against reviewed upstream hunks.

The committed manifest is reviewed input. --candidate prints a proposed manifest
for review; neither command edits the recipe, upstream cache or browser source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

MANIFEST = 'build/restoration-rules-windows156.json'
CONFIG = 'build/google-services-win155.json'
HEADER = re.compile(r'^--- (a/[^\r\n]+|/dev/null)\r?\n\+\+\+ (b/[^\r\n]+|/dev/null)\r?\n', re.M)
HUNK = re.compile(r'^@@ [^\n]+\n', re.M)
RULE_KEYS = ('remove_patches', 'restore_sections', 'restore_hunks', 'restore_windows_sections')


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sections(data: str) -> dict[str, str]:
    result = {}
    matches = list(HEADER.finditer(data))
    for index, match in enumerate(matches):
        name = match[2][2:] if match[2] != '/dev/null' else match[1][2:]
        if name in result:
            raise ValueError('Duplicate upstream patch section: ' + name)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(data)
        result[name] = re.split(r'^diff --git ', data[match.start():end], flags=re.M)[0]
    if not result:
        raise ValueError('Upstream restoration patch has no unified sections')
    return result


def hunks(section: str) -> list[str]:
    matches = list(HUNK.finditer(section))
    if not matches:
        raise ValueError('Restoration section has no unified hunks')
    return [section[match.start():matches[index + 1].start() if index + 1 < len(matches) else len(section)]
            for index, match in enumerate(matches)]


def marker_matches(section: str, markers: list[str]) -> list[list[str]]:
    if (not isinstance(markers, list) or not markers
            or any(not isinstance(marker, str) or not marker for marker in markers)
            or len(markers) != len(set(markers))):
        raise ValueError('Restoration markers must be nonempty unique strings')
    hits = []
    for hunk in hunks(section):
        changes = '\n'.join(line[1:] for line in hunk.splitlines() if line.startswith(('+', '-')))
        hits.append([marker for marker in markers if marker in changes])
    missing = [marker for marker in markers if not any(marker in matched for matched in hits)]
    if missing:
        raise ValueError('Restoration marker did not match changed lines: ' + ', '.join(missing))
    return hits


def retain_hunks(section: str, markers: list[str]) -> str:
    """Filter only after the caller validates the complete pinned rule manifest."""
    parts = hunks(section)
    hits = marker_matches(section, markers)
    kept = [part for part, matched in zip(parts, hits, strict=True) if not matched]
    return section[:HUNK.search(section).start()] + ''.join(kept) if kept else ''


def patch_series(root: Path) -> list[tuple[str, str]]:
    root = root.resolve()
    entries = []
    seen = set()
    for line in (root / 'patches/series').read_text(encoding='utf-8').splitlines():
        name = line.split('#', 1)[0].strip()
        if not name:
            continue
        path = root / 'patches' / name
        if (name in seen or '\\' in name or ':' in name or name.startswith('/')
                or any(part in ('', '.', '..') for part in name.split('/'))
                or any(part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction())
                       for part in (path, *path.parents))):
            raise ValueError('Unsafe or duplicate upstream series entry: ' + name)
        seen.add(name)
        entries.append((name, path.read_text(encoding='utf-8')))
    if not entries:
        raise ValueError('Empty upstream patch series')
    return entries


def inventory(config: dict, core, windows) -> dict:
    layers = {}
    for name, entries in (('core', core), ('windows', windows)):
        layers[name] = dict(entries)
        if len(layers[name]) != len(entries):
            raise ValueError('Duplicate ' + name + ' patch series entry')
    removed = config.get('remove_patches', [])
    if not isinstance(removed, list) or len(removed) != len(set(removed)):
        raise ValueError('Restoration remove_patches must be an ordered unique list')
    rows, patch_ids = [], {}

    def patch(layer, name):
        if not isinstance(name, str) or name not in layers[layer]:
            raise ValueError('Restoration patch rule did not match upstream: ' + str(name))
        data = layers[layer][name]
        patch_ids[layer + '/' + name] = digest(data)
        return sections(data)

    for name in removed:
        mapped = patch('core', name)
        hashes = [digest(part) for text in mapped.values() for part in hunks(text)]
        rows.append({'kind': 'remove_patch', 'patch': name, 'section_count': len(mapped),
                     'hunk_count': len(hashes), 'hunks_sha256': hashes})
    for key, layer in (('restore_sections', 'core'), ('restore_hunks', 'core'),
                       ('restore_windows_sections', 'windows')):
        rules = config.get(key, {})
        if not isinstance(rules, dict):
            raise ValueError('Restoration rule map must be an object: ' + key)
        for name, paths in rules.items():
            if layer == 'core' and name in removed:
                raise ValueError('Section rule overlaps a fully removed patch: ' + name)
            mapped = patch(layer, name)
            if not isinstance(paths, dict if key == 'restore_hunks' else list) or not paths:
                raise ValueError('Empty or malformed restoration paths: ' + name)
            if len(paths) != len(set(paths)):
                raise ValueError('Duplicate restoration section rule: ' + name)
            for path in paths:
                if path not in mapped:
                    raise ValueError('Restoration section did not match upstream: ' + name + ': ' + path)
                if key == 'restore_hunks' and path in config.get('restore_sections', {}).get(name, []):
                    raise ValueError('Hunk rule overlaps a full section rule: ' + path)
                section = mapped[path]
                parts = hunks(section)
                row = {'kind': key, 'patch': name, 'path': path, 'section_sha256': digest(section),
                       'hunk_count': len(parts), 'hunks_sha256': [digest(part) for part in parts]}
                if key == 'restore_hunks':
                    hits = marker_matches(section, paths[path])
                    row['markers'] = paths[path]
                    row['marker_hunk_counts'] = {marker: sum(marker in matched for matched in hits)
                                                 for marker in paths[path]}
                    row['restored_hunks_sha256'] = [digest(part) for part, matched in zip(parts, hits, strict=True)
                                                     if matched]
                rows.append(row)
    rules = {key: config.get(key, [] if key == 'remove_patches' else {}) for key in RULE_KEYS}
    return {'schema_version': 1, 'chromium_version': config['chromium_version'],
            'core_commit': config['core_commit'], 'windows_commit': config['windows_commit'],
            'upstream_patch_order': {layer: list(entries) for layer, entries in layers.items()},
            'rules_sha256': digest(json.dumps(rules, sort_keys=True, separators=(',', ':'))),
            'upstream_patch_sha256': patch_ids, 'rules': rows}


def validate(repo: Path, config: dict, core, windows) -> dict:
    observed = inventory(config, core, windows)
    expected = json.loads((repo / MANIFEST).read_text(encoding='utf-8'))
    if observed != expected:
        raise ValueError('Restoration rule/hunk identity changed; review a new candidate manifest before replay')
    return observed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--windows', type=Path, required=True)
    parser.add_argument('--candidate', action='store_true', help='Print unapproved proposed rule identities only')
    args = parser.parse_args(argv)
    try:
        config = json.loads((args.repo / CONFIG).read_text(encoding='utf-8'))
        core, windows = patch_series(args.core), patch_series(args.windows)
        result = inventory(config, core, windows) if args.candidate else validate(args.repo, config, core, windows)
        if args.candidate:
            print(json.dumps(result, indent=2))
        else:
            print(json.dumps({'status': 'verified', 'rules': len(result['rules']),
                              'upstream_patches': len(result['upstream_patch_sha256'])}))
    except (OSError, ValueError) as error:
        print('restoration rules: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
