"""Exercise incremental log output and resource sampling without a browser/build."""
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from contextlib import redirect_stdout

from tools import build_telemetry as telemetry


class LogDeltaTest(unittest.TestCase):
    def test_quiet_builds_emit_no_historical_tail_and_append_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ninja.log'
            path.write_bytes(b'[1/2] CXX a.cc\n')
            state, initial = telemetry.log_delta(path, {})
            self.assertIn('CXX', initial['text'])
            for _ in range(3):
                state, quiet = telemetry.log_delta(path, state)
                self.assertEqual(quiet['new_bytes'], 0)
                self.assertEqual(quiet['text'], '')
            with path.open('ab') as stream:
                stream.write(b'[2/2] LINK chrome.dll\n')
            state, appended = telemetry.log_delta(path, state)
            self.assertEqual(appended['text'], '[2/2] LINK chrome.dll\n')
            self.assertEqual(telemetry.log_delta(path, state)[1]['text'], '')

    def test_bounded_new_output_and_truncated_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ninja.log'
            path.write_bytes(b'x' * 128)
            state, item = telemetry.log_delta(path, {}, limit=32)
            self.assertEqual(item['new_bytes'], 128)
            self.assertEqual(item['omitted_new_bytes'], 96)
            self.assertEqual(len(item['text']), 32)
            path.write_bytes(b'fresh')
            _, item = telemetry.log_delta(path, state)
            self.assertTrue(item['reset'])
            self.assertEqual(item['text'], 'fresh')

    def test_missing_log_is_not_an_error_or_a_false_progress_event(self):
        with tempfile.TemporaryDirectory() as directory:
            state, item = telemetry.log_delta(Path(directory) / 'absent', {})
            self.assertEqual(state['offset'], 0)
            self.assertTrue(item['missing'])
            self.assertEqual(item['new_bytes'], 0)


class MetricTest(unittest.TestCase):
    def test_cpu_and_owned_io_require_matching_process_identities(self):
        self.assertIsNone(telemetry.cpu_percent({'idle': 0, 'kernel': 0, 'user': 0}, None))
        self.assertEqual(telemetry.cpu_percent(
            {'idle': 160, 'kernel': 200, 'user': 100},
            {'idle': 100, 'kernel': 100, 'user': 0}), 70)
        def process(created, cpu, io):
            return {'created': created, 'cpu_100ns': cpu, 'read_bytes': io,
                    'write_bytes': io, 'working_set_bytes': 20, 'private_bytes': 30}
        old = {'1': process(10, 0, 0), '2': process(20, 0, 0)}
        current = {'1': process(10, 10_000_000, 100), '2': process(21, 10_000_000, 100)}
        result = telemetry.tree_delta(current, old, 1, 4)
        self.assertEqual(result['matched_processes'], 1)
        self.assertEqual(result['cpu_percent_of_host'], 25)
        self.assertEqual(result['read_bytes_delta'], 100)
        self.assertEqual(result['working_set_bytes'], 40)

    @unittest.skipUnless(os.name == 'nt', 'Native Windows telemetry')
    def test_windows_sampler_only_reads_the_owned_test_process_tree(self):
        result = telemetry.windows_snapshot(os.getpid())
        self.assertTrue(result['root_alive'])
        self.assertIn(str(os.getpid()), result['processes'])
        self.assertGreater(result['memory']['available_bytes'], 0)
        self.assertGreater(result['processes'][str(os.getpid())]['working_set_bytes'], 0)
        reused = telemetry.windows_snapshot(os.getpid(), result['root_created'] + 1)
        self.assertFalse(reused['root_alive'])
        self.assertEqual(reused['processes'], {})

    def test_failed_metrics_do_not_hide_new_logs_or_claim_successful_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / 'ninja.log'
            log.write_text('link started\n')
            args = SimpleNamespace(state=root / 'state.json', report=root / 'metrics.jsonl',
                                   disk=root, log=[log], root_pid=123)
            output = io.StringIO()
            with mock.patch.object(telemetry, 'windows_snapshot', side_effect=OSError('fixture unavailable')), redirect_stdout(output):
                first = telemetry.sample(args)
                second = telemetry.sample(args)
            self.assertEqual(first['metrics_error'], 'fixture unavailable')
            self.assertNotIn('cpu_percent', first)
            self.assertEqual(second['logs']['ninja.log']['new_bytes'], 0)
            self.assertEqual(output.getvalue().count('link started'), 1)
            self.assertEqual(len(args.report.read_text().splitlines()), 2)
            self.assertEqual(json.loads(args.state.read_text())['logs'][str(log.resolve())]['offset'], log.stat().st_size)


if __name__ == '__main__':
    unittest.main()
