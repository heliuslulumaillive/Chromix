"""Exact completed-157 identity and the real empty-TLB/GUID control flow."""
import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools import repair_windows_midl as repair
from tools.tests import test_windows154_midl as previous

IDENTITY = {
    'chromium_version': '157.0.8103.38',
    'ungoogled_commit': 'de09e0c70c6bc1ac83d4ecdfd0e82c7e250c9a3c',
    'head_sha': 'f85bc55429c37b1c748b676b18fe79b38eee3207',
    'platform': 'windows', 'arch': 'x64',
    'repository': 'ungoogled-software/ungoogled-chromium-windows', 'repository_id': 177210827,
    'head_branch': '157.0.8103.38-1.1', 'event': 'push',
    'workflow_path': '.github/workflows/build-x64.yml', 'run_id': 42046998774,
    'artifact_id': 13560057174, 'artifact_name': 'build-artifact',
    'artifact_digest': 'sha256:0dc5c30c4eb0c21e503371e9aa0e28c37c1b478271b4612d82e1fa4dec9bc16d',
    'artifact_size_in_bytes': 16243947927,
}


def load_functions(payload):
    digest = hashlib.sha256(payload).hexdigest()
    if digest not in (repair.WINDOWS157_ORIGINAL_SHA256, repair.WINDOWS157_PREPARED_ORIGINAL_SHA256,
                       repair.WINDOWS157_REPAIRED_SHA256):
        raise ValueError('unknown Windows157 MIDL function fixture')
    original = previous.FIXTURE.read_bytes()
    equivalent = repair.transform155(original) if digest == repair.WINDOWS157_REPAIRED_SHA256 else original
    # Canonicalization changes a comment only; execute the actual accepted
    # wrapper's reviewed functions without executing imports or native MIDL.
    assert ast.dump(ast.parse(payload)) == ast.dump(ast.parse(equivalent))
    namespace = previous.load_functions(equivalent)
    functions = [node for node in ast.parse(payload).body
                 if isinstance(node, ast.FunctionDef) and node.name in previous.FUNCTIONS]
    if {node.name for node in functions} != previous.FUNCTIONS or len(functions) != len(previous.FUNCTIONS):
        raise ValueError('unexpected Windows157 MIDL function inventory')
    exec(compile(ast.Module(body=functions, type_ignores=[]), 'reviewed-windows157-midl', 'exec'), namespace)
    return namespace


class Windows157MidlIdentityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='windows157-midl-')
        self.addCleanup(temporary.cleanup)
        self.src = Path(temporary.name).resolve()
        self.script = self.src / repair.SCRIPT
        self.script.parent.mkdir(parents=True)
        # The exact 157 raw script + pinned Windows overlay + domain transform
        # independently reconstruct the same bytes as this existing fixture.
        self.original = previous.FIXTURE.read_bytes()
        self.script.write_bytes(self.original)
        self.out = self.src / 'out/Default'
        self.out.mkdir(parents=True)
        self.output = self.out / 'gen/midl/a.tlb'
        self.output.parent.mkdir(parents=True)
        self.output.write_bytes(b'stale')
        (self.out / 'build.ninja').write_text(previous.graph(['gen/midl/a.tlb']))

    def apply(self, identity=IDENTITY, arch='x64', **kwargs):
        return repair.apply(self.src, 'windows', arch, identity, **kwargs)

    def test_exact_producer_and_once_only_declared_output_invalidation(self):
        self.assertEqual(dict(repair.WINDOWS157_RECORDS[0]['identity']), IDENTITY)
        self.assertEqual(hashlib.sha256(self.original).hexdigest(), repair.WINDOWS157_ORIGINAL_SHA256)
        source = self.src / 'third_party/win_build_output/midl/x64/source.tlb'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'')
        unrelated = self.out / 'retained.obj'
        unrelated.write_bytes(b'retained compiled object')
        states = {p: previous.snapshot(p) for p in (source, unrelated)}
        first = self.apply()
        self.assertEqual(first['status'], 'repaired')
        self.assertEqual(first['invalidated_outputs'], ['gen/midl/a.tlb'])
        self.assertEqual(self.script.read_bytes(), repair.transform157(self.original))
        self.output.write_bytes(b'new native generated output')
        output_state = previous.snapshot(self.output)
        self.assertEqual(self.apply()['status'], 'verified')
        self.assertEqual(previous.snapshot(self.output), output_state)
        for path, state in states.items():
            self.assertEqual(previous.snapshot(path), state)
        self.assertEqual(json.loads((self.src / repair.RECORD).read_text())['identity'], IDENTITY)

    def test_identity_fields_types_architecture_and_version_rejected_before_writes(self):
        for key, value in IDENTITY.items():
            alternatives = ['unknown'] + ([float(value)] if type(value) is int else [])
            for alternate in alternatives:
                changed = dict(IDENTITY, **{key: alternate})
                if key == 'chromium_version':
                    changed[key] = '157.0.8103.39'
                with self.subTest(key=key), self.assertRaises(ValueError):
                    self.apply(changed)
        for identity, arch in [(dict(IDENTITY, extra=True), 'x64'),
                               (dict(IDENTITY, arch='arm64'), 'arm64'), (IDENTITY, 'arm64')]:
            with self.assertRaises(ValueError):
                self.apply(identity, arch)
        self.assertEqual(self.output.read_bytes(), b'stale')
        self.assertEqual(self.script.read_bytes(), self.original)
        self.assertFalse((self.src / repair.RECORD).exists())

    def test_current_upstream_x64_admission_matches_the_exact_repair_pin(self):
        from tools.restore_upstream_cache import identities
        repo = Path(__file__).resolve().parents[2]
        identity, _, _ = identities(repo, 'windows', 'x64')
        self.assertEqual(identity, IDENTITY)
        self.assertEqual(self.apply(identity)['status'], 'repaired')

    def test_inspect_product_domain_restoration_finish_and_warm_reuse(self):
        from tools.prepare_google_services import restore_domains
        fixture = Path(__file__).parent / 'fixtures/windows157-domain-regex.list'
        filelist = self.src / 'domain_substitution.list'
        filelist.write_text(repair.SCRIPT + '\n')
        self.apply()  # base-prepare inspect
        before = previous.snapshot(self.script)
        restore_domains(self.src, None, fixture, filelist, pinned=True)
        self.assertEqual(previous.snapshot(self.script), before)
        self.output.write_bytes(b'regenerated native output')
        output = previous.snapshot(self.output)
        self.assertEqual(self.apply()['status'], 'verified')  # Configure finish
        self.assertEqual(self.apply()['status'], 'verified')
        self.assertEqual(previous.snapshot(self.output), output)

    def test_prepared_checkpoint_form_records_its_actual_preimage(self):
        prepared = self.original.replace(repair.WINDOWS157_COMMENT_URL_BEFORE,
                                         repair.WINDOWS157_COMMENT_URL_AFTER)
        self.assertEqual(hashlib.sha256(prepared).hexdigest(), repair.WINDOWS157_PREPARED_ORIGINAL_SHA256)
        self.script.write_bytes(prepared)
        first = self.apply()
        self.assertEqual(first['before_sha256'], repair.WINDOWS157_PREPARED_ORIGINAL_SHA256)
        proof_path = self.src / repair.RECORD
        proof = json.loads(proof_path.read_text())
        self.assertEqual(proof['before_sha256'], repair.WINDOWS157_PREPARED_ORIGINAL_SHA256)
        self.assertEqual(self.apply()['status'], 'verified')
        for invalid in ('f' * 64, [], None):
            proof['before_sha256'] = invalid
            proof_path.write_text(json.dumps(proof))
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'invalidation record'):
                self.apply()

    def test_unknown_or_partial_wrapper_cannot_invalidate_outputs(self):
        for raw in (self.original + b'# changed', self.original.replace(b'\n', b'\r\n'),
                    self.original.replace(repair.BEFORE, repair.AFTER)):
            self.script.write_bytes(raw)
            with self.assertRaises(ValueError):
                self.apply()
            self.assertEqual(self.output.read_bytes(), b'stale')
        with self.assertRaises(TypeError):
            repair.WINDOWS157_RECORDS[0]['identity']['artifact_id'] = 1


class Windows157MidlFunctionsTest(previous.Windows154MidlFunctionsTest):
    def setUp(self):
        super().setUp()
        self.midl = load_functions(repair.transform157(self.original))

    def test_canonical_loader_rejects_unreviewed_trailing_code(self):
        with self.assertRaisesRegex(ValueError, 'unknown Windows157'):
            load_functions(repair.transform157(self.original) + b'raise RuntimeError("unreviewed")\n')


if __name__ == '__main__':
    unittest.main()
