"""Rebinding outer receipts must not admit stale collectors or missing contracts."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tools.tests.test_fingerprint_build_pipeline import (
    ROOT, candidate, rebind_fingerprint_fixture, write_fingerprint_evidence,
)


class FingerprintEvidenceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fingerprint-evidence-identity-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        (self.bundle / 'chrome.exe').write_bytes(b'synthetic browser; never executed')
        (self.bundle / 'chrome.dll').write_bytes(b'synthetic DLL; never loaded')
        self.report = write_fingerprint_evidence(
            self.root, self.bundle, build_variant=candidate.SECURITY_VARIANT)
        self.runtime_path = self.root / 'modes-runtime.json'
        self.runtime = json.loads(self.runtime_path.read_text())
        self.source_path = self.bundle / 'google-services-source.json'
        self.source = json.loads(self.source_path.read_text())

    def validate(self):
        return candidate.validate_fingerprint_tests(
            self.report, self.bundle, build_variant=candidate.SECURITY_VARIANT)

    def mutate_runtime(self, mutation):
        value = copy.deepcopy(self.runtime)
        mutation(value)
        self.runtime_path.write_text(json.dumps(value))
        rebind_fingerprint_fixture(self.root)

    def test_exact_source_collectors_and_retirement_contract_pass(self):
        result = self.validate()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['retirement_contract']['manifest_sha256'],
                         candidate.sha256(ROOT / candidate.RETIREMENT_MANIFEST_INPUT))
        self.assertEqual(result['retirement_contract']['source_receipt_sha256'],
                         candidate.sha256(self.source_path))

    def test_missing_old_or_malformed_harness_and_oracle_digests_fail_after_outer_rebind(self):
        for key in ('harness_sha256', 'oracle_sha256'):
            mutations = [lambda value: value.pop(key),
                         lambda value: value.update({key: '0' * 64}),
                         lambda value: value.update({key: [self.runtime[key]]})]
            for mutation in mutations:
                with self.subTest(key=key, mutation=mutation):
                    self.mutate_runtime(mutation)
                    with self.assertRaisesRegex(ValueError, 'collector source identity'):
                        self.validate()

    def test_every_collector_module_is_bound_even_when_outer_hashes_are_valid(self):
        for name in candidate.FINGERPRINT_COLLECTOR_MODULES:
            with self.subTest(module=name):
                self.mutate_runtime(lambda value: value['collector_modules_sha256'].update({name: 'f' * 64}))
                with self.assertRaisesRegex(ValueError, 'collector module identity'):
                    self.validate()

    def test_missing_extra_or_malformed_collector_inventory_fail(self):
        for name in candidate.FINGERPRINT_COLLECTOR_MODULES:
            self.mutate_runtime(lambda value: value['collector_modules_sha256'].pop(name))
            with self.subTest(missing=name), self.assertRaisesRegex(ValueError, 'module inventory'):
                self.validate()
        for modules in (None, [], {'extra.mjs': 'a' * 64},
                        {**self.runtime['collector_modules_sha256'], 'extra.mjs': 'b' * 64}):
            self.mutate_runtime(lambda value: value.update(collector_modules_sha256=modules))
            with self.subTest(modules=modules), self.assertRaisesRegex(ValueError, 'module inventory'):
                self.validate()

    def test_changed_selected_source_invalidates_an_older_complete_report(self):
        source_paths = [candidate.FINGERPRINT_RUNTIME_HARNESS, candidate.FINGERPRINT_RUNTIME_ORACLE,
                        *('tools/' + name for name in candidate.FINGERPRINT_COLLECTOR_MODULES)]
        alternate = self.root / 'new-source'
        for name in source_paths:
            path = alternate / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, path)
        for name in source_paths:
            path = alternate / name
            original = path.read_bytes()
            path.write_bytes(original + b'\nsource changed\n')
            with self.subTest(source=name), patch.object(candidate, 'REPO', alternate), \
                    self.assertRaisesRegex(ValueError, 'collector .*identity'):
                self.validate()
            path.write_bytes(original)

    def test_retirement_manifest_and_source_receipt_are_required_package_members(self):
        for path in (self.bundle / 'retirement-manifest.json', self.source_path):
            original = path.read_bytes()
            path.unlink()
            with self.subTest(missing=path.name), self.assertRaisesRegex(ValueError, 'missing or linked'):
                self.validate()
            path.write_bytes(original)

    def test_changed_retirement_payload_cannot_be_rebased_in_source_receipt(self):
        path = self.bundle / 'retirement-manifest.json'
        path.write_bytes(path.read_bytes() + b'\n')
        source = copy.deepcopy(self.source)
        source['preparation_inputs'][candidate.RETIREMENT_MANIFEST_INPUT] = candidate.sha256(path)
        self.source_path.write_text(json.dumps(source))
        rebind_fingerprint_fixture(self.root)
        with self.assertRaisesRegex(ValueError, 'retirement manifest differs'):
            self.validate()

    def test_retirement_receipt_must_bind_both_manifest_and_capability_source(self):
        mutations = [lambda value: value['preparation_inputs'].pop(candidate.RETIREMENT_MANIFEST_INPUT),
                     lambda value: value['preparation_inputs'].update({candidate.RETIREMENT_MANIFEST_INPUT: '0' * 64}),
                     lambda value: value['preparation_inputs'].pop('build/fingerprint-modes-capabilities.json'),
                     lambda value: value.update(build_variant='fingerprint-modes-win155'),
                     lambda value: value.update(preparation_inputs=[])]
        for mutation in mutations:
            source = copy.deepcopy(self.source)
            mutation(source)
            self.source_path.write_text(json.dumps(source))
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'prepared source receipt'):
                self.validate()

    def test_retirement_declaration_cannot_be_removed_with_outer_rebinding(self):
        contract = json.loads((self.bundle / 'fingerprint-modes-capabilities.json').read_text())
        contract.pop('lite_retirement')
        data = json.dumps(contract).encode()
        (self.bundle / 'fingerprint-modes-capabilities.json').write_bytes(data)
        (self.root / 'fingerprint-modes-capabilities.json').write_bytes(data)
        source = copy.deepcopy(self.source)
        source['preparation_inputs']['build/fingerprint-modes-capabilities.json'] = hashlib.sha256(data).hexdigest()
        self.source_path.write_text(json.dumps(source))
        rebind_fingerprint_fixture(self.root)
        with self.assertRaisesRegex(ValueError, 'capabilities identity'):
            self.validate()


if __name__ == '__main__':
    unittest.main()
