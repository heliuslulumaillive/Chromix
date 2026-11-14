"""A source repair preserves objects and refuses every unreviewed source byte."""
import copy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import lite_patch_stack as stack, lite_product_migration as migration


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ProductSourceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='product-source-repair-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo, self.src = self.root / 'repo', self.root / 'src'
        self.repo.mkdir()
        self.src.mkdir()
        self.original = {'timezone.cc': b'old locale call\n', 'locale.cc': b'native locale owner\n',
                         'unchanged.cc': b'unchanged source\n', 'out/Default/obj/retained.obj': b'compiled object\x00',
                         'out/Default/.ninja_log': b'completed edges\n'}
        self.updates = []
        self.product = {'stack_sha256': 'e' * 64, 'source_files': []}
        for name, data in self.original.items():
            path = self.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.utime(path, ns=(1600000000000000000, 1600000000000000000))
            if name in ('timezone.cc', 'locale.cc'):
                after = data + b'reviewed ICU bridge repair\n'
                replacement = 'build/checkpoint-sources/' + digest(after) + '.cc'
                target = self.repo / replacement
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(after)
                self.updates.append({'path': name, 'before_sha256': digest(data),
                    'after_sha256': digest(after), 'replacement_path': replacement})
            else:
                after = data
            if not name.startswith('out/'):
                self.product['source_files'].append({'path': name, 'after_sha256': digest(after)})
        self.rule = {'source_patch_kind': stack.KIND, 'target_patch_kind': stack.KIND,
            'source_aggregate_sha256': 'd' * 64, 'target_aggregate_sha256': self.product['stack_sha256'],
            'product_source_updates': self.updates}
        self.mock_product = patch.object(stack, 'load', return_value=self.product)
        self.mock_product.start()
        self.addCleanup(self.mock_product.stop)

    def snapshot(self):
        return {path.relative_to(self.src).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.src.rglob('*') if path.is_file()}

    def test_reviewed_created_header_requires_absence_and_exact_pinned_bytes(self):
        content=b'reviewed new private resource helper\n'
        name='base/reviewed-private-fonts.h';replacement='build/checkpoint-sources/'+digest(content)+'.h'
        file=self.repo/replacement;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(content)
        self.product['source_files'].append({'path':name,'before_sha256':None,'after_sha256':digest(content)})
        self.updates.append({'path':name,'before_sha256':None,'after_sha256':digest(content),'replacement_path':replacement})
        conflict=self.src/name;conflict.parent.mkdir(parents=True);conflict.write_bytes(b'unreviewed existing header')
        before=self.snapshot()
        with self.assertRaises(ValueError):migration.apply_source_updates(self.repo,self.src,self.rule)
        self.assertEqual(before,self.snapshot());conflict.unlink()
        migration.apply_source_updates(self.repo,self.src,self.rule)
        self.assertEqual(conflict.read_bytes(),content)

    def test_missing_before_digest_cannot_create_an_existing_source(self):
        self.updates[0]['before_sha256']=None
        before=self.snapshot()
        with self.assertRaises(ValueError):migration.apply_source_updates(self.repo,self.src,self.rule)
        self.assertEqual(before,self.snapshot())

    def test_only_reviewed_sources_change_and_compiled_state_is_preserved(self):
        before = self.snapshot()
        migration.verify_donor_sources(self.repo, self.src, self.rule)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(migration.apply_source_updates(self.repo, self.src, self.rule),
                         ['timezone.cc', 'locale.cc'])
        after = self.snapshot()
        for name in before:
            if name in ('timezone.cc', 'locale.cc'):
                self.assertNotEqual(after[name][0], before[name][0])
                self.assertGreater(after[name][1], before[name][1])
            else:
                self.assertEqual(after[name], before[name])
        stack.verify_source(self.repo, self.src)
        with self.assertRaisesRegex(ValueError, 'reviewed final bytes'):
            migration.apply_source_updates(self.repo, self.src, self.rule)

    def test_changed_preimage_or_unrelated_source_refused_before_any_write(self):
        for name in ('timezone.cc', 'unchanged.cc'):
            with self.subTest(name=name):
                path = self.src / name
                path.write_bytes(b'unreviewed source')
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, 'reviewed final bytes'):
                    migration.apply_source_updates(self.repo, self.src, self.rule)
                self.assertEqual(self.snapshot(), before)
                path.write_bytes(self.original[name])

    def test_missing_or_changed_replacement_refused_before_any_write(self):
        path = self.repo / self.updates[-1]['replacement_path']
        original = path.read_bytes()
        path.write_bytes(original + b'tampering')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'replacement bytes changed'):
            migration.apply_source_updates(self.repo, self.src, self.rule)
        self.assertEqual(self.snapshot(), before)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            migration.apply_source_updates(self.repo, self.src, self.rule)
        self.assertEqual(self.snapshot(), before)

    def test_missing_duplicate_unowned_or_mismatched_updates_are_rejected(self):
        cases = []
        for key, value in [('product_source_updates', []), ('source_patch_kind', 'unreviewed'),
                           ('target_aggregate_sha256', 'd' * 64)]:
            rule = copy.deepcopy(self.rule)
            rule[key] = value
            cases.append(rule)
        for key, value in [('path', '../escape.cc'), ('path', 'out/Default/obj/retained.obj'),
                           ('before_sha256', None), ('before_sha256', self.updates[0]['after_sha256']),
                           ('after_sha256', 'a' * 64), ('replacement_path', '../outside.cc')]:
            rule = copy.deepcopy(self.rule)
            rule['product_source_updates'][0][key] = value
            cases.append(rule)
        rule = copy.deepcopy(self.rule)
        rule['product_source_updates'].append(copy.deepcopy(rule['product_source_updates'][0]))
        cases.append(rule)
        before = self.snapshot()
        for index, rule in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                migration.apply_source_updates(self.repo, self.src, rule)
            self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
