"""Pinned ETW resources survive pruning and compile from the real output layout."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from tools import restored_source_resources as resources

ROOT = Path(__file__).resolve().parents[2]
PROVENANCE = ROOT / 'build/security-resources/etw_events/provenance.json'
EXPECTED = {
    'chrome/app/etw_events/MSG00001.bin':
        ('build/security-resources/etw_events/MSG00001.bin', 180,
         '51b3d8c734d307af13984bb2c2abde79c859a5acb6386fd233f3e9338f2a7dc5'),
    'chrome/app/etw_events/chrome_events_winTEMP.BIN':
        ('build/security-resources/etw_events/chrome_events_winTEMP.BIN', 1402,
         '1de7329c900e3e367a04ea99c1169cde528754c408de077628f5be856cfe0926'),
}
CONFIG = {'restore_pruned_resources': {
    name: {'repository_path': path, 'size': size, 'sha256': digest}
    for name, (path, size, digest) in EXPECTED.items()
}}
RC_SOURCE = 'chrome/app/etw_events/chrome_events_win.rc'
RC_FIXTURE = ROOT / 'tools/tests/fixtures/windows156-etw/chrome_events_win.rc.bin'


def windows_rc():
    explicit = os.environ.get('CHROMIX_TEST_RC')
    if explicit:
        return Path(explicit) if Path(explicit).is_file() else None
    if os.name != 'nt':
        return None
    root = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Windows Kits/10/bin'
    candidates = list(root.glob('*/x64/rc.exe'))
    version = lambda path: tuple(int(n) for n in path.parents[1].name.split('.') if n.isdigit())
    return max(candidates, key=version) if candidates else None


def res_payloads(data):
    """Read the binary .res records, rather than trusting rc.exe exit code alone."""
    result = {}
    position = 0
    while position < len(data):
        if position + 8 > len(data):
            raise ValueError('Truncated resource record')
        size, header_size = struct.unpack_from('<II', data, position)
        if header_size < 32 or position + header_size + size > len(data):
            raise ValueError('Invalid resource header')
        cursor = position + 8

        def identifier():
            nonlocal cursor
            value = struct.unpack_from('<H', data, cursor)[0]
            cursor += 2
            if value == 0xffff:
                ordinal = struct.unpack_from('<H', data, cursor)[0]
                cursor += 2
                return ordinal
            text = []
            while value:
                text.append(value)
                value = struct.unpack_from('<H', data, cursor)[0]
                cursor += 2
            return ''.join(chr(n) for n in text)

        kind, name = identifier(), identifier()
        cursor = (cursor + 3) & ~3
        _, _, language, _, _ = struct.unpack_from('<IHHII', data, cursor)
        payload = data[position + header_size:position + header_size + size]
        if size:
            key = (kind, name, language)
            if key in result:
                raise ValueError('Duplicate ETW resource')
            result[key] = payload
        position = (position + header_size + size + 3) & ~3
    return result


class WindowsEtwResourceTests(unittest.TestCase):
    def test_payloads_match_pinned_git_objects_and_config(self):
        provenance = json.loads(PROVENANCE.read_text(encoding='utf-8'))
        self.assertEqual(provenance['chromium_version'], '156.0.8078.79')
        self.assertEqual(provenance['ungoogled_core_commit'], '28a211ab4ab6b83d919966a4f58a6df23cf4fb29')
        entries = {row['path']: row for row in provenance['files']}
        for name, (path, size, digest) in EXPECTED.items():
            data = (ROOT / path).read_bytes()
            self.assertEqual(len(data), size)
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
            self.assertEqual(entries[name]['sha256'], digest)
            self.assertEqual(entries[name]['gitiles_blob'],
                             hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest())
            self.assertIn('/156.0.8078.79/' + name, entries[name]['url'])
        rc = RC_FIXTURE.read_bytes()
        self.assertEqual(len(rc), 84)
        self.assertEqual(hashlib.sha256(rc).hexdigest(), entries[RC_SOURCE]['sha256'])
        self.assertEqual(hashlib.sha1(b'blob 84\0' + rc).hexdigest(), entries[RC_SOURCE]['gitiles_blob'])
        config = json.loads((ROOT / 'build/google-services-win155.json').read_text(encoding='utf-8'))
        self.assertIn(RC_SOURCE, config['extra_sources'])
        for name, spec in CONFIG['restore_pruned_resources'].items():
            self.assertIn(name, config['restore_pruned_sources'])
            self.assertEqual(config['restore_pruned_resources'][name], spec)

    def test_restore_preserves_exact_bytes_and_unchanged_mtimes(self):
        with tempfile.TemporaryDirectory(prefix='etw-resource-fixture-') as temporary:
            src = Path(temporary)
            self.assertEqual(set(resources.restore(src, ROOT, CONFIG)), set(EXPECTED))
            before = {name: (src / name).stat().st_mtime_ns for name in EXPECTED}
            self.assertEqual(set(resources.verify(src, ROOT, CONFIG)), set(EXPECTED))
            resources.restore(src, ROOT, CONFIG)
            self.assertEqual(before, {name: (src / name).stat().st_mtime_ns for name in EXPECTED})
            for name, (payload, _, _) in EXPECTED.items():
                self.assertEqual((src / name).read_bytes(), (ROOT / payload).read_bytes())

    def test_unexpected_existing_source_bytes_are_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix='etw-corrupt-source-') as temporary:
            src = Path(temporary)
            resources.restore(src, ROOT, CONFIG)
            for name in EXPECTED:
                path = src / name
                original = path.read_bytes()
                changed = bytes([original[0] ^ 1]) + original[1:]
                path.write_bytes(changed)
                with self.subTest(resource=name), self.assertRaisesRegex(ValueError, 'unexpected bytes'):
                    resources.restore(src, ROOT, CONFIG)
                self.assertEqual(path.read_bytes(), changed)
                with self.assertRaisesRegex(ValueError, 'missing or changed'):
                    resources.verify(src, ROOT, CONFIG)
                path.write_bytes(original)

    def test_changed_repository_bytes_or_wrong_size_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='etw-corrupt-payload-') as temporary:
            repo = Path(temporary)
            for path, _, _ in EXPECTED.values():
                target = repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / path, target)
            for name, (path, _, _) in EXPECTED.items():
                target = repo / path
                original = target.read_bytes()
                target.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                with self.subTest(resource=name), self.assertRaisesRegex(ValueError, 'identity mismatch'):
                    resources.resources(repo, CONFIG)
                target.write_bytes(original)
                wrong = copy.deepcopy(CONFIG)
                wrong['restore_pruned_resources'][name]['size'] += 1
                with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                    resources.resources(repo, wrong)

    @unittest.skipUnless(windows_rc(), 'Windows SDK resource compiler is required')
    def test_real_rc_from_out_default_requires_and_embeds_both_restored_resources(self):
        rc = windows_rc()
        with tempfile.TemporaryDirectory(prefix='etw-native-rc-') as temporary:
            src = Path(temporary) / 'src'
            source = src / RC_SOURCE
            source.parent.mkdir(parents=True)
            source.write_bytes(RC_FIXTURE.read_bytes())
            output = src / 'out/Default'
            output.mkdir(parents=True)

            def compile_resource(name):
                # Match the real output directory and relative search inputs.
                # Do not change cwd to the directory containing the .rc file.
                args = [str(rc), '/nologo', '/I../..', '/Igen',
                        '/I../../buildtools/third_party/libc++', '/fo' + name,
                        '../../' + RC_SOURCE]
                return subprocess.run(args, cwd=output, capture_output=True, text=True,
                                      encoding='utf-8', errors='replace', timeout=30)

            missing = compile_resource('missing.res')
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn('RC2135', missing.stdout + missing.stderr)
            resources.restore(src, ROOT, CONFIG)
            resources.verify(src, ROOT, CONFIG)
            built = compile_resource('restored.res')
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            actual = res_payloads((output / 'restored.res').read_bytes())
            self.assertEqual(actual, {
                (11, 1, 0x409): (ROOT / EXPECTED['chrome/app/etw_events/MSG00001.bin'][0]).read_bytes(),
                ('WEVT_TEMPLATE', 1, 0x409): (ROOT / EXPECTED['chrome/app/etw_events/chrome_events_winTEMP.BIN'][0]).read_bytes(),
            })


if __name__ == '__main__':
    unittest.main()
