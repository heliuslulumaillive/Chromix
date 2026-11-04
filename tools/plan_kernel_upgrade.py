"""Read-only upgrade readiness: validate an already reviewed platform recipe.

This command never edits pins, downloads sources, changes caches or compiles.
A newer version requires a reviewed recipe and patch port before it can pass.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

try:
    from .platform_pins import PLATFORMS, load_pins
    from .patch_selection import select
except ImportError:
    from platform_pins import PLATFORMS, load_pins
    from patch_selection import select


def plan(repo: Path, version: str, platform: str = 'windows', *, profile: str = 'lite',
         src: Path | None = None, core: Path | None = None) -> dict:
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){3}', version):
        raise ValueError('Use an exact four-part Chromium version; latest is not an immutable build input')
    if profile == 'lite' and platform != 'windows':
        raise ValueError('The Lite product recipe is currently reviewed only for Windows x64')
    pins = load_pins(repo, platform)
    result = {'schema_version': 1, 'platform': platform, 'profile': profile,
              'requested_version': version, 'pinned_version': pins['ChromiumVersion'],
              'core_commit': pins['UngoogledCommit'],
              'platform_commit': pins['Ungoogled' + PLATFORMS[platform] + 'Commit'],
              'read_only': True, 'compiled': False, 'runtime_verified': False}
    if version != pins['ChromiumVersion']:
        result.update(status='port-required', reason='The requested version has no active reviewed recipe. '
                      'Review the Chromium/core/platform pins, port the product patches, and verify source equivalence first.')
        return result
    identity, patches = select(repo, platform, src=src, core=core, profile=profile)
    result.update(status='recipe-verified', patch_count=len(patches), selection=identity,
                  qualification='Recipe identity only; clean source replay, hosted build and actual artifact acceptance remain required.')
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--version', required=True)
    parser.add_argument('--platform', choices=tuple(PLATFORMS), default='windows')
    parser.add_argument('--profile', choices=('lite', 'legacy'), default='lite')
    parser.add_argument('--src', type=Path)
    parser.add_argument('--core', type=Path)
    args = parser.parse_args(argv)
    try:
        result = plan(args.repo, args.version, args.platform, profile=args.profile, src=args.src, core=args.core)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError) as error:
        print('upgrade readiness: ' + str(error), file=sys.stderr)
        return 1
    return 0 if result['status'] == 'recipe-verified' else 2


if __name__ == '__main__':
    raise SystemExit(main())
