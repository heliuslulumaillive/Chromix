import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import verify_google_services_candidate as candidate

def receipt():
    cases=candidate.retirement_cases()
    return {'version':1,'status':'passed','expected_cases':len(cases),
            'cases':[{**row,'exit_code':1,'signal':None,'timed_out':False,'spawn_error':None,
                      'stderr':row['diagnostic'],'passed':True,'cleanup':None} for row in cases]}

class LiteRetirementRuntimeTests(unittest.TestCase):
    def test_explicit_rejections_are_complete_and_versioned(self):
        self.assertEqual(candidate.validate_retirement_rejections(receipt()),31)

    def test_crashes_timeouts_missing_and_forged_positive_evidence_fail(self):
        mutations=[lambda r:r['cases'].pop(),lambda r:r['cases'][0].update(exit_code=0),
                   lambda r:r['cases'][0].update(exit_code=2),lambda r:r['cases'][0].update(exit_code=True),
                   lambda r:r['cases'][0].update(signal='SIGSEGV'),lambda r:r['cases'][0].update(timed_out=True),
                   lambda r:r['cases'][0].update(stderr=''),lambda r:r['cases'][0].update(argument='--fingerprint=off'),
                   lambda r:r['cases'][0].update(passed=False),lambda r:r['cases'][0].update(cleanup={'forced':True}),
                   lambda r:r['cases'][0].update(spawn_error='fixture'),lambda r:r.update(status='failed')]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value=receipt();mutation(value)
                with self.assertRaises(ValueError):candidate.validate_retirement_rejections(value)
        with self.assertRaises(ValueError):candidate.validate_retirement_rejections(None)

    def test_native_collector_and_receipt_validator_require_identical_inputs(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node unavailable')
        result=subprocess.run([node,'--input-type=module','-e',
            "import {retirementCases} from './tools/verify_fingerprint_modes_runtime.mjs'; console.log(JSON.stringify(retirementCases()));"],
            cwd=ROOT,capture_output=True,text=True,check=True,timeout=20)
        self.assertEqual(json.loads(result.stdout),candidate.retirement_cases())

if __name__=='__main__':unittest.main()
