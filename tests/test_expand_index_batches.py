"""Offline coverage for the bounded incremental collector orchestrator."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import expand_index_batches as batches
from plan_genius_update import make_synthetic_state
from update_genius_bundle import state_rows


LIBRARY_HASH = 'a' * 64
MAP_BYTES = b'{"fixture":"identity-map"}'


def pid(number):
    return f'{number:016X}'


class Matcher:
    def __init__(self, tracks):
        self.tracks = tracks

    def artist_credits(self, artist):
        return (artist,) if artist else ()


def graph(root, targets):
    return {'root_pid': root, 'ordered_target_pids': list(targets),
            'observations': [{}], 'candidate_groups': [], 'input': {'path': root}}


class ExpandIndexBatchesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tracks = [
            {'persistent_id': pid(i), 'title': f'Track {i}', 'artist': f'Artist {i}',
             'album': f'Album {i}', 'duration_ms': 120000}
            for i in range(1, 7)
        ]
        self.matcher = Matcher(self.tracks)
        self.state = json.loads(json.dumps(make_synthetic_state(
            [{'root_pid': pid(1), 'ordered_target_pids': [pid(2)]}],
            self.matcher, LIBRARY_HASH, batches._sha(MAP_BYTES))))
        self.relations = batches._relation_document(
            LIBRARY_HASH, self.state, [{'root_pid': pid(1), 'ordered_target_pids': [pid(2)]}])
        self.evaluation = {'schema_version': 1, 'library_sha256': LIBRARY_HASH,
                           'relation_rows': self.relations['relation_rows'],
                           'root_results': [], 'profile': 'fixture'}
        source_path = self.root / 'baseline-observation.json'
        source_path.write_text('{"saved":"evidence"}', encoding='utf-8')
        self.accepted = [graph(pid(1), [pid(2)])]
        self.accepted[0]['input'] = {
            'path': str(source_path), 'sha256': batches._sha(source_path.read_bytes())}
        self.paths = {}
        for name, document in (
                ('snapshot.json', {'fixture': True}),
                ('state.json', self.state), ('relations.json', self.relations),
                ('evaluation.json', self.evaluation), ('accepted.json', self.accepted)):
            path = self.root / name
            path.write_text(json.dumps(document), encoding='utf-8')
            self.paths[name] = path
        self.paths['map.json'] = self.root / 'map.json'
        self.paths['map.json'].write_bytes(MAP_BYTES)

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_collector_waits_before_subprocess_launch(self):
        events = []
        def sleep(seconds):
            events.append(('sleep', seconds))
        def run(command, **kwargs):
            events.append(('run', command, kwargs))
            return SimpleNamespace(returncode=0)
        with patch.object(batches.time, 'sleep', side_effect=sleep), \
                patch.object(batches.subprocess, 'run', side_effect=run):
            result = batches._invoke_collector(['collector'])
        self.assertEqual(result.returncode, 0)
        self.assertEqual([event[0] for event in events], ['sleep', 'run'])
        self.assertEqual(events[0], ('sleep', 5))
        self.assertEqual(events[1][1], ['collector'])

    def invoke(self, output, runner, *, max_requests=450, max_attempts=2,
               exclude_pids=(), prefetched_batch=None, prefetched_plan=None,
               batch_size=1, unindexed_per_batch=0,
               seed_video_evidence_path=None, candidate_limit=25,
               known_video_only=False,
               interval=5, target_indexed_count=None,
               deadline=None,
               seed_plan_override_path=None):
        patches = (
            patch.object(batches, 'load_library', return_value=(self.tracks, LIBRARY_HASH, {'path': 'fixture'})),
            patch.object(batches, 'IdentityMap', return_value=self.matcher),
        )
        with patches[0], patches[1]:
            return batches.run_expansion(
                track_snapshot=self.paths['snapshot.json'],
                identity_map_path=self.paths['map.json'],
                baseline_state_path=self.paths['state.json'],
                baseline_relations_path=self.paths['relations.json'],
                baseline_evaluation_path=self.paths['evaluation.json'],
                accepted_snapshots_path=self.paths['accepted.json'], output_dir=output,
                auth_path=self.root / 'auth.json', max_requests=max_requests,
                max_attempts=max_attempts, batch_size=batch_size,
                interval=interval, target_indexed_count=target_indexed_count,
                deadline=deadline,
                unindexed_per_batch=unindexed_per_batch,
                exclude_pids=exclude_pids, collector_runner=runner,
                prefetched_batch=prefetched_batch, prefetched_plan=prefetched_plan,
                seed_video_evidence_path=seed_video_evidence_path,
                known_video_only=known_video_only,
                candidate_limit=candidate_limit,
                seed_plan_override_path=seed_plan_override_path)

    def fake_runner(self, requests_each=1, statuses=None):
        calls = []
        def run(command):
            calls.append(command)
            plan = json.loads(Path(command[command.index('--seed-plan') + 1]).read_text())
            out = Path(command[command.index('--output') + 1])
            selected = plan['seeds']
            for seed in selected:
                (out / (seed['persistent_id'] + '.json')).write_text(json.dumps({
                    'local_seed': {'persistent_id': seed['persistent_id']}
                }), encoding='utf-8')
            status = (statuses or {}).get(len(calls), 'completed')
            request_budget = int(command[command.index('--request-budget') + 1])
            summary = {'schema_version': 1, 'status': status,
                       'account_authentication_verified': status == 'completed',
                       'library_sha256': LIBRARY_HASH,
                       'http_requests': min(requests_each, request_budget),
                       'request_budget': request_budget,
                       'results': [{'persistent_id': seed['persistent_id'], 'status': 'collected'}
                                   for seed in selected]}
            if status == 'stopped':
                summary.update(account_authentication_verified=True,
                               error_type='RuntimeError',
                               error_location={'function': 'request'})
            (out / 'summary-fixture.json').write_text(json.dumps(summary), encoding='utf-8')
            return SimpleNamespace(returncode=0)
        return run, calls

    def test_multiple_batches_preserve_old_edges_and_advance_virtual_state(self):
        runner, calls = self.fake_runner()
        new_graphs = {pid(2): graph(pid(2), [pid(3)]),
                      pid(3): graph(pid(3), [pid(4)])}
        with patch.object(batches, 'collect_graphs', side_effect=lambda paths, *_:
                          ([new_graphs[paths[0].stem]], [], [{'path': str(paths[0])}])):
            progress = self.invoke(self.root / 'run', runner)
        self.assertEqual(len(calls), 2)
        self.assertEqual(progress['attempted_seeds'], 2)
        self.assertEqual(progress['requests'], 2)
        self.assertEqual(progress['seeds_valid'], 2)
        audit = json.loads((self.root / 'run' / 'run-audit.json').read_text())
        self.assertTrue(audit['baseline_root_graph_preserved'])
        final = json.loads((self.root / 'run' / 'batch-002' / 'relations.json').read_text())
        edges = {row['root_pid']: row['ordered_target_pids'] for row in final['relation_rows']}
        self.assertEqual(edges[pid(1)], [pid(2)])
        self.assertEqual(edges[pid(2)], [pid(3)])
        self.assertEqual(edges[pid(3)], [pid(4)])
        state = json.loads((self.root / 'run' / 'batch-002' / 'state.json').read_text())
        ids, _, _ = state_rows(state)
        self.assertTrue({pid(1), pid(2), pid(3), pid(4)} <= set(ids))

    def test_request_budget_limits_next_batch(self):
        runner, calls = self.fake_runner(requests_each=2)
        new_graphs = {pid(2): graph(pid(2), [pid(3)]),
                      pid(3): graph(pid(3), [pid(4)])}
        with patch.object(batches, 'collect_graphs', side_effect=lambda paths, *_:
                          ([new_graphs[paths[0].stem]], [], [])):
            progress = self.invoke(self.root / 'budget', runner, max_requests=3, max_attempts=5)
        self.assertEqual(len(calls), 2)
        second_budget = calls[1][calls[1].index('--request-budget') + 1]
        self.assertEqual(second_budget, '1')
        self.assertEqual(progress['requests'], 3)
        self.assertEqual(progress['stop_reason'], 'request_budget_reached')

    def test_collector_http_or_auth_failure_stops_without_retry(self):
        runner, calls = self.fake_runner(statuses={1: 'stopped'})
        # The stopped collector creates no accepted graph; it is still recorded as an attempt.
        progress = self.invoke(self.root / 'error', runner, max_attempts=5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(progress['attempted_seeds'], 1)
        self.assertEqual(progress['stop_reason'], 'collector_stopped')
        audit = json.loads((self.root / 'error' / 'run-audit.json').read_text())
        self.assertEqual(audit['batch_count'], 1)

    def test_request_boundary_stop_accepts_completed_snapshots_and_stops(self):
        runner, calls = self.fake_runner(requests_each=3, statuses={1: 'stopped'})
        new_graph = graph(pid(2), [pid(3)])
        with patch.object(batches, 'collect_graphs', return_value=([new_graph], [], [])):
            progress = self.invoke(self.root / 'boundary-stop', runner,
                                   max_requests=3, max_attempts=5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(progress['requests'], 3)
        self.assertEqual(progress['attempted_seeds'], 1)
        self.assertEqual(progress['seeds_valid'], 1)
        self.assertEqual(progress['stop_reason'], 'request_budget_reached')
        audit = json.loads((self.root / 'boundary-stop' / 'batch-001' / 'batch-audit.json').read_text())
        self.assertEqual(audit['collector_boundary_stop'], 'request_budget_boundary')

    def test_max_attempts_can_reach_library_size_but_not_exceed_it(self):
        runner, calls = self.fake_runner()
        progress = self.invoke(self.root / 'library-sized-budget', runner, max_attempts=len(self.tracks))
        self.assertEqual(progress['target_attempted_seed_count'], len(self.tracks))
        self.assertEqual(progress['stop_reason'], 'no_more_eligible_roots')
        self.assertTrue(calls)
        with self.assertRaisesRegex(ValueError, 'Library track count'):
            self.invoke(self.root / 'oversized-budget', runner, max_attempts=len(self.tracks) + 1)

    def test_interval_is_bounded_and_forwarded_to_collector(self):
        runner, calls = self.fake_runner()
        self.invoke(self.root / 'fast-interval', runner, max_attempts=1, interval=2,
                    max_requests=10000)
        self.assertEqual(calls[0][calls[0].index('--interval') + 1], '2')
        for value in (1.99, 60.01, float('inf'), float('nan')):
            with self.assertRaisesRegex(ValueError, 'interval must be finite'):
                self.invoke(self.root / ('invalid-interval-' + str(value)), runner,
                            max_attempts=1, interval=value)

    def test_target_indexed_count_stops_after_reaching_virtual_state_target(self):
        runner, calls = self.fake_runner()
        with patch.object(batches, 'collect_graphs', return_value=(
                [graph(pid(2), [pid(3)])], [], [])):
            progress = self.invoke(self.root / 'target-count', runner, max_attempts=5,
                                   target_indexed_count=3)
        self.assertEqual(len(calls), 1)
        self.assertEqual(progress['indexed_tracks'], 3)
        self.assertEqual(progress['stop_reason'], 'indexed_track_target_reached')

    def test_target_indexed_count_is_bounded_by_library_size(self):
        runner, _ = self.fake_runner()
        with self.assertRaisesRegex(ValueError, 'target_indexed_count'):
            self.invoke(self.root / 'oversized-index-target', runner,
                        target_indexed_count=len(self.tracks) + 1)

    def test_expired_deadline_prevents_collector_launch_and_attempt_accounting(self):
        runner, calls = self.fake_runner()
        deadline = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        progress = self.invoke(self.root / 'expired-deadline', runner,
                               max_attempts=2, deadline=deadline)
        self.assertEqual(calls, [])
        self.assertEqual(progress['attempted_seeds'], 0)
        self.assertEqual(progress['stop_reason'], 'deadline_reached')
        audit = json.loads((self.root / 'expired-deadline' / 'run-audit.json').read_text())
        self.assertEqual(audit['deadline'], deadline)

    def test_deadline_stop_accepts_only_completed_seeds_and_keeps_actual_count(self):
        runner, calls = self.fake_runner(requests_each=2)
        deadline_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        deadline = deadline_at.isoformat()
        clock = [deadline_at - timedelta(seconds=10)]
        def stopped_run(command):
            result = runner(command)
            clock[0] = deadline_at + timedelta(seconds=1)
            output = Path(command[command.index('--output') + 1])
            summary_path = next(output.glob('summary-*.json'))
            summary = json.loads(summary_path.read_text())
            summary.update(status='stopped', account_authentication_verified=True,
                           deadline=deadline, deadline_stop_at=clock[0].isoformat(),
                           error_type='CollectionDeadlineReached',
                           error_location={'function': 'request'})
            summary['results'] = summary['results'][:1]
            summary_path.write_text(json.dumps(summary), encoding='utf-8')
            return result
        with patch.object(batches, 'datetime', SimpleNamespace(now=lambda tz: clock[0])), \
                patch.object(batches, 'collect_graphs', side_effect=lambda paths, *_:
                          ([graph(paths[0].stem, [pid(4)])], [], [])):
            progress = self.invoke(self.root / 'deadline-partial', stopped_run,
                                   max_attempts=2, batch_size=2, deadline=deadline)
        self.assertEqual(len(calls), 1)
        self.assertEqual(progress['attempted_seeds'], 1)
        self.assertEqual(progress['attempted_pids'], [pid(2)])
        self.assertEqual(progress['stop_reason'], 'deadline_reached')
        audit = json.loads((self.root / 'deadline-partial' / 'batch-001' / 'batch-audit.json').read_text())
        self.assertEqual(audit['seeds'], [pid(2)])
        self.assertEqual(audit['requested_seeds'], [pid(2), pid(3)])
        self.assertEqual(calls[0][calls[0].index('--deadline') + 1], deadline)

    def test_invoke_collector_rechecks_deadline_after_pacing_wait(self):
        deadline = datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc)
        times = iter([deadline - timedelta(seconds=2), deadline + timedelta(seconds=1)])
        command = ['python', 'collector.py', '--deadline', deadline.isoformat()]
        with patch.object(batches, 'datetime', SimpleNamespace(now=lambda tz: next(times))), \
                patch.object(batches.time, 'sleep') as sleep, \
                patch.object(batches.subprocess, 'run') as run:
            result = batches._invoke_collector(command)
        sleep.assert_called_once_with(2.0)
        run.assert_not_called()
        self.assertTrue(result.deadline_before_launch)

    def test_known_video_only_planner_uses_strict_hints_with_regular_fairness(self):
        evidence_path = self.root / 'strict-evidence.json'
        evidence_path.write_text('{}', encoding='utf-8')
        # PID 4 is deliberately a version variant and must be removed even when
        # the evidence validator returns it as a saved hint.
        self.tracks[3]['title'] = 'Track 4 Karaoke'
        self.state = json.loads(json.dumps(make_synthetic_state(
            [{'root_pid': pid(1), 'ordered_target_pids': [pid(2)]}],
            self.matcher, LIBRARY_HASH, batches._sha(MAP_BYTES))))
        self.relations = batches._relation_document(
            LIBRARY_HASH, self.state,
            [{'root_pid': pid(1), 'ordered_target_pids': [pid(2)]}])
        self.evaluation['relation_rows'] = self.relations['relation_rows']
        self.paths['state.json'].write_text(json.dumps(self.state), encoding='utf-8')
        self.paths['relations.json'].write_text(json.dumps(self.relations), encoding='utf-8')
        self.paths['evaluation.json'].write_text(json.dumps(self.evaluation), encoding='utf-8')
        runner, calls = self.fake_runner()
        hints = {pid(2): {}, pid(3): {}, pid(4): {}, pid(5): {}}
        with patch.object(batches, 'select_seed_video_evidence', return_value=hints), \
                patch.object(batches, 'collect_graphs', side_effect=lambda paths, *_:
                             ([graph(paths[0].stem, [pid(6)])], [], [])):
            progress = self.invoke(self.root / 'known-video', runner,
                                   max_attempts=2, batch_size=2,
                                   seed_video_evidence_path=evidence_path,
                                   known_video_only=True)
        selected = [seed['persistent_id'] for command in calls
                    for seed in json.loads(Path(command[command.index('--seed-plan') + 1]).read_text())['seeds']]
        self.assertTrue(set(selected) <= {pid(2), pid(3), pid(5)})
        self.assertEqual(progress['attempted_seeds'], 2)
        plan_audit = json.loads((self.root / 'known-video' / 'batch-001' /
                                 'seed-plan-audit.json').read_text())
        self.assertTrue(plan_audit['known_video_only'])

    def test_all_previously_attempted_candidates_are_skipped(self):
        runner, calls = self.fake_runner()
        # The sole incoming-only PID and every currently unindexed PID are held.
        excluded = [track['persistent_id'] for track in self.tracks[1:]]
        progress = self.invoke(self.root / 'all-skipped', runner, max_attempts=2,
                               exclude_pids=excluded)
        self.assertEqual(calls, [])
        self.assertEqual(progress['attempted_seeds'], 0)
        self.assertEqual(progress['stop_reason'], 'no_more_eligible_roots')

    def test_candidate_root_mismatch_is_skipped_and_old_graph_survives(self):
        runner, _ = self.fake_runner()
        with patch.object(batches, 'collect_graphs', side_effect=lambda *_:
                          ([graph(pid(6), [pid(5)])], [], [])):
            progress = self.invoke(self.root / 'mismatch', runner, max_attempts=1)
        self.assertEqual(progress['roots'], 1)
        self.assertEqual(progress['seeds_collected'], 1)
        self.assertEqual(progress['seeds_valid'], 0)
        self.assertEqual(progress['target_attempted_seed_count'], 1)
        doc = json.loads((self.root / 'mismatch' / 'batch-001' / 'accepted-graphs.json').read_text())
        self.assertEqual(doc['skipped_snapshots'][0]['reason'], 'candidate_root_pid_mismatch')
        self.assertEqual(doc['accepted_roots'][0]['root_pid'], pid(1))

    def test_prefetched_batch_is_hash_bound_and_counted_without_recollection(self):
        from plan_index_expansion import plan_index_expansion
        from probe_ytmusic_batch import select_planned_seeds
        planned, _ = plan_index_expansion(self.tracks, LIBRARY_HASH, self.state,
                                          self.relations, 1)
        plan_path = self.root / 'prefetched-plan.json'
        plan_path.write_text(json.dumps(planned), encoding='utf-8')
        pre_dir = self.root / 'prefetched'
        pre_dir.mkdir()
        selected = select_planned_seeds(planned, self.tracks, LIBRARY_HASH, 1)
        (pre_dir / 'seeds.json').write_text(json.dumps({
            'library_sha256': LIBRARY_HASH,
            'seed_plan_sha256': batches._sha(plan_path.read_bytes()),
            'seeds': selected,
        }), encoding='utf-8')
        seed_pid = selected[0]['persistent_id']
        (pre_dir / (seed_pid + '.json')).write_text(json.dumps({
            'local_seed': {'persistent_id': seed_pid}
        }), encoding='utf-8')
        (pre_dir / 'summary-fixture.json').write_text(json.dumps({
            'schema_version': 1, 'status': 'stopped',
            'account_authentication_verified': True,
            'library_sha256': LIBRARY_HASH, 'http_requests': 3, 'request_budget': 3,
            'error_type': 'RuntimeError', 'error_location': {'function': 'request'},
            'results': [{'persistent_id': seed_pid, 'status': 'collected'}],
        }), encoding='utf-8')
        runner, calls = self.fake_runner()
        with patch.object(batches, 'collect_graphs', return_value=(
                [graph(seed_pid, [pid(3)])], [], [])):
            progress = self.invoke(self.root / 'prefetched-run', runner,
                                   max_attempts=1, prefetched_batch=pre_dir,
                                   prefetched_plan=plan_path)
        self.assertEqual(calls, [])
        self.assertEqual(progress['attempted_seeds'], 1)
        self.assertEqual(progress['requests'], 3)
        self.assertFalse((pre_dir / 'state.json').exists())
        batch_audit = json.loads((self.root / 'prefetched-run' / 'batch-001' / 'batch-audit.json').read_text())
        self.assertEqual(batch_audit['collector_boundary_stop'], 'request_budget_boundary')

    def test_ambiguous_seed_is_skipped_without_discarding_other_roots(self):
        runner, _ = self.fake_runner()
        def collect(paths, *_):
            if paths[0].stem == pid(2):
                raise ValueError('No valid recommendation snapshots remain')
            return [graph(pid(3), [pid(4)])], [], []
        with patch.object(batches, 'collect_graphs', side_effect=collect):
            progress = self.invoke(self.root / 'partial-valid', runner, max_attempts=2,
                                   batch_size=2, unindexed_per_batch=1)
        self.assertEqual(progress['roots'], 2)
        audit = json.loads((self.root / 'partial-valid' / 'batch-001' / 'accepted-graphs.json').read_text())
        self.assertEqual(audit['skipped_snapshots'][0]['reason'], 'no_unique_duration_supported_root')
        self.assertEqual({row['root_pid'] for row in audit['new_graphs']}, {pid(3)})

    def test_explicit_known_video_plan_chunks_and_forwards_candidate_limit(self):
        evidence_path = self.root / 'strict-evidence.json'
        evidence_path.write_text('{}', encoding='utf-8')
        planned_pids = [pid(i) for i in range(2, 6)]
        plan_path = self.root / 'explicit-plan.json'
        plan_path.write_text(json.dumps({
            'schema_version': 1, 'source_library_sha256': LIBRARY_HASH,
            'seeds': [{'persistent_id': value, 'series': 'known-video',
                       'sample_kind': 'indexed_target_only'} for value in planned_pids],
        }), encoding='utf-8')
        runner, calls = self.fake_runner(requests_each=2)
        new_graphs = {value: graph(value, [pid(6)]) for value in planned_pids}
        hints = {value: {'video_id': f'video{i:07d}', 'source': 'saved'}
                 for i, value in enumerate(planned_pids)}
        with patch.object(batches, 'select_seed_video_evidence', return_value=hints), \
                patch.object(batches, 'collect_graphs', side_effect=lambda paths, *_:
                             ([new_graphs[paths[0].stem]], [], [])):
            progress = self.invoke(
                self.root / 'override-run', runner, max_attempts=4,
                batch_size=2, unindexed_per_batch=0, max_requests=205,
                seed_video_evidence_path=evidence_path, candidate_limit=100,
                seed_plan_override_path=plan_path)
        self.assertEqual(len(calls), 2)
        self.assertEqual(progress['attempted_seeds'], 4)
        self.assertEqual(progress['seeds_valid'], 4)
        for command in calls:
            self.assertEqual(command[command.index('--candidate-limit') + 1], '100')
            self.assertEqual(command[command.index('--seed-video-evidence') + 1], str(evidence_path))
        first_plan = json.loads((self.root / 'override-run' / 'batch-001' / 'seed-plan.json').read_text())
        second_plan = json.loads((self.root / 'override-run' / 'batch-002' / 'seed-plan.json').read_text())
        self.assertEqual([row['persistent_id'] for row in first_plan['seeds']], planned_pids[:2])
        self.assertEqual([row['persistent_id'] for row in second_plan['seeds']], planned_pids[2:])
        self.assertTrue(json.loads((self.root / 'override-run' / 'batch-001' /
                                    'seed-plan-audit.json').read_text())['explicit_override'])

    def test_explicit_plan_requires_strict_video_evidence_for_every_pid(self):
        evidence_path = self.root / 'strict-evidence.json'
        evidence_path.write_text('{}', encoding='utf-8')
        plan_path = self.root / 'explicit-plan.json'
        plan_path.write_text(json.dumps({
            'schema_version': 1, 'source_library_sha256': LIBRARY_HASH,
            'seeds': [{'persistent_id': pid(2), 'series': 'known-video',
                       'sample_kind': 'indexed_target_only'}],
        }), encoding='utf-8')
        runner, calls = self.fake_runner()
        with patch.object(batches, 'select_seed_video_evidence', return_value={}):
            with self.assertRaisesRegex(ValueError, 'Every explicit seed'):
                self.invoke(self.root / 'missing-evidence', runner, max_attempts=1,
                            seed_video_evidence_path=evidence_path,
                            seed_plan_override_path=plan_path)
        self.assertEqual(calls, [])

    def test_baseline_relation_comparison_ignores_row_order(self):
        left = [{'root_pid': pid(1), 'ordered_target_pids': [pid(2)]},
                {'root_pid': pid(3), 'ordered_target_pids': [pid(4), pid(5)]}]
        right = list(reversed(left))
        self.assertEqual(batches._edge_map(left, 'left'), batches._edge_map(right, 'right'))


if __name__ == '__main__':
    unittest.main()
