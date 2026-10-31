"""Preserve every accepted patch while authenticating the new Windows pin pair."""
import json
from pathlib import Path
import shutil
import os
import subprocess

import pytest

from tools import patch_selection as s
from tools.tests.test_patch_selection import clone_repo, set_platform_pins

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def repo156(tmp_path):
    repo = clone_repo(tmp_path)
    for name in (s.WINDOWS156_BASELINE, s.WINDOWS156_OVERRIDES):
        shutil.copy2(ROOT / name, repo / name)
    set_platform_pins(repo, 'windows', s.VERSION156, s.CORE156, s.WINDOWS156)
    return repo


def test_preserves_all_original_bytes_and_order(repo156):
    identity, patches = s.select(repo156, 'windows')
    base = json.loads((repo156 / s.WINDOWS156_BASELINE).read_text())
    ports = json.loads((repo156 / s.WINDOWS156_OVERRIDES).read_text())
    mapping = {e['base_path']: e['path'] for e in ports['overrides']}
    assert len(patches) == 231
    assert [p for p, _ in patches] == [mapping.get(e['path'], e['path']) for e in base['patches']]
    assert identity['selection']['version'] == s.VERSION156
    for entry in base['patches']:
        assert s.digest((repo156 / entry['path']).read_bytes()) == entry['sha256']


@pytest.mark.parametrize('kind', ['baseline', 'ports', 'original', 'port', 'series', 'extra'])
def test_tampering_or_incomplete_inventory_rejected(repo156, kind):
    base = json.loads((repo156 / s.WINDOWS156_BASELINE).read_text())
    ports = json.loads((repo156 / s.WINDOWS156_OVERRIDES).read_text())
    name = {'baseline': s.WINDOWS156_BASELINE, 'ports': s.WINDOWS156_OVERRIDES,
            'original': base['patches'][0]['path'], 'port': ports['overrides'][0]['path'],
            'series': 'patches/series', 'extra': 'patches/chromium156-windows/unreviewed.patch'}[kind]
    p = repo156 / name
    p.write_bytes((p.read_bytes() if p.exists() else b'') + b'\nchanged\n')
    with pytest.raises(s.SelectionError):
        s.select(repo156, 'windows')


def test_wrong_core_pin_rejected(repo156):
    set_platform_pins(repo156, 'windows', s.VERSION156, '0'*40, s.WINDOWS156)
    with pytest.raises(s.SelectionError, match='pin pair'):
        s.select(repo156, 'windows')


def test_wrong_source_version_rejected(repo156, tmp_path):
    src = tmp_path / 'source'; (src / 'chrome').mkdir(parents=True)
    (src / 'chrome/VERSION').write_text('MAJOR=155\nMINOR=0\nBUILD=8059\nPATCH=39\n')
    with pytest.raises(s.SelectionError, match='source version'):
        s.select(repo156, 'windows', src=src)


def test_prior_windows155_stack_still_supported(repo156):
    set_platform_pins(repo156, 'windows', s.VERSION155, s.CORE155, s.PLATFORM155_COMMITS['windows'])
    identity, patches = s.select(repo156, 'windows')
    assert len(patches) == 231
    assert identity['selection']['version'] == s.VERSION155
    assert all('chromium156' not in p for p, _ in patches)


@pytest.mark.skipif(os.name != 'nt', reason='Windows directory junction check')
def test_windows_junction_cannot_redirect_reviewed_ports(repo156, tmp_path):
    ports = repo156 / 'patches/chromium156-windows'
    target = tmp_path / 'owned-port-target'
    shutil.copytree(ports, target)
    assert ports.resolve().is_relative_to(tmp_path.resolve())
    shutil.rmtree(ports)
    env = {**os.environ, 'PORT_JUNCTION': str(ports), 'PORT_TARGET': str(target)}
    subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                    'New-Item -ItemType Junction -Path $env:PORT_JUNCTION -Target $env:PORT_TARGET | Out-Null'],
                   env=env, check=True, capture_output=True)
    try:
        with pytest.raises(s.SelectionError, match='linked|symlink'):
            s.select(repo156, 'windows')
        assert list(target.glob('*.patch')), 'Target must remain untouched'
    finally:
        assert ports.is_junction() and ports.parent.resolve().is_relative_to(tmp_path.resolve())
        ports.rmdir()
