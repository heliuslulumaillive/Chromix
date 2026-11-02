import unittest
from tools.verify_native_browser_tests import REQUIRED, verify


class NativeBrowserAcceptanceTests(unittest.TestCase):
    def good(self):
        return {'per_iteration_data': [{name: [{'status': 'SUCCESS'}] for name in REQUIRED}]}

    def test_requires_executed_cases_not_only_listed_tests(self):
        with self.assertRaises(ValueError):
            verify({'all_tests': list(REQUIRED), 'per_iteration_data': [{}]})

    def test_complete_single_execution_passes(self):
        self.assertEqual(verify(self.good())['executed'], 4)

    def test_missing_failed_skipped_or_retried_cases_fail(self):
        for value in ([], [{'status': 'FAILURE'}], [{'status': 'SKIPPED'}], [{'status': 'FAILURE'}, {'status': 'SUCCESS'}]):
            data = self.good()
            data['per_iteration_data'][0][next(iter(REQUIRED))] = value
            with self.assertRaises(ValueError):
                verify(data)
