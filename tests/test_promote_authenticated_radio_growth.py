"""Offline tests for frozen campaign promotion parent and receipt binding."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import promote_authenticated_radio_growth as promoter
from genuine_root_recovery import runner_config_sha256


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    return {'path': str(path.resolve()),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


class PromotionParentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sources = {}
        for key in ('track_snapshot', 'identity_map', 'baseline_state',
                    'baseline_graph_export', 'rebase_report'):
            self.sources[key] = write(self.root / (key + '.json'), {'source': key})
        self.parents = []
        for batch, roots in ((1, 2), (2, 3)):
            path = self.root / ('source-growth-%d' % batch) / 'accepted-graphs.json'
            roots_doc = [{'root_pid': '%016X' % index,
                          'ordered_target_pids': ['target']}
                         for index in range(1, roots + 1)]
            receipt = write(path, {'accepted_roots': roots_doc,
                                   'growth_runs': [{'batch': index}
                                                   for index in range(batch + 1)]})
            self.parents.append((path.parent, receipt, roots))
        initial_path = self.root / 'source-initial' / 'accepted-graphs.json'
        initial_doc = {'accepted_roots': [
            {'root_pid': '0000000000000001', 'ordered_target_pids': ['target']}],
            'growth_runs': [{'batch': 0}, {'batch': -1}]}
        initial_receipt = write(initial_path, initial_doc)
        self.initial_parent = (initial_path.parent, initial_receipt, 1)
        initial_parent = {'path': str(self.initial_parent[0]),
                          'sha256': self.initial_parent[1]['sha256']}
        self.config = {**self.sources,
                       'previous_growth': initial_parent,
                       'source_policy': None,
                       'batch_request_budget': 10,
                       'deadline': None}
        self.config = {key: value for key, value in self.config.items()
                       if key != 'source_policy'}
        batch_rows = []
        original_parent_receipt = {
            'path': self.initial_parent[1]['path'],
            'accepted_graphs_sha256': self.initial_parent[1]['sha256'],
        }
        for batch in (1, 2):
            directory = self.root / ('campaign-batch-%d' % batch)
            collector = directory / 'collector'
            summary = write(collector / 'summary.json', {
                'status': 'completed', 'http_requests': 5,
                'request_budget': 10,
                'results': [{'persistent_id': '%016X' % batch,
                             'status': 'collected'}],
            })
            manifest = write(collector / 'seeds.json', {'seeds': [{'persistent_id': '%016X' % batch}]})
            plan = write(directory / 'seed-plan.json', {'seeds': [{'persistent_id': '%016X' % batch}]})
            evidence = write(directory / 'seed-video-evidence.json', {'rows': []})
            snapshot = write(collector / ('%016X.json' % batch), {'seed': batch})
            intent_path = directory / 'intent.json'
            source_inputs = [self.config[key] for key in (
                'track_snapshot', 'identity_map', 'baseline_state',
                'baseline_graph_export', 'rebase_report')]
            intent = {
                'runner_config': self.config,
                'runner_config_sha256': runner_config_sha256(self.config),
                'library_sha256': 'a' * 64,
                'identity_map_sha256': self.sources['identity_map']['sha256'],
                'batch': batch, 'seed_pids': ['%016X' % batch],
                'seed_plan': plan, 'seed_video_evidence': evidence,
                'request_budget': 10,
                'previous_growth': original_parent_receipt,
                'source_inputs': source_inputs,
            }
            write(intent_path, intent)
            batch_rows.append({
                'batch': batch, 'status': 'completed', 'outcome': 'merged',
                'seed_pids': ['%016X' % batch], 'seed_plan': plan,
                'seed_manifest': {**manifest, 'seed_count': 1},
                'seed_video_evidence': evidence,
                'collector_summary': {**summary, 'http_requests': 5,
                                      'request_budget': 10},
                'request_budget': 10, 'http_requests': 5,
                'snapshot_inputs': [snapshot],
                'growth_output': str(self.parents[batch - 1][0]),
                'growth_receipt': self.parents[batch - 1][1],
                'attempted_pids': ['%016X' % batch],
            })
            source_parent = self.parents[batch - 1]
            original_parent_receipt = {
                'path': source_parent[1]['path'],
                'accepted_graphs_sha256': source_parent[1]['sha256'],
            }
        self.config['previous_growth'] = {
            'path': str(self.initial_parent[0]),
            'sha256': self.initial_parent[1]['sha256'],
        }
        self.progress = {
            'status': 'paused_at_checkpoint', 'config': self.config,
            'batches': batch_rows, 'attempted_pids': ['0000000000000001', '0000000000000002'],
            'http_requests': 10, 'successful_outgoing_roots': 3,
        }
        self.progress_path = self.root / 'frozen-progress.json'
        write(self.progress_path, self.progress)

    def tearDown(self):
        self.temp.cleanup()

    def test_second_intent_keeps_legacy_parent_while_promoted_merge_chains(self):
        outputs = []

        def fake_prepare(*, snapshot_paths, output_dir, **_kwargs):
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            proofs, sanitized = [], []
            for source in snapshot_paths:
                pid = Path(source).stem
                proof = write(output_dir / (pid + '.proof.json'), {
                    'collector_snapshot': {'path': str(Path(source).resolve())}})
                snapshot = write(output_dir / (pid + '.snapshot.json'), {
                    'radio_only_source_proof': proof})
                proofs.append(proof)
                sanitized.append(snapshot)
            return {'source_policy': promoter.POLICY, 'proofs': proofs,
                    'snapshot_inputs': sanitized, 'ineligible': [],
                    'output_dir': str(output_dir.resolve())}

        def fake_subprocess(command, check=False):
            self.assertFalse(check)
            output = Path(command[command.index('--output') + 1])
            previous = command[command.index('--previous-growth') + 1]
            outputs.append((output, Path(previous)))
            before = json.loads((Path(previous) / 'accepted-graphs.json').read_text())
            roots = list(before['accepted_roots'])
            pid = '%016X' % (len(outputs) + 10)
            roots.append({'root_pid': pid, 'ordered_target_pids': ['target']})
            runs = list(before['growth_runs'])
            run = {'batch': len(outputs), 'source_policy': promoter.POLICY}
            if len(outputs) == 1:
                run['source_policy_transition'] = {
                    'legacy_run_count': len(before['growth_runs'])}
            runs.append(run)
            growth = {'accepted_roots': roots, 'growth_runs': runs}
            write(output / 'accepted-graphs.json', growth)
            return type('Completed', (), {'returncode': 0})()

        output_dir = self.root / 'promotion-output'
        ledger = self.root / 'v2-ledger.json'
        with patch.object(promoter, '_verify_frozen_campaign', return_value=(
                'a' * 64, self.sources['identity_map']['sha256'])), \
                patch.object(promoter, '_graph_roots', side_effect=promoter._graph_roots), \
                patch.object(promoter, 'prepare_radio_only_snapshots', side_effect=fake_prepare), \
                patch.object(promoter.subprocess, 'run', side_effect=fake_subprocess), \
                patch('genuine_root_recovery.replay_growth_checkpoint', side_effect=lambda **kwargs: {
                    'accepted_graphs_sha256': hashlib.sha256(
                        (Path(kwargs['previous_growth']) / 'accepted-graphs.json').read_bytes()).hexdigest(),
                    'outgoing_root_count': promoter._graph_roots(kwargs['previous_growth'])[0],
                }):
            result = promoter.promote(
                track_snapshot=self.sources['track_snapshot']['path'],
                identity_map=self.sources['identity_map']['path'],
                baseline_state=self.sources['baseline_state']['path'],
                baseline_graph_export=self.sources['baseline_graph_export']['path'],
                rebase_report=self.sources['rebase_report']['path'],
                frozen_progress=self.progress_path, output_dir=output_dir,
                ledger_output=ledger)
        self.assertEqual(len(outputs), 2)
        self.assertEqual(outputs[0][1], self.parents[1][0])
        self.assertEqual(outputs[1][1], outputs[0][0])
        report = json.loads(Path(result['report_path']).read_text())
        self.assertEqual(report['http_request_delta'], 0)
        self.assertEqual(report['attempt_delta'], 0)
        self.assertEqual(report['promoted_outgoing_roots'], 5)
        final_graph = json.loads((outputs[1][0] / 'accepted-graphs.json').read_text())
        self.assertEqual(len(final_graph['growth_runs']), 5)
        self.assertEqual(final_graph['growth_runs'][3]['source_policy_transition']['legacy_run_count'], 3)
        self.assertEqual(json.loads(ledger.read_text())['http_requests_total'], 10)


if __name__ == '__main__':
    unittest.main()
