"""Verify the complete ungoogled Windows 158 source port without compiling it.

This source-only lane does not modify the production recipe, checkpoint admission,
capability receipts, DLL allowlists or installed runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

try:
    from . import restored_source_resources, verify_google_services_source as services
    from .google_services_inputs import owned, plain, write_if_changed
    from .platform_pins import load_pins
    from .verify_browser_feature_port import verify_signin
    from .verify_browser_services158 import verify as verify_services, service_requirements, verify_popup, verify_full_feature_links
    from .verify_history_retention_source import verify as verify_history
    from .verify_native_browser_source import body, compact, read, require, verify_shell
    from .verify_native_cookie_source import verify_implementation as verify_cookie
    from .verify_native_environment_controls_source import verify as verify_controls
    from .verify_translation_init_source import verify as verify_translation
except ImportError:
    import restored_source_resources
    import verify_google_services_source as services
    from google_services_inputs import owned, plain, write_if_changed
    from platform_pins import load_pins
    from verify_browser_feature_port import verify_signin
    from verify_browser_services158 import verify as verify_services, service_requirements, verify_popup, verify_full_feature_links
    from verify_history_retention_source import verify as verify_history
    from verify_native_browser_source import body, compact, read, require, verify_shell
    from verify_native_cookie_source import verify_implementation as verify_cookie
    from verify_native_environment_controls_source import verify as verify_controls
    from verify_translation_init_source import verify as verify_translation

MANIFEST = 'build/windows158-product-port.json'
RELEASE = 'build/lite-windows157.json'
FEATURES = 'build/windows158-browser-feature-port.json'
PATCH_FLAGS = ['-p1', '--batch', '--fuzz=0', '--binary', '--get=0',
               '--no-backup-if-mismatch', '--reject-file=-']
HEX = re.compile(r'[0-9a-f]{64}\Z')


def digest(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def source(tree: Path, name: str, binaries=()) -> bytes | None:
    path = owned(tree, name)
    if path.exists() and not path.is_file():
        raise ValueError('Expected a regular source file: ' + name)
    raw = path.read_bytes() if path.is_file() else None
    return raw.replace(b'\r\n', b'\n') if raw is not None and name not in binaries else raw


def load(repo: Path) -> dict:
    manifest = json.loads(owned(repo, MANIFEST).read_bytes())
    payload = {k: v for k, v in manifest.items() if k != 'manifestSha256'}
    if (manifest.get('schemaVersion') != 1 or manifest.get('kind') != 'ungoogled-windows158-source-port'
            or manifest.get('manifestSha256') != digest(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode())
            or manifest.get('chromiumVersion') != '158.0.8127.36'
            or any(manifest.get(k) is not False for k in ('compiled', 'runtimeAccepted', 'installed'))):
        raise ValueError('Invalid source-only 158 manifest')
    pins = load_pins(repo, 'windows')
    for key, pin in [('chromiumVersion', 'ChromiumVersion'), ('coreCommit', 'UngoogledCommit'),
                     ('windowsCommit', 'UngoogledWindowsCommit')]:
        if manifest[key] != pins[pin]:
            raise ValueError('158 source manifest differs from the platform pins: ' + key)
    for name, field in [(RELEASE, 'releaseManifestSha256'), (FEATURES, 'browserFeatureManifestSha256')]:
        if digest(owned(repo, name).read_bytes()) != manifest[field]:
            raise ValueError('Changed source lineage: ' + name)
    release = json.loads(owned(repo, RELEASE).read_bytes())
    features = json.loads(owned(repo, FEATURES).read_bytes())
    if manifest['releaseSource'] != features['releaseSource'] or manifest['releaseSource'] != '230b7f6b631a9bbc72884786951bbe4d5dc2340d':
        raise ValueError('Wrong release ancestor')
    if manifest['restore_pruned_resources'] != release['runtime']['restore_pruned_resources']:
        raise ValueError('Changed native resource contract')
    originals = {row['path']: row for row in release['patches']}
    for name, row in originals.items():
        if digest(owned(repo, name).read_bytes()) != row['sha256']:
            raise ValueError('Changed 157 release input: ' + name)
    groups = manifest['groups']
    if len(groups) != 26 or {g['releasePatch'] for g in groups} != set(originals):
        raise ValueError('The port must cover all 26 original product topics')
    targets, patch_paths = set(), set()
    for group in groups:
        original = originals[group['releasePatch']]
        raw = owned(repo, group['path']).read_bytes()
        actual = [n.decode() for n in re.findall(rb'^diff --git a/(\S+) b/\S+$', raw, re.M)]
        if (group['releaseSha256'] != original['sha256'] or digest(raw) != group['sha256']
                or actual != group['files'] or not actual or len(actual) != len(set(actual))
                or not set(actual).issubset(original['files']) or targets.intersection(actual)
                or group['path'] in patch_paths):
            raise ValueError('Invalid or overlapping product group: ' + group['path'])
        targets.update(actual)
        patch_paths.add(group['path'])
    generated = {p.relative_to(repo).as_posix() for p in owned(repo, 'patches/lite-windows158-complete').glob('*.patch')}
    if generated != {n for n in patch_paths if n.startswith('patches/lite-windows158-complete/')}:
        raise ValueError('Unexpected complete-port patch inventory')
    rows = manifest['sourceFiles']
    names = [r['path'] for r in rows]
    expected = {r['path'] for r in release['source_files']} | set(features['supportFiles'])
    if len(names) != len(set(names)) or set(names) != expected:
        raise ValueError('The complete port has missing or duplicate source paths')
    for row in rows:
        owned(repo, row['path'])
        if any(row[k] is not None and not HEX.fullmatch(row[k]) for k in ('before', 'after')):
            raise ValueError('Invalid source identity: ' + row['path'])
    changed = {r['path'] for r in rows if r['before'] != r['after']}
    if targets != changed - set(manifest['restore_pruned_resources']):
        raise ValueError('Patches do not cover the declared source changes')
    row_map = {r['path']: r for r in rows}
    manual = manifest['manualResolutions']
    expected_manual = {
        'third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d.h',
        'third_party/blink/renderer/modules/webaudio/audio_handler.cc',
        'third_party/blink/renderer/modules/webaudio/offline_audio_context.cc',
        'third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc',
    }
    if set(manual) != expected_manual or any(manual[n] != {k: row_map[n][k] for k in ('before', 'after')} for n in manual):
        raise ValueError('Manual resolution identities differ from the source inventory')
    return manifest


def verify_rendering_adaptations(src: Path) -> list[str]:
    canvas = 'third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d'
    header = read(src, canvas + '.h')
    require(header, ['void ResetInternal() override;', 'UxrCanvasInputs() const override;'], canvas + '.h')
    if 'voidReset()override;' in compact(header):
        raise ValueError('Canvas still declares the removed 157 Reset override')
    reset = compact(body(read(src, canvas + '.cc'), 'BaseRenderingContext2D::ResetInternal'))
    if not reset.startswith('uxr_canvas_inputs_.reset();Canvas2DRecorderContext::ResetInternal();'):
        raise ValueError('Canvas reset must clear the input mask before the 158 recorder reset')
    audio = 'third_party/blink/renderer/modules/webaudio/offline_audio_context.cc'
    text = compact(body(read(src, audio), 'OfflineAudioContext::OnChunkRendered'))
    start = text.index('CHECK(rendered_buffer);')
    text = text[start:]
    steps = ['rendered_buffer->FinalizeUxrRenderedSamples();', 'resolver->Resolve(rendered_buffer.Get());',
             'if(IsContextCleared()||ContextState()==V8AudioContextState::Enum::kClosed)',
             'CHECK_EQ(pending_render_targets_.front(),rendered_buffer);', 'pending_render_targets_.TakeFirst();']
    offsets = [text.index(step) for step in steps]
    if offsets != sorted(offsets) or len(offsets) != len(set(offsets)):
        raise ValueError('Offline audio finalization or reentrant render-queue ordering changed')
    handler = 'third_party/blink/renderer/modules/webaudio/audio_handler.cc'
    require(read(src, handler), ['#include <cmath>'], handler)
    webgl = 'third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc'
    require(read(src, webgl), [
        '#include "third_party/blink/renderer/platform/scheduler/public/thread_scheduler.h"',
        '#include "third_party/blink/renderer/platform/weborigin/security_origin.h"'], webgl)
    return [canvas + '.h', canvas + '.cc', audio, handler, webgl]


def verify_release_custom_symbols(src: Path, repo: Path) -> list[str]:
    release = json.loads(owned(repo, RELEASE).read_bytes())
    symbol = re.compile(r'\b(?:Uxr\w+|UXR_\w+|uxr[-_][\w-]+|shardx[-_][\w-]+)')
    checked = []
    for group in release['patches']:
        raw = owned(repo, group['path']).read_text('utf-8')
        for section in re.split(r'(?m)(?=^diff --git )', raw):
            if not section:
                continue
            name = section.splitlines()[0].split()[3][2:]
            if not name.endswith(('.cc', '.h', '.mm', '.cpp', '.cxx')):
                continue
            additions = '\n'.join(line[1:] for line in section.splitlines() if line.startswith('+') and not line.startswith('+++'))
            required = set(symbol.findall(additions))
            if not required:
                continue
            actual = set(symbol.findall(read(src, name)))
            if required - actual:
                raise ValueError('Full release custom symbols were lost in ' + name + ': ' + ', '.join(sorted(required - actual)))
            checked.append(name)
    return checked


def verify_source_checks(src: Path, repo: Path | None = None) -> dict:
    repo = repo or Path(__file__).resolve().parents[1]
    requirements = service_requirements()
    for name in ('base/BUILD.gn', 'base/uxr_surface_modes_test_main.cc', 'content/browser/speech/tts_win.cc'):
        requirements[name] = services.REQUIRED[name]
    return {
        **verify_services(src, complete=True),
        'completeServiceAndRetirementGates': services.verify_requirements(src, requirements, require_security=True, require_lite_retirement=True),
        'nativeEnvironmentControls': verify_controls(src),
        'cookieImplementation': verify_cookie(src),
        'historyRetention': verify_history(src),
        'translation': verify_translation(src),
        'windowsShellAndAvatar': verify_shell(src, account_overlay=True),
        'signinAndHistorySync': verify_signin(src),
        'rendering158Adaptations': verify_rendering_adaptations(src),
        'releaseCustomSymbolCoverage': verify_release_custom_symbols(src, repo),
    }


def verify_negative_controls(src: Path) -> list[str]:
    prefix = 'third_party/blink/renderer/modules/'
    cases = [
        ('popup-sizing-signature', 'third_party/blink/renderer/core/frame/web_frame_widget_impl.cc',
         'bool WebFrameWidgetImpl::ApplyVisualPropertiesSizing(',
         'void WebFrameWidgetImpl::ApplyVisualPropertiesSizing('),
        ('gpu-startup-preflight-removed', 'chrome/browser/chrome_browser_main.cc',
         'if (!UxrPrepareRequiredGpu())', 'if (false)'),
        ('inherited-screen-connection-removed', 'third_party/blink/renderer/core/frame/web_frame_widget_impl.cc',
         'inherited_emulated_screen_infos_ = visual_properties.emulated_screen_infos;',
         'inherited_emulated_screen_infos_.reset();'),
        ('canvas-old-reset', prefix + 'canvas/canvas2d/base_rendering_context_2d.h',
         'void ResetInternal() override;', 'void ResetInternal() override;\n  void Reset() override;'),
        ('canvas-input-mask-not-reset', prefix + 'canvas/canvas2d/base_rendering_context_2d.cc',
         '  uxr_canvas_inputs_.reset();', '  // Input mask reset deliberately removed for the negative control.'),
        ('audio-output-not-finalized', prefix + 'webaudio/offline_audio_context.cc',
         '  rendered_buffer->FinalizeUxrRenderedSamples();', ''),
        ('audio-queue-popped-before-resolve', prefix + 'webaudio/offline_audio_context.cc',
         '  resolver->Resolve(rendered_buffer.Get());',
         '  pending_render_targets_.TakeFirst();\n  resolver->Resolve(rendered_buffer.Get());'),
        ('audio-reentrant-close-unchecked', prefix + 'webaudio/offline_audio_context.cc',
         'if (IsContextCleared() ||\n      ContextState() == V8AudioContextState::Enum::kClosed) {\n    // close() or teardown',
         'if (false) {\n    // close() or teardown'),
        ('audio-math-header-missing', prefix + 'webaudio/audio_handler.cc', '#include <cmath>', ''),
        ('webgl-scheduler-header-missing', prefix + 'webgl/webgl_rendering_context_base.cc',
         '#include "third_party/blink/renderer/platform/scheduler/public/thread_scheduler.h"', ''),
        ('webgl-origin-header-missing', prefix + 'webgl/webgl_rendering_context_base.cc',
         '#include "third_party/blink/renderer/platform/weborigin/security_origin.h"', ''),
    ]
    rejected = []
    for label, name, old, new in cases:
        path = owned(src, name)
        raw = path.read_bytes()
        text = raw.decode('utf-8')
        if old not in text:
            raise ValueError('Negative control has no source target: ' + label)
        try:
            write_if_changed(path, text.replace(old, new, 1).encode())
            try:
                if label == 'popup-sizing-signature':
                    verify_popup(src, complete=True)
                elif label in ('gpu-startup-preflight-removed', 'inherited-screen-connection-removed'):
                    verify_full_feature_links(src)
                else:
                    verify_rendering_adaptations(src)
            except ValueError:
                rejected.append(label)
            else:
                raise AssertionError('Invalid rendering source was accepted: ' + label)
        finally:
            write_if_changed(path, raw)
    return rejected


def verify_gn(src: Path, manifest: dict, gn_bin: Path) -> dict:
    version = subprocess.check_output([str(gn_bin), '--version'], text=True).strip()
    if '938087829fcb' not in version:
        raise ValueError('Expected the pinned Windows 158 GN parser')
    checked = {}
    for row in manifest['sourceFiles']:
        name = row['path']
        if name.endswith(('.gn', '.gni')) and row['after'] is not None:
            raw = source(src, name)
            if digest(raw) != row['after']:
                raise ValueError('GN parser source identity mismatch: ' + name)
            subprocess.run([str(gn_bin), 'format', '--stdin'], input=raw,
                           stdout=subprocess.DEVNULL, check=True)
            checked[name] = row['after']
    return {'method': 'GN parse only; no generation, compilation or linking', 'toolVersion': version,
            'toolSha256': digest(gn_bin.read_bytes()), 'files': checked, 'passed': len(checked)}


def verify(repo: Path, baseline: Path, patch_bin: Path, *, export_source: Path | None = None,
           gn_bin: Path | None = None) -> dict:
    manifest = load(repo)
    binaries = manifest['restore_pruned_resources']
    with tempfile.TemporaryDirectory(prefix='shardx-158-complete-source-') as tmp:
        tree = Path(tmp)
        for row in manifest['sourceFiles']:
            data = source(baseline, row['path'], binaries)
            if digest(data) != row['before']:
                raise ValueError('Wrong upstream source: ' + row['path'])
            if data is not None:
                write_if_changed(owned(tree, row['path']), data)
        patches = [owned(repo, group['path']).read_bytes() for group in manifest['groups']]
        for raw in patches:
            subprocess.run([str(patch_bin), *PATCH_FLAGS, '--forward'], input=raw, cwd=tree,
                           check=True, stdout=subprocess.DEVNULL)
        restored_source_resources.restore(tree, repo, manifest)
        for row in manifest['sourceFiles']:
            if digest(source(tree, row['path'], binaries)) != row['after']:
                raise ValueError('Wrong ported source: ' + row['path'])
        checks = verify_source_checks(tree, repo)
        negative_controls = verify_negative_controls(tree)
        gn_syntax = verify_gn(tree, manifest, gn_bin) if gn_bin is not None else {'status': 'not-run'}
        restored_source_resources.verify(tree, repo, manifest)
        if export_source is not None:
            export_source = plain(export_source)
            export_source.mkdir(parents=True, exist_ok=False)
            for row in manifest['sourceFiles']:
                data = source(tree, row['path'], binaries)
                if data is not None:
                    write_if_changed(owned(export_source, row['path']), data)
        for raw in reversed(patches):
            subprocess.run([str(patch_bin), *PATCH_FLAGS, '--reverse'], input=raw, cwd=tree,
                           check=True, stdout=subprocess.DEVNULL)
        for name in binaries:
            owned(tree, name).unlink()
        for row in manifest['sourceFiles']:
            if digest(source(tree, row['path'], binaries)) != row['before']:
                raise ValueError('Reverse replay did not recover upstream: ' + row['path'])
    return {'status': 'complete-source-port-verified', 'chromiumVersion': manifest['chromiumVersion'],
            'coreCommit': manifest['coreCommit'], 'windowsCommit': manifest['windowsCommit'],
            'releaseSource': manifest['releaseSource'], 'manifestSha256': digest(owned(repo, MANIFEST).read_bytes()),
            'productGroups': len(patches), 'forwardAndReverseFiles': len(manifest['sourceFiles']),
            'sourceChecks': checks, 'negativeControlsRejected': negative_controls,
            'gnSyntax': gn_syntax,
            'compiled': False, 'runtimeAccepted': False, 'installed': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--baseline', type=Path, required=True, help='Full ungoogled Windows 158 source after core, Windows patches and pruning')
    parser.add_argument('--patch-bin', type=Path, default=Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/usr/bin/patch.exe')
    parser.add_argument('--gn-bin', type=Path, help='Optionally parse every reviewed GN/GNI file using the pinned Windows 158 GN executable')
    parser.add_argument('--export-source', type=Path, help='Export only the verified source projection to a new directory, not a full checkout')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = verify(args.repo.absolute(), args.baseline.absolute(), args.patch_bin.absolute(),
                    export_source=args.export_source, gn_bin=args.gn_bin.absolute() if args.gn_bin else None)
    report = (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode()
    if args.report:
        if args.report.exists() and args.report.read_bytes() != report:
            raise ValueError('Use a fresh report path')
        write_if_changed(args.report, report)
    print(f"Verified {result['productGroups']} product groups and {result['forwardAndReverseFiles']} source paths; no build or runtime acceptance")


if __name__ == '__main__':
    main()
