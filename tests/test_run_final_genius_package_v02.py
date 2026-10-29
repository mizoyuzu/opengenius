import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_final_genius_package_v02 as runner


def write(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding='utf-8')
    return path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FinalPackageOrchestratorV02Tests(unittest.TestCase):
    def checkpoint(self, root, count):
        campaign = Path(root) / 'campaign'
        growth = campaign / 'batch-0001' / 'growth'
        graph = write(growth / 'accepted-graphs.json', {
            'accepted_roots': [{'root_pid': f'{n:016X}', 'ordered_target_pids': [f'{n+1:016X}']}
                               for n in range(count)]})
        progress = write(campaign / 'progress.json', {
            'status': 'target_reached', 'successful_outgoing_roots': count,
            'http_requests': 4225, 'attempted_pids': [f'{n:016X}' for n in range(1925)],
            'batches': [{'growth_output': str(growth),
                         'growth_receipt': {'path': str(graph), 'sha256': sha(graph)}}]})
        terminal = write(campaign / 'terminal.json', {
            'status': 'completed', 'returncode': 0, 'progress_sha256': sha(progress),
            'accepted_graphs_sha256': sha(graph),
            'runtime_manifest_sha256': runner.RUNTIME_MANIFEST_SHA256,
            'invocation_sha256': 'fixture-only'})
        return progress, terminal

    def test_under_target_refuses_before_lineage_or_any_write(self):
        with tempfile.TemporaryDirectory() as td:
            progress, terminal = self.checkpoint(td, 999)
            before = sorted(str(p) for p in Path(td).rglob('*'))
            with self.assertRaisesRegex(runner.GateError, 'below 1000 accepted roots'):
                runner.validate_checkpoint(progress, terminal)
            self.assertEqual(before, sorted(str(p) for p in Path(td).rglob('*')))

    def test_target_count_cannot_bypass_missing_real_v15_lineage(self):
        with tempfile.TemporaryDirectory() as td:
            progress, terminal = self.checkpoint(td, 1000)
            with self.assertRaisesRegex(runner.GateError, 'v15 recovery/continuation lineage receipts are incomplete'):
                runner.validate_checkpoint(progress, terminal)

    def test_success_terminal_and_pretransport_lineage_shape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            campaign = root / 'campaign'
            growth = campaign / 'batch-0001' / 'growth'
            rows = [{'root_pid': f'{n:016X}', 'ordered_target_pids': [f'{n+1:016X}']}
                    for n in range(1000)]
            graph = write(growth / 'accepted-graphs.json', {'accepted_roots': rows})
            config = {
                'auth_path': '/private/auth.json',
                'track_snapshot': {'path': '/inputs/tracks.json'},
                'identity_map': {'path': '/inputs/map.json'},
                'baseline_state': {'path': '/inputs/state.json'},
                'baseline_graph_export': {'path': '/inputs/graph.json'},
                'rebase_report': {'path': '/inputs/report.json'},
                'external_seed_video_evidence': {'path': '/inputs/seeds.json'},
                'prior_attempt_ledger': {'path': '/inputs/ledger.json'},
                'target_outgoing_roots': 1000, 'global_request_budget': 6500,
                'max_attempts': 2800, 'batch_size': 44, 'batch_request_budget': 200,
                'candidate_limit': 100, 'interval': 3.0,
                'source_policy': 'authenticated-watch-radio-only-v1',
                'collector_collection_mode': 'radio-only-v1',
                'authorized_retry_release_pids': [],
            }
            args = []
            config_flags = (
                ('--auth', 'auth_path', False), ('--track-snapshot', 'track_snapshot', True),
                ('--identity-map', 'identity_map', True), ('--baseline-state', 'baseline_state', True),
                ('--baseline-graph-export', 'baseline_graph_export', True),
                ('--rebase-report', 'rebase_report', True),
                ('--seed-video-evidence', 'external_seed_video_evidence', True),
                ('--prior-attempt-ledger', 'prior_attempt_ledger', True),
            )
            for flag, key, nested in config_flags:
                value = config[key]['path'] if nested else config[key]
                args.extend([flag, value])
            for flag, key in (
                ('--target-outgoing-roots', 'target_outgoing_roots'),
                ('--global-request-budget', 'global_request_budget'),
                ('--max-attempts', 'max_attempts'), ('--batch-size', 'batch_size'),
                ('--batch-request-budget', 'batch_request_budget'),
                ('--candidate-limit', 'candidate_limit'), ('--interval', 'interval'),
                ('--source-policy', 'source_policy'),
                ('--collector-collection-mode', 'collector_collection_mode'),
            ):
                args.extend([flag, '3' if key == 'interval' else str(config[key])])
            args.extend(['--stop-after-batches', '20', '--output', str(campaign)])
            runtime_manifest = runner.RUNTIME_MANIFEST
            manifest_sha = runner.RUNTIME_MANIFEST_SHA256
            runner_script = runner.RECOVERY_SCRIPT
            old_checkpoint = runner.V13_FAILED_CHECKPOINT.read_bytes()
            failed_terminal = runner.CAMPAIGN / 'v13-continuation-supervisor-terminal-v05.json'
            old_invocation = runner.V13_FAILED_INVOCATION.read_bytes()
            v13_checkpoint_path = campaign / 'v13-accepted826-progress-checkpoint-v01.json'
            v13_checkpoint_path.write_bytes(old_checkpoint)
            v13_terminal_path = campaign / 'v13-continuation-supervisor-terminal-v05.json'
            v13_terminal_path.write_bytes(failed_terminal.read_bytes())
            v13_invocation_path = campaign / 'v13-continuation-invocation-v05.json'
            v13_invocation_path.write_bytes(old_invocation)
            recovered = write(campaign / 'recovered.json', {
                'successful_outgoing_roots': 840, 'http_requests': 4225,
                'attempted_pids': [f'{n:016X}' for n in range(1925)],
                'batches': [{}, {}],
            })
            preservation = write(campaign / 'preservation.json', {
                'status': 'passed_recovered_checkpoint_preservation_before_new_transport',
                'progress_sha256': sha(recovered), 'roots': 840, 'edges': 19527,
                'http_total': 4225, 'attempts': 1925,
                'new_authenticated_radio_roots': 14, 'new_radio_edges': 535,
            })
            argv = [str(runner.ROOT / '.venv/bin/python'),
                    str(runner.RUNTIME_ROOT / 'scripts/expand_genuine_roots.py'), *args]
            invocation = write(campaign / 'invocation.json', {
                'argv': argv, 'runtime_manifest_sha256': manifest_sha,
                'script_sha256': sha(runner_script),
                'source_v05_invocation_sha256': sha(v13_invocation_path),
                'progress_before_sha256': sha(v13_checkpoint_path),
                'no_limits_raised': True,
            })
            process = write(campaign / 'process.json', {
                **json.loads(invocation.read_text()), 'worker_pid': 321, 'started_unix': 123.5,
            })
            progress = write(campaign / 'progress.json', {
                'status': 'target_reached', 'successful_outgoing_roots': 1000,
                'http_requests': 4225, 'attempted_pids': [f'{n:016X}' for n in range(1925)],
                'config': config,
                'batches': [{'growth_output': str(growth),
                             'growth_receipt': {'path': str(graph), 'sha256': sha(graph)}}],
            })
            terminal = write(campaign / 'terminal.json', {
                'status': 'completed', 'returncode': 0, 'progress_sha256': sha(progress),
                'accepted_graphs_sha256': sha(graph), 'runtime_manifest_sha256': manifest_sha,
                'invocation_sha256': sha(invocation), 'progress_status': 'target_reached',
                'roots': 1000, 'http_requests_total': 4225, 'attempted_pids': 1925,
                'pretransport_gate_passed': True,
            })
            values = {
                'CAMPAIGN': campaign, 'SUPERVISOR_TERMINAL': terminal,
                'SUPERVISOR_PROCESS': process, 'CONTINUATION_INVOCATION': invocation,
                'RECOVERY_PROGRESS_CHECKPOINT': recovered, 'PRESERVATION_AUDIT': preservation,
                'RUNTIME_MANIFEST': runtime_manifest, 'RUNTIME_MANIFEST_SHA256': manifest_sha,
                'RUNTIME_ROOT': runtime_manifest.parent, 'RECOVERY_SCRIPT': runner_script,
                'RECOVERY_SCRIPT_SHA256': sha(runner_script),
                'V13_FAILED_TERMINAL': v13_terminal_path,
                'V13_FAILED_CHECKPOINT': v13_checkpoint_path,
                'V13_FAILED_INVOCATION': v13_invocation_path,
            }
            with patch.multiple(runner, **values):
                result = runner.validate_checkpoint(progress, terminal)
            self.assertEqual(result['active_roots'], 1000)
            self.assertEqual(result['progress_status'], 'target_reached')
            self.assertEqual(result['invocation_lineage']['worker_pid'], 321)

    def test_unfinished_progress_refuses_before_lineage(self):
        with tempfile.TemporaryDirectory() as td:
            progress, terminal = self.checkpoint(td, 1000)
            doc = json.loads(progress.read_text())
            doc['status'] = 'running'
            progress.write_text(json.dumps(doc), encoding='utf-8')
            terminal_doc = json.loads(terminal.read_text())
            terminal_doc['progress_sha256'] = sha(progress)
            terminal.write_text(json.dumps(terminal_doc), encoding='utf-8')
            with self.assertRaisesRegex(runner.GateError, 'target_reached terminal status'):
                runner.validate_checkpoint(progress, terminal)


if __name__ == '__main__':
    unittest.main()
