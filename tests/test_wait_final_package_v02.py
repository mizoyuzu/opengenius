import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_final_genius_package_v02 as orchestrator

# The waiter lives outside the repository and is loaded without executing main().
import importlib.util
WAITER_PATH = Path('/tmp/opengenius-wait-final-package-v02.py')
spec = importlib.util.spec_from_file_location('_wait_final_package_v02', WAITER_PATH)
waiter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(waiter)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding='utf-8')
    return path


class FinalPackageWaiterV02Tests(unittest.TestCase):
    def fixture(self, root, terminal_status='completed', roots=1000):
        root = Path(root)
        campaign = root / 'campaign'
        growth = campaign / 'batch-0001/growth'
        graph = write(growth / 'accepted-graphs.json', {
            'accepted_roots': [{'root_pid': f'{i:016X}', 'ordered_target_pids': [f'{i+1:016X}']}
                               for i in range(roots)]})
        runtime = waiter.RUNTIME_MANIFEST
        entry = runtime.parent / 'scripts/expand_genuine_roots.py'
        args = [str(waiter.ROOT / '.venv/bin/python'), str(entry),
                '--target-outgoing-roots', '1000', '--global-request-budget', '6500',
                '--max-attempts', '2800', '--batch-size', '44', '--batch-request-budget', '200',
                '--interval', '3', '--candidate-limit', '100', '--stop-after-batches', '20',
                '--source-policy', 'authenticated-watch-radio-only-v1',
                '--collector-collection-mode', 'radio-only-v1', '--output', str(campaign)]
        invocation = write(campaign / 'invocation.json', {
            'argv': args, 'runtime_manifest_sha256': waiter.RUNTIME_MANIFEST_SHA256,
            'script_sha256': waiter.LIVE_SCRIPT_SHA256, 'no_limits_raised': True,
        })
        process = write(campaign / 'process.json', {
            **json.loads(invocation.read_text()), 'worker_pid': waiter.LIVE_PID, 'started_unix': 1.0,
        })
        recovered = write(campaign / 'recovered.json', {
            'successful_outgoing_roots': 840, 'http_requests': 4225,
            'attempted_pids': [str(i) for i in range(1925)],
        })
        preservation = write(campaign / 'preservation.json', {
            'status': 'passed_recovered_checkpoint_preservation_before_new_transport',
            'progress_sha256': sha(recovered), 'roots': 840, 'edges': 19527,
            'http_total': 4225, 'attempts': 1925,
            'new_authenticated_radio_roots': 14, 'new_radio_edges': 535,
        })
        progress = write(campaign / 'progress.json', {
            'status': 'target_reached', 'successful_outgoing_roots': roots,
            'http_requests': 4225, 'attempted_pids': [str(i) for i in range(1925)],
            'batches': [{'growth_output': str(growth),
                         'growth_receipt': {'path': str(graph), 'sha256': sha(graph)}}],
        })
        terminal = write(campaign / 'terminal.json', {
            'status': terminal_status, 'returncode': 0 if terminal_status == 'completed' else 1,
            'progress_status': 'target_reached', 'progress_sha256': sha(progress),
            'runtime_manifest_sha256': waiter.RUNTIME_MANIFEST_SHA256,
            'invocation_sha256': sha(invocation), 'pretransport_gate_passed': True,
            'roots': roots, 'accepted_graphs_sha256': sha(graph),
            'http_requests_total': 4225, 'attempted_pids': 1925,
        })
        values = {
            'LIVE_PID': waiter.LIVE_PID, 'LIVE_SCRIPT': waiter.LIVE_SCRIPT,
            'LIVE_SCRIPT_SHA256': waiter.LIVE_SCRIPT_SHA256,
            'RUNTIME_MANIFEST': runtime, 'RUNTIME_MANIFEST_SHA256': waiter.RUNTIME_MANIFEST_SHA256,
            'PROCESS': process, 'INVOCATION': invocation, 'TERMINAL': terminal,
            'RECOVERED_CHECKPOINT': recovered, 'PRESERVATION': preservation,
            'PROGRESS': progress, 'ORCHESTRATOR': waiter.ORCHESTRATOR,
            'ORCHESTRATOR_SHA256': waiter.ORCHESTRATOR_SHA256,
            'AUDIT_DIR': root / 'audit', 'PACKAGE_DIR': root / 'package',
            'ORCHESTRATOR_RECEIPT': root / 'receipt.json',
            'CAMPAIGN': campaign,
        }
        return values

    def test_successful_actual_v15_receipt_shape_passes_waiter_checkpoint_gate(self):
        with tempfile.TemporaryDirectory() as td:
            values = self.fixture(td)
            with patch.multiple(waiter, **values):
                result = waiter.validate_completed_checkpoint()
            self.assertEqual(result['active_roots'], 1000)
            self.assertEqual(result['status'], 'ready')

    def test_failed_terminal_and_under_target_graph_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            values = self.fixture(td, terminal_status='failed')
            with patch.multiple(waiter, **values), self.assertRaisesRegex(ValueError, 'not a successful exact target checkpoint'):
                waiter.validate_completed_checkpoint()
        with tempfile.TemporaryDirectory() as td:
            values = self.fixture(td, roots=999)
            with patch.multiple(waiter, **values), self.assertRaisesRegex(ValueError, 'remains below the total-genuine target'):
                waiter.validate_completed_checkpoint()

    def test_live_v15_process_and_invocation_are_exact_subset_shape(self):
        process = json.loads(waiter.PROCESS.read_text(encoding='utf-8'))
        invocation = json.loads(waiter.INVOCATION.read_text(encoding='utf-8'))
        self.assertEqual(process.get('worker_pid'), waiter.LIVE_PID)
        self.assertEqual(set(process), set(invocation) | {'started_unix', 'worker_pid'})
        self.assertTrue(all(process.get(key) == value for key, value in invocation.items()))
        self.assertIn('--global-request-budget', invocation['argv'])
        self.assertNotIn('global_http_cap', invocation)


if __name__ == '__main__':
    unittest.main()
