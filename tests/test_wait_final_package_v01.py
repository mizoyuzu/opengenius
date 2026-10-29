import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path('/tmp/opengenius-wait-final-package-v01.py')
SPEC = importlib.util.spec_from_file_location('wait_final_package_v01', SCRIPT)
waiter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(waiter)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding='utf-8')
    return path


class WaitFinalPackageTests(unittest.TestCase):
    def fixture(self, root, count=1000, status='target_reached'):
        campaign = root / 'campaign'
        growth = campaign / 'batch-0001/growth'
        growth.mkdir(parents=True)
        graph = write_json(growth / 'accepted-graphs.json', {
            'accepted_roots': [
                {'root_pid': f'{index:016X}', 'ordered_target_pids': [f'{index + 1:016X}']}
                for index in range(count)
            ]
        })
        progress = write_json(campaign / 'progress.json', {
            'status': status, 'successful_outgoing_roots': count,
            'batches': [{'growth_output': str(growth), 'growth_receipt': {
                'path': str(graph), 'sha256': digest(graph)}}],
        })
        runtime_root = root / 'runtime'
        entrypoint = runtime_root / 'scripts/expand_genuine_roots.py'
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text('sealed fake entrypoint', encoding='utf-8')
        runtime = write_json(runtime_root / 'runtime-manifest.json', {'modules': []})
        worker = root / 'worker.py'
        worker.write_text('sealed fake worker', encoding='utf-8')
        orchestrator = root / 'orchestrator.py'
        orchestrator.write_text('sealed fake orchestrator', encoding='utf-8')
        process = write_json(campaign / 'process.json', {
            'supervisor_pid': 77, 'runtime_manifest_sha256': digest(runtime)})
        args = ['--target-outgoing-roots', '1000', '--global-request-budget', '6500',
                '--max-attempts', '2800', '--batch-size', '20', '--batch-request-budget', '200',
                '--candidate-limit', '100', '--interval', '3', '--stop-after-batches', '20',
                '--source-policy', 'authenticated-watch-radio-only-v1',
                '--collector-collection-mode', 'radio-only-v1', '--output', str(campaign)]
        invocation = write_json(campaign / 'invocation.json', {
            'argv': ['/python', str(entrypoint), *args], 'no_limits_raised': True,
            'global_http_cap': 6500})
        child = write_json(campaign / 'child.json', {'invocation_sha256': digest(invocation)})
        terminal = write_json(campaign / 'terminal.json', {
            'status': 'completed', 'returncode': 0, 'progress_sha256': digest(progress),
            'runtime_manifest_sha256': digest(runtime), 'invocation_sha256': digest(invocation),
            'accepted_graphs_sha256': digest(graph)})
        return locals()

    def patches(self, fixture, supervisor_dir):
        campaign = fixture['campaign']
        return {
            'LIVE_PID': 77, 'LIVE_SCRIPT': fixture['worker'],
            'LIVE_SCRIPT_SHA256': digest(fixture['worker']),
            'RUNTIME_MANIFEST': fixture['runtime'],
            'RUNTIME_MANIFEST_SHA256': digest(fixture['runtime']),
            'RUNTIME_ENTRYPOINT': fixture['entrypoint'],
            'PROGRESS': fixture['progress'], 'SUPERVISOR_PROCESS': fixture['process'],
            'INVOCATION': fixture['invocation'], 'CHILD': fixture['child'],
            'TERMINAL': fixture['terminal'], 'ORCHESTRATOR': fixture['orchestrator'],
            'ORCHESTRATOR_SHA256': digest(fixture['orchestrator']),
            'AUDIT_DIR': fixture['root'] / 'audits', 'PACKAGE_DIR': fixture['root'] / 'package',
            'ORCHESTRATOR_RECEIPT': fixture['root'] / 'receipt.json',
            'SUPERVISOR_DIR': supervisor_dir,
        }

    def test_completed_target_checkpoint_binds_real_receipt_shapes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture = self.fixture(root)
            with patch.multiple(waiter, **self.patches(fixture, root / 'waiter')):
                checkpoint = waiter.validate_completed_checkpoint()
            self.assertEqual(checkpoint['active_roots'], 1000)
            self.assertEqual(checkpoint['progress_sha256'], digest(fixture['progress']))

    def test_under_target_or_running_checkpoint_refuses(self):
        for count, status in ((999, 'target_reached'), (1000, 'running')):
            with self.subTest(count=count, status=status), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                fixture = self.fixture(root, count=count, status=status)
                with patch.multiple(waiter, **self.patches(fixture, root / 'waiter')):
                    with self.assertRaises(ValueError):
                        waiter.validate_completed_checkpoint()
                self.assertFalse((root / 'waiter').exists())

    def test_waiter_invokes_only_structured_orchestrator_after_gates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture = self.fixture(root)
            supervisor_dir = root / 'waiter'
            calls = []

            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                kwargs['stdout'].write(json.dumps({'status': 'completed'}))
                return waiter.subprocess.CompletedProcess(command, 0)

            with patch.multiple(waiter, **self.patches(fixture, supervisor_dir)), \
                    patch.object(waiter, 'live_cmdline_matches', side_effect=[True, False]), \
                    patch.object(waiter.subprocess, 'run', side_effect=fake_run):
                self.assertEqual(waiter.run(), 0)
            self.assertEqual(len(calls), 1)
            command, kwargs = calls[0]
            self.assertEqual(command, [str(waiter.ROOT / '.venv/bin/python'), str(fixture['orchestrator'])])
            self.assertNotIn('shell', kwargs)
            terminal = json.loads((supervisor_dir / 'terminal.json').read_text())
            self.assertEqual(terminal['status'], 'completed')
            self.assertEqual(terminal['network_requests'], 0)


if __name__ == '__main__':
    unittest.main()
