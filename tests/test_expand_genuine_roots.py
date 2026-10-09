"""Offline checks for bounded genuine recommendation-root growth."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import expand_genuine_roots as runner


LIBRARY = 'a' * 64


def track(pid, artist='Artist', album=None):
    return {'persistent_id': pid, 'title': 'Song ' + pid[-1], 'artist': artist,
            'album': album or ('Album ' + pid[-1]), 'duration_ms': 100000}


class Matcher:
    def artist_credits(self, artist):
        return (artist,)


class GenuineRootRunnerTests(unittest.TestCase):
    def test_request_boundary_requires_an_exact_authenticated_request_stop(self):
        valid = {'schema_version': 1, 'account_authentication_verified': True, 'status': 'stopped',
                 'http_requests': 9, 'request_budget': 9, 'error_type': 'RuntimeError',
                 'error_location': {'function': 'request'}}
        self.assertTrue(runner._valid_collector_batch(valid))
        invalid = [
            {**valid, 'http_requests': 8},
            {**valid, 'request_budget': 10},
            {**valid, 'http_requests': None, 'request_budget': None},
            {**valid, 'account_authentication_verified': False},
            {**valid, 'error_type': 'HTTPError'},
            {**valid, 'error_location': {'function': 'parse'}},
            {**valid, 'http_error_status': 429},
        ]
        for summary in invalid:
            with self.subTest(summary=summary):
                self.assertFalse(runner._valid_collector_batch(summary))

    def test_choose_batch_stops_when_every_candidate_was_already_attempted(self):
        tracks = [track('0000000000000001'), track('0000000000000002')]
        roots = {'0000000000000001': ['0000000000000002']}
        selected = runner.choose_batch(
            tracks, Matcher(), roots,
            {'0000000000000002'}, 2, LIBRARY)
        self.assertEqual(selected, [])

    def test_choose_batch_prefers_known_hints_then_incoming_only_frontier(self):
        pids = ['0000000000000001', '0000000000000002',
                '0000000000000003', '0000000000000004']
        tracks = [track(pid, artist='Artist ' + pid[-1]) for pid in pids]
        roots = {pids[0]: [pids[1]], pids[2]: [pids[0]]}
        selected = runner.choose_batch(
            tracks, Matcher(), roots, set(), 2, LIBRARY, {pids[3]})
        self.assertEqual(selected[0], pids[3])
        self.assertEqual(selected[1], pids[1])
        self.assertNotIn(pids[0], selected)  # Already has outgoing edges.
        self.assertNotIn(pids[2], selected)  # Outgoing root, even without incoming edges.

    def _run_fixture(self, root, *, max_attempts=2, request_budget=2, batch_size=1):
        tracks = [track('0000000000000001'), track('0000000000000002')]
        files = {}
        for name, payload in {
                'tracks.json': '{}', 'identity.json': '{}', 'state.json': '{}',
                'graphs.json': json.dumps({'accepted_roots': []}),
                'rebase.json': '{}', 'auth.json': '{}'} .items():
            path = root / name
            path.write_text(payload, encoding='utf-8')
            files[name] = path
        output = root / 'run'
        args = type('Args', (), {
            'track_snapshot': files['tracks.json'], 'identity_map': files['identity.json'],
            'baseline_state': files['state.json'], 'baseline_graph_export': files['graphs.json'],
            'rebase_report': files['rebase.json'], 'output': output,
            'auth': files['auth.json'], 'target_outgoing_roots': 1,
            'global_request_budget': request_budget, 'max_attempts': max_attempts,
            'batch_size': batch_size, 'batch_request_budget': 1, 'interval': 3,
            'deadline': None, 'plan_only': False})()

        def collect(command):
            collector_dir = Path(command[command.index('--output') + 1])
            plan_path = Path(command[command.index('--seed-plan') + 1])
            evidence_path = Path(command[command.index('--seed-video-evidence') + 1])
            seeds = json.loads(plan_path.read_text(encoding='utf-8'))['seeds']
            collector_dir.mkdir(parents=True, exist_ok=True)
            intent = json.loads((plan_path.parent / 'intent.json').read_text(encoding='utf-8'))
            (collector_dir / 'seeds.json').write_text(json.dumps({
                'library_sha256': LIBRARY,
                'identity_map_sha256': runner._sha(files['identity.json'].read_bytes()),
                'seed_plan_sha256': runner._sha(plan_path.read_bytes()),
                'seed_video_evidence_sha256': runner._sha(evidence_path.read_bytes()),
                'seeds': seeds,
            }), encoding='utf-8')
            (collector_dir / 'summary-001.json').write_text(json.dumps({
                'schema_version': 1, 'status': 'completed',
                'account_authentication_verified': True, 'library_sha256': LIBRARY,
                'http_requests': 1, 'request_budget': intent['request_budget'],
                'results': [{'persistent_id': row['persistent_id'],
                             'status': 'search_unresolved'} for row in seeds],
            }), encoding='utf-8')
            return 0

        return args, tracks, collect

    def test_all_unresolved_batches_consume_attempts_and_terminate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, collect = self._run_fixture(root)
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}), \
                    patch.object(runner, '_invoke', side_effect=collect):
                progress = runner.run(args)
            self.assertEqual(progress['status'], 'budget_or_attempt_limit')
            self.assertEqual(progress['attempted_pids'], ['0000000000000001', '0000000000000002'])
            self.assertEqual(progress['http_requests'], 2)
            self.assertEqual(len(progress['batches']), 2)
            self.assertTrue(all(row['outcome'] == 'authenticated_no_graphs'
                                for row in progress['batches']))

    def test_resume_rejects_changed_source_hashes_before_more_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, collect = self._run_fixture(root, max_attempts=1, request_budget=1)
            patches = [
                patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})),
                patch.object(runner, 'IdentityMap', return_value=Matcher()),
                patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}),
                patch.object(runner, '_invoke', side_effect=collect),
            ]
            with patches[0], patches[1], patches[2], patches[3]:
                runner.run(args)
            args.baseline_state.write_text('{"changed": true}', encoding='utf-8')
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_invoke', side_effect=AssertionError('must not collect')):
                with self.assertRaisesRegex(ValueError, 'Resume inputs or runner limits differ'):
                    runner.run(args)

    def test_resume_rejects_changed_batch_summary_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, collect = self._run_fixture(root, max_attempts=1, request_budget=1)
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}), \
                    patch.object(runner, '_invoke', side_effect=collect):
                runner.run(args)
            summary_path = root / 'run' / 'batch-0001' / 'collector' / 'summary-001.json'
            summary_path.write_text('{"changed": true}', encoding='utf-8')
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_invoke', side_effect=AssertionError('must not collect')):
                with self.assertRaisesRegex(ValueError, 'file bound by saved progress changed'):
                    runner.run(args)

    def test_completed_v01_progress_migrates_only_when_its_recorded_batch_cap_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, collect = self._run_fixture(root, max_attempts=1, request_budget=1)
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}), \
                    patch.object(runner, '_invoke', side_effect=collect):
                runner.run(args)
            progress_path = root / 'run' / 'progress.json'
            progress = json.loads(progress_path.read_text(encoding='utf-8'))
            progress['config'].pop('batch_request_budget')
            progress['config'].pop('deadline')
            progress_path.write_text(json.dumps(progress), encoding='utf-8')
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}), \
                    patch.object(runner, '_invoke', side_effect=AssertionError('must not collect')):
                resumed = runner.run(args)
            self.assertEqual(resumed['batches'][0]['request_budget'], 1)
            self.assertEqual(resumed['config']['batch_request_budget'], 1)

    def test_request_boundary_does_not_mark_unreturned_planned_seeds_attempted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, _ = self._run_fixture(
                root, max_attempts=2, request_budget=2, batch_size=2)

            def collect(command):
                collector_dir = Path(command[command.index('--output') + 1])
                plan_path = Path(command[command.index('--seed-plan') + 1])
                evidence_path = Path(command[command.index('--seed-video-evidence') + 1])
                plan = json.loads(plan_path.read_text())
                seeds = plan['seeds']
                collector_dir.mkdir(parents=True, exist_ok=True)
                (collector_dir / 'seeds.json').write_text(json.dumps({
                    'library_sha256': LIBRARY,
                    'identity_map_sha256': runner._sha(args.identity_map.read_bytes()),
                    'seed_plan_sha256': runner._sha(plan_path.read_bytes()),
                    'seed_video_evidence_sha256': runner._sha(evidence_path.read_bytes()),
                    'seeds': seeds}), encoding='utf-8')
                if 'batch-0001' in str(collector_dir):
                    status = {'status': 'stopped', 'http_requests': 1,
                              'request_budget': 1, 'error_type': 'RuntimeError',
                              'error_location': {'function': 'request'},
                              'results': [{'persistent_id': seeds[0]['persistent_id'],
                                           'status': 'search_unresolved'}]}
                else:
                    status = {'status': 'completed', 'http_requests': 1,
                              'request_budget': 1,
                              'results': [{'persistent_id': row['persistent_id'],
                                           'status': 'search_unresolved'} for row in seeds]}
                (collector_dir / 'summary-001.json').write_text(json.dumps({
                    'schema_version': 1, 'account_authentication_verified': True, **status,
                    'library_sha256': LIBRARY,
                }), encoding='utf-8')
                return 0

            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}), \
                    patch.object(runner, '_invoke', side_effect=collect):
                progress = runner.run(args)
            self.assertEqual(len(progress['batches']), 2)
            self.assertEqual(len(progress['batches'][0]['seed_pids']), 2)
            self.assertEqual(len(progress['batches'][1]['seed_pids']), 1)
            self.assertEqual(progress['batches'][0]['seed_pids'][0],
                             progress['rejected_or_unresolved_pids'][0])
            self.assertEqual(progress['batches'][1]['seed_pids'],
                             [progress['batches'][0]['seed_pids'][1]])
            self.assertEqual(set(progress['attempted_pids']),
                             {'0000000000000001', '0000000000000002'})

    def test_interruption_after_collector_recovers_without_repeating_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, collect = self._run_fixture(
                root, max_attempts=1, request_budget=2)
            common = [
                patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})),
                patch.object(runner, 'IdentityMap', return_value=Matcher()),
                patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}),
            ]
            with common[0], common[1], common[2], \
                    patch.object(runner, '_invoke', side_effect=collect), \
                    patch.object(runner, '_promote_orphan', side_effect=RuntimeError('simulated crash')):
                with self.assertRaisesRegex(RuntimeError, 'simulated crash'):
                    runner.run(args)
            with common[0], common[1], common[2], \
                    patch.object(runner, '_invoke', side_effect=AssertionError('must not collect again')):
                progress = runner.run(args)
            self.assertEqual(progress['attempted_pids'], ['0000000000000001'])
            self.assertEqual(progress['http_requests'], 1)
            self.assertEqual(len(progress['batches']), 1)
            self.assertEqual(progress['batches'][0]['outcome'], 'authenticated_no_graphs')

    def test_interruption_after_merge_replays_output_before_progress_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, _ = self._run_fixture(root, max_attempts=1, request_budget=8)
            args.batch_request_budget = 8
            args.global_request_budget = 8
            pid = '0000000000000001'

            def invoke(command):
                if 'probe_ytmusic_batch.py' in command[1]:
                    collector_dir = Path(command[command.index('--output') + 1])
                    plan_path = Path(command[command.index('--seed-plan') + 1])
                    evidence_path = Path(command[command.index('--seed-video-evidence') + 1])
                    seeds = json.loads(plan_path.read_text())['seeds']
                    collector_dir.mkdir(parents=True, exist_ok=True)
                    (collector_dir / 'seeds.json').write_text(json.dumps({
                        'library_sha256': LIBRARY,
                        'identity_map_sha256': runner._sha(args.identity_map.read_bytes()),
                        'seed_plan_sha256': runner._sha(plan_path.read_bytes()),
                        'seed_video_evidence_sha256': runner._sha(evidence_path.read_bytes()),
                        'seeds': seeds}), encoding='utf-8')
                    video = 'abcdefghijk'
                    snapshot = {
                        'schema_version': 1, 'origin': 'probe_ytmusic_batch',
                        'account_authentication_verified': True, 'library_sha256': LIBRARY,
                        'identity_map_sha256': runner._sha(args.identity_map.read_bytes()),
                        'selected_video_id': video, 'local_seed': {'persistent_id': pid},
                        'related_status': 'collected',
                        'requests': [{'endpoint': 'watch_radio'}, {'endpoint': 'watch_seed'},
                                     {'endpoint': 'song_related'}],
                        'observations': [],
                    }
                    for relation, video_id, title, is_seed in (
                            ('radio', 'lmnopqrstuv', 'Radio', False),
                            ('related', 'mnopqrstuvw', 'Related', False),
                            ('radio', video, 'Seed', True)):
                        snapshot['observations'].append({
                            'source': 'ytmusic', 'relation': relation, 'section': None,
                            'position': 1, 'observed_at': '2026-10-09T00:00:00+00:00',
                            'is_seed': is_seed,
                            'track': {'video_id': video_id, 'title': title,
                                      'artists': [{'name': 'Artist'}]},
                        })
                    (collector_dir / (pid + '.json')).write_text(
                        json.dumps(snapshot), encoding='utf-8')
                    (collector_dir / 'summary-001.json').write_text(json.dumps({
                        'schema_version': 1, 'status': 'completed',
                        'account_authentication_verified': True, 'library_sha256': LIBRARY,
                        'http_requests': 3, 'request_budget': 8,
                        'results': [{'persistent_id': pid, 'status': 'collected'}],
                    }), encoding='utf-8')
                    return 0
                output_dir = Path(command[command.index('--output') + 1])
                collector_dir = Path(command[command.index('--seed-manifest') + 1]).parent
                summary_path = Path(command[command.index('--collector-summary') + 1])
                manifest_path = Path(command[command.index('--seed-manifest') + 1])
                snapshot_path = Path(command[command.index('--snapshot') + 1])
                summary = json.loads(summary_path.read_text())
                manifest = json.loads(manifest_path.read_text())
                inputs = [{'path': str(snapshot_path.resolve()),
                           'sha256': runner._sha(snapshot_path.read_bytes())}]
                run = {
                    'collector_summary': {
                        'path': str(summary_path.resolve()),
                        'sha256': runner._sha(summary_path.read_bytes()),
                        'http_requests': summary['http_requests'],
                        'request_budget': summary['request_budget']},
                    'seed_manifest': {
                        'path': str(manifest_path.resolve()),
                        'sha256': runner._sha(manifest_path.read_bytes()),
                        'seed_count': len(manifest['seeds'])},
                    'snapshot_inputs': inputs,
                }
                output_dir.mkdir()
                (output_dir / 'accepted-graphs.json').write_text(json.dumps({
                    'producer': 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1',
                    'growth_runs': [run], 'accepted_roots': [
                        {'root_pid': pid, 'ordered_target_pids': ['0000000000000002']}],
                }), encoding='utf-8')
                return 0

            def replay(_args, previous_growth):
                if previous_growth is None:
                    return {'outgoing_root_count': 0, 'accepted_graphs_sha256': None}
                path = Path(previous_growth) / 'accepted-graphs.json'
                return {'outgoing_root_count': 1,
                        'accepted_graphs_sha256': runner._sha(path.read_bytes())}

            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch('evaluate_ytmusic_batch.collect_graphs', return_value=([{'root_pid': pid}], [], [])), \
                    patch.object(runner, '_replay_checkpoint', side_effect=replay), \
                    patch.object(runner, '_invoke', side_effect=invoke), \
                    patch.object(runner, '_write_atomic', side_effect=RuntimeError('simulated crash')):
                with self.assertRaisesRegex(RuntimeError, 'simulated crash'):
                    runner.run(args)
            growth_path = root / 'run' / 'batch-0001' / 'growth' / 'accepted-graphs.json'
            self.assertTrue(growth_path.exists())
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', side_effect=replay), \
                    patch.object(runner, '_invoke', side_effect=AssertionError('must not collect again')):
                progress = runner.run(args)
            self.assertEqual(progress['successful_outgoing_roots'], 1)
            self.assertEqual(progress['http_requests'], 3)
            self.assertEqual(progress['batches'][0]['outcome'], 'merged')


if __name__ == '__main__':
    unittest.main()
