"""Reject incomplete or mixed source ports without compiling a browser."""
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from tools import lite_patch_stack
from tools import verify_windows158_product_port as port

ROOT = Path(__file__).resolve().parents[2]


class Windows158ProductPortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='source-port-158-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.manifest = json.loads((ROOT / port.MANIFEST).read_bytes())
        release = json.loads((ROOT / port.RELEASE).read_bytes())
        paths = {port.MANIFEST, port.RELEASE, port.FEATURES, 'build/ungoogled-revisions.psd1',
                 'CHROMIUM_WINDOWS_VERSION', 'UNGOOGLED_WINDOWS_VERSION'}
        paths.update(g['path'] for g in self.manifest['groups'])
        paths.update(p['path'] for p in release['patches'])
        for name in paths:
            dest = self.repo / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)

    def save(self):
        payload = {k: v for k, v in self.manifest.items() if k != 'manifestSha256'}
        self.manifest['manifestSha256'] = port.digest(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode())
        (self.repo / port.MANIFEST).write_text(json.dumps(self.manifest), encoding='utf-8')

    def test_complete_source_port_does_not_claim_runtime_acceptance(self):
        manifest = port.load(self.repo)
        self.assertEqual(len(manifest['groups']), 26)
        self.assertEqual(len(manifest['sourceFiles']), 686)
        self.assertFalse(manifest['compiled'])
        active = lite_patch_stack.load(ROOT)
        self.assertEqual(active['runtime']['chromium_version'], manifest['chromiumVersion'])
        self.assertEqual(active['source_port_manifest_sha256'], port.digest((ROOT / port.MANIFEST).read_bytes()))

    def test_selected_patch_additions_retain_release_custom_symbols(self):
        symbol = re.compile(r'\b(?:Uxr\w+|UXR_\w+|uxr[-_][\w-]+|shardx[-_][\w-]+)')
        def additions(paths):
            result = {}
            for path in paths:
                for section in re.split(r'(?m)(?=^diff --git )', (self.repo / path).read_text('utf-8')):
                    if not section:
                        continue
                    name = section.splitlines()[0].split()[3][2:]
                    if name.endswith(('.cc', '.h', '.mm', '.cpp', '.cxx')):
                        added = '\n'.join(line[1:] for line in section.splitlines()
                                          if line.startswith('+') and not line.startswith('+++'))
                        result[name] = set(symbol.findall(added))
            return result
        release = json.loads((self.repo / port.RELEASE).read_bytes())
        expected = additions(row['path'] for row in release['patches'])
        selected = additions(row['path'] for row in self.manifest['groups'])
        missing = {name: sorted(names - selected.get(name, set()))
                   for name, names in expected.items() if names - selected.get(name, set())}
        self.assertEqual(missing, {}, 'A partial browser patch displaced a full release feature')

    def test_rehashed_manifest_cannot_drop_a_release_topic(self):
        self.manifest['groups'].pop()
        self.save()
        with self.assertRaisesRegex(ValueError, '26 original product topics'):
            port.load(self.repo)

    def test_rehashed_manifest_cannot_drop_unchanged_dependencies(self):
        self.manifest['sourceFiles'] = [r for r in self.manifest['sourceFiles']
                                        if r['path'] != 'v8/src/flags/flag-definitions.h']
        self.save()
        with self.assertRaisesRegex(ValueError, 'missing or duplicate source paths'):
            port.load(self.repo)

    def test_unlisted_patch_is_rejected(self):
        path = self.repo / 'patches/lite-windows158-complete/99-unlisted.patch'
        path.write_text('unlisted', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'patch inventory'):
            port.load(self.repo)

    def test_altered_port_patch_is_rejected(self):
        group = next(g for g in self.manifest['groups'] if '/lite-windows158-complete/' in g['path'])
        path = self.repo / group['path']
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'Invalid or overlapping product group'):
            port.load(self.repo)

    def test_altered_release_ancestor_is_rejected(self):
        path = self.repo / 'patches/lite-windows157/19-shared-policy.patch'
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'Changed 157 release input'):
            port.load(self.repo)

    def test_source_record_cannot_assert_runtime_acceptance(self):
        self.manifest['runtimeAccepted'] = True
        self.save()
        with self.assertRaisesRegex(ValueError, 'source-only 158 manifest'):
            port.load(self.repo)

    def test_native_resource_inventory_cannot_be_removed(self):
        self.manifest['restore_pruned_resources'] = {}
        self.save()
        with self.assertRaisesRegex(ValueError, 'native resource contract'):
            port.load(self.repo)

    def test_manual_resolution_record_must_bind_the_actual_source(self):
        name = next(iter(self.manifest['manualResolutions']))
        self.manifest['manualResolutions'][name]['after'] = '0' * 64
        self.save()
        with self.assertRaisesRegex(ValueError, 'Manual resolution identities'):
            port.load(self.repo)

    def test_wrong_upstream_commit_is_rejected_even_after_rehash(self):
        self.manifest['coreCommit'] = '0' * 40
        self.save()
        with self.assertRaisesRegex(ValueError, 'platform pins'):
            port.load(self.repo)


if __name__ == '__main__':
    unittest.main()
