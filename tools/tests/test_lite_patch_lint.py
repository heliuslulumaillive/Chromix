"""Compact topics keep manifest and unified-diff integrity without legacy shape rules."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from tools import check_patches, lite_patch_stack as stack, patch_selection

ROOT=Path(__file__).resolve().parents[2]


class ProductLintTests(unittest.TestCase):
    def copy_product(self, destination):
        for name in stack.input_hashes(ROOT):
            path=destination/name
            path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/name,path)

    def test_current_multi_file_product_is_the_active_windows_inventory(self):
        self.assertEqual(patch_selection.active_profile(ROOT,'windows'),'lite')
        self.assertEqual(patch_selection.active_profile(ROOT,'linux'),'legacy')
        report=check_patches.Report()
        check_patches.check_product_stack(report,ROOT)
        self.assertFalse(report.failures)

    def test_changed_product_bytes_without_manifest_update_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.copy_product(root)
            product=stack.load(root);path=root/product['patches'][0]['path']
            path.write_bytes(path.read_bytes()+b'\n')
            report=check_patches.Report();check_patches.check_product_stack(report,root)
            self.assertEqual(report.failures,['selected-windows'])

    def test_rehashed_manifest_cannot_hide_malformed_product_hunks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.copy_product(root)
            product=stack.load(root);row=product['patches'][0];path=root/row['path']
            original=path.read_bytes()
            raw,count=re.subn(rb'(?m)^(@@ -\d+,)(\d+)',
                             lambda m:m[1]+str(int(m[2])+1).encode(),original,count=1)
            self.assertEqual(count,1);path.write_bytes(raw)
            row['sha256']=hashlib.sha256(raw).hexdigest()
            payload={k:v for k,v in product.items() if k!='stack_sha256'}
            product['stack_sha256']=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            (root/stack.MANIFEST).write_text(json.dumps(product),encoding='utf-8')
            report=check_patches.Report();check_patches.check_product_stack(report,root)
            self.assertEqual(report.failures,['selected-windows'])


if __name__=='__main__':unittest.main()
