"""Compile the real graphics helper and historical/invariant tests on Windows."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import tempfile

from build_google_services_patch import sections

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    wanted = {'base/uxr_graphics_noise.h', 'base/uxr_graphics_noise.cc',
              'base/uxr_surface_modes.h', 'base/uxr_pixel_noise.h',
              'base/uxr_graphics_mode_goldens.cc'}
    with tempfile.TemporaryDirectory(prefix='chromix-shard-v2-') as directory:
        root = Path(directory)
        for name in ['patches/0221-pixel-noise-shared-helper.patch',
                     'patches/fingerprint-modes/0001-independent-mode-contract.patch',
                     'patches/fingerprint-modes/0002-graphics.patch']:
            for path, created, data in sections((ROOT / name).read_text(encoding='utf-8')):
                if path in wanted:
                    assert created, path
                    text = ''.join(line[1:] for line in data.splitlines(keepends=True)
                                   if line.startswith('+') and not line.startswith('+++'))
                    dst = root / path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_text(text, encoding='utf-8', newline='\n')
        overlay = ROOT / 'patches/fingerprint-modes/0009-shardx-webgl-consistent-readback.patch'
        chosen = ''.join(text for name, _, text in sections(overlay.read_text(encoding='utf-8'))
                         if name.startswith('base/'))
        patch = root / 'base.patch'
        patch.write_text(chosen, encoding='utf-8', newline='\n')
        subprocess.run(['C:/Program Files/Git/usr/bin/patch.exe', '-p1', '--batch', '--fuzz=0',
                        '-i', str(patch)], cwd=root, check=True, capture_output=True)
        (root / 'base/base_export.h').write_text('#pragma once\n#define BASE_EXPORT\n')
        (root / 'base/containers').mkdir()
        (root / 'base/containers/span.h').write_text(
            '#pragma once\n#include <span>\nnamespace base { using std::span; }\n')
        compiler = shutil.which('clang++') or 'C:/Program Files/LLVM/bin/clang++.exe'
        environment = dict(os.environ)
        if os.name == 'nt':
            vcvars = 'C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat'
            setup = root / 'compiler-environment.cmd'
            setup.write_text(f'@echo off\ncall "{vcvars}" >nul\nif errorlevel 1 exit /b 1\nset\n')
            result = subprocess.run(['cmd.exe', '/d', '/c', str(setup)],
                                    capture_output=True, text=True, check=True)
            for line in result.stdout.splitlines():
                if '=' in line:
                    key, value = line.split('=', 1)
                    environment[key] = value
        executable = root / 'graphics.exe'
        command = [compiler, '-std=c++20', '-O2', '-DUXR_GRAPHICS_STANDALONE',
                   '-I' + str(root), str(root / 'base/uxr_graphics_noise.cc'),
                   str(root / 'base/uxr_graphics_mode_goldens.cc'), '-o', str(executable)]
        built = subprocess.run(command, env=environment, capture_output=True, text=True)
        if built.returncode:
            print(built.stdout, built.stderr)
            raise SystemExit(built.returncode)
        run = subprocess.run([str(executable), str(ROOT / 'tools/tests/fixtures')],
                             capture_output=True, text=True)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report = {'status': 'passed' if run.returncode == 0 else 'failed',
                  'compiler': compiler, 'exitCode': run.returncode,
                  'stdout': run.stdout, 'stderr': run.stderr,
                  'scope': 'Real production graphics helper plus historical byte fixtures and V2 invariants; GL integration requires the built browser.'}
        args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(run.stdout, run.stderr)
        raise SystemExit(run.returncode)


if __name__ == '__main__':
    main()
