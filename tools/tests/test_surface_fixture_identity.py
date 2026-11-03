"""The new executed corpus must be part of the source recipe, never optional."""
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from prepare_google_services import recipe_identity

PREFIX = 'tools/tests/fixtures/pixel-semantics-v1/'
OVERLAY = 'patches/fingerprint-modes/0011-pixel-semantic-preservation.patch'


class SurfaceFixtureIdentity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='surface-recipe-test-')
        self.repo = Path(self.temp.name)
        self.digest, self.inputs = recipe_identity(ROOT)
        for name in self.inputs:
            destination = self.repo / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)

    def tearDown(self):
        self.temp.cleanup()

    def test_all_corpus_inputs_bound_and_mutation_changes_recipe(self):
        wanted = {PREFIX + name for name in ('cases.tsv', 'pixels.bin', 'provenance.json')}
        self.assertTrue(wanted <= self.inputs.keys())
        self.assertEqual(recipe_identity(self.repo), (self.digest, self.inputs))
        corpus = self.repo / (PREFIX + 'pixels.bin')
        original = corpus.read_bytes()
        corpus.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        digest, inputs = recipe_identity(self.repo)
        self.assertNotEqual(digest, self.digest)
        self.assertEqual([name for name in inputs if inputs[name] != self.inputs[name]], [PREFIX + 'pixels.bin'])

    def test_missing_active_corpus_fails_closed(self):
        (self.repo / (PREFIX + 'cases.tsv')).unlink()
        with self.assertRaises(FileNotFoundError):
            recipe_identity(self.repo)

    def test_older_recipe_does_not_require_new_corpus(self):
        config_path = self.repo / 'build/google-services-win155.json'
        config = json.loads(config_path.read_text(encoding='utf-8'))
        config['restoration_overlays'].remove(OVERLAY)
        config_path.write_text(json.dumps(config), encoding='utf-8')
        for name in ('cases.tsv', 'pixels.bin', 'provenance.json'):
            (self.repo / (PREFIX + name)).unlink()
        _, inputs = recipe_identity(self.repo)
        self.assertFalse(any(name.startswith(PREFIX) for name in inputs))


if __name__ == '__main__':
    unittest.main()
