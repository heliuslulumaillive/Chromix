"""Only the product lane builds automatically; push defaults are executable."""
import json
import fnmatch
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)


def expression(text, event, inputs):
    value = text.removeprefix('${{ ').removesuffix(' }}')
    value = value.replace('github.event_name', repr(event))
    value = re.sub(r'inputs\.(\w+)', lambda match: repr(inputs.get(match[1], '')), value)
    return eval(value.replace('||', 'or').replace('&&', 'and'),
                {'__builtins__': {}, 'fromJSON': json.loads})


class ProductEntrypointTest(unittest.TestCase):
    def test_all_active_payload_inputs_trigger_the_current_product_build(self):
        from tools.lite_patch_stack import input_hashes
        patterns = load('security-win157.yml')['on']['push']['paths']
        missing = [name for name in input_hashes(ROOT)
                   if not any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)]
        self.assertEqual(missing, [])

    def test_main_push_uses_product_lane_and_historical_lane_is_explicit(self):
        product = load('security-win157.yml')
        historical = load('build-win-x64-github.yml')
        self.assertEqual(product['on']['push']['branches'], ['main'])
        self.assertEqual(set(historical['on']), {'workflow_call', 'workflow_dispatch'})
        self.assertIn('Historical', historical['name'])
        self.assertIn('patches/lite-windows157/**', product['on']['push']['paths'])
        self.assertNotIn('patches/**', product['on']['push']['paths'])
        release = load('release-browser.yml')
        self.assertNotIn(historical['name'], release['on']['workflow_run']['workflows'])

    def test_push_uses_verified_upstream_cache_and_safe_stage_defaults(self):
        workflow = load('security-win157.yml')
        inputs = workflow['jobs']['stage1']['with']
        self.assertEqual(expression(inputs['operation'], 'push', {}), 'upstream')
        self.assertEqual(expression(inputs['source_stage'], 'push', {}), 0)
        self.assertEqual(expression(inputs['source_run'], 'push', {}), '')
        self.assertIs(expression(inputs['retain_checkpoint'], 'push', {}), True)
        for stage in ('stage2', 'stage3', 'stage4'):
            self.assertIn("fromJSON(inputs.max_stages || '4')", workflow['jobs'][stage]['if'])

    def test_manual_values_and_false_retention_are_preserved(self):
        settings = load('security-win157.yml')['jobs']['stage1']['with']
        requested = {'operation': 'upgrade', 'source_run': '41805171472',
                     'source_stage': '1', 'retain_checkpoint': False}
        self.assertEqual(expression(settings['operation'], 'workflow_dispatch', requested), 'upgrade')
        self.assertEqual(expression(settings['source_run'], 'workflow_dispatch', requested), '41805171472')
        self.assertEqual(expression(settings['source_stage'], 'workflow_dispatch', requested), 1)
        self.assertIs(expression(settings['retain_checkpoint'], 'workflow_dispatch', requested), False)


if __name__ == '__main__':
    unittest.main()
