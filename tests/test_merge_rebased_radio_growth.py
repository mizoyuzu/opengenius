"""Offline receipt and chained-state checks for rebased YT Music growth."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import merge_rebased_radio_growth as merger
from music_identity_map import IdentityMap, build_map
from plan_genius_update import make_synthetic_state, plan_genius_update
from update_genius_bundle import state_rows


LIBRARY_HASH = 'a' * 64
PID1, PID2, PID3 = ('0000000000000001', '0000000000000002', '0000000000000003')


class RebasedRadioGrowthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tracks = [
            {'persistent_id': pid, 'title': 'Track ' + pid[-1], 'artist': 'Artist',
             'album': 'Album', 'duration_ms': 120000}
            for pid in (PID1, PID2, PID3)
        ]
        self.matcher = IdentityMap(build_map(self.tracks, LIBRARY_HASH),
                                   self.tracks, LIBRARY_HASH)
        map_bytes = json.dumps(build_map(self.tracks, LIBRARY_HASH)).encode()
        self.identity_hash = hashlib.sha256(map_bytes).hexdigest()
        self.baseline_roots = [{'root_pid': PID1, 'ordered_target_pids': [PID2]}]
        self.baseline = make_synthetic_state(
            self.baseline_roots, self.matcher, LIBRARY_HASH, self.identity_hash)
        self.baseline_provenance = {'graph_export_sha256': '1' * 64,
                                    'rebase_report_sha256': '2' * 64}

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def run_receipt(self, root_pid, targets, label):
        summary = self.write_json(self.root / (label + '-summary.json'), {
            'schema_version': 1, 'status': 'completed',
            'account_authentication_verified': True,
            'library_sha256': LIBRARY_HASH, 'http_requests': 1,
            'request_budget': 5,
            'results': [{'persistent_id': root_pid, 'status': 'collected'}],
        })
        manifest = self.write_json(self.root / (label + '-seeds.json'), {
            'library_sha256': LIBRARY_HASH,
            'identity_map_sha256': self.identity_hash,
            'seeds': [{'persistent_id': root_pid}],
        })
        snapshot = self.write_json(self.root / (label + '-snapshot.json'), {'saved': label})
        snapshot_input = {'path': str(snapshot.resolve()),
                          'sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest()}
        return {
            'collector_summary': {'path': str(summary.resolve()),
                                  'sha256': hashlib.sha256(summary.read_bytes()).hexdigest()},
            'seed_manifest': {'path': str(manifest.resolve()),
                              'sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()},
            'snapshot_inputs': [snapshot_input],
            'new_graphs': [{'root_pid': root_pid,
                            'ordered_target_pids': list(targets)}],
        }, snapshot_input, root_pid, list(targets)

    def make_previous_output(self, run, snapshot_input, root_pid, targets):
        generated = {'root_pid': root_pid, 'ordered_target_pids': targets,
                     'observations': [{}], 'input': snapshot_input}
        new_roots = [{'root_pid': root_pid, 'ordered_target_pids': targets}]
        roots, _ = merger._append_graphs(self.baseline_roots, new_roots)
        state = plan_genius_update(self.baseline, roots, self.matcher,
                                   LIBRARY_HASH, self.identity_hash)['next_state']
        run = {**run, 'resulting_state_sha256': state['state_sha256']}
        accepted = {
            'schema_version': 1,
            'producer': 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1',
            'library_sha256': LIBRARY_HASH,
            'identity_map_sha256': self.identity_hash,
            'baseline_state_sha256': self.baseline['state_sha256'],
            'baseline_rebase_provenance': self.baseline_provenance,
            'baseline_accepted_roots': self.baseline_roots,
            'growth_runs': [run],
            'accepted_roots': roots,
        }
        previous = self.root / 'previous'
        previous.mkdir()
        self.write_json(previous / 'accepted-graphs.json', accepted)
        self.write_json(previous / 'state.json', state)
        self.write_json(previous / 'relations.json', {'relation_rows': roots})
        self.write_json(previous / 'accepted-evaluation.json', {
            'virtual_state_sha256': state['state_sha256'], 'relation_rows': roots})
        return previous, state, roots, generated

    def test_previous_growth_receipts_replay_and_preserve_assignments_for_next_run(self):
        run, snapshot_input, root_pid, targets = self.run_receipt(PID2, [PID3], 'first')
        previous, first_state, first_roots, graph = self.make_previous_output(
            run, snapshot_input, root_pid, targets)
        with patch.object(merger, 'collect_graphs', return_value=(
                [graph], [], [snapshot_input])), \
                patch.object(merger, 'merge_graphs', return_value=[{
                    'root_pid': PID2, 'ordered_target_pids': [PID3]}]):
            replayed_state, replayed_roots, replayed_runs, _ = merger._replay_previous_growth(
                previous, self.baseline, self.baseline_roots,
                self.baseline_provenance, self.matcher, LIBRARY_HASH,
                self.identity_hash)
        self.assertEqual(replayed_state['state_sha256'], first_state['state_sha256'])
        self.assertEqual(replayed_roots, first_roots)
        self.assertEqual(len(replayed_runs), 1)

        before_ids = state_rows(replayed_state)[0]
        second_roots, old_edges = merger._append_graphs(
            replayed_roots, [{'root_pid': PID3, 'ordered_target_pids': [PID1]}])
        second_state = plan_genius_update(
            replayed_state, second_roots, self.matcher,
            LIBRARY_HASH, self.identity_hash)['next_state']
        after_ids = state_rows(second_state)[0]
        self.assertEqual({pid: after_ids[pid] for pid in before_ids}, before_ids)
        self.assertEqual(second_roots[0]['ordered_target_pids'], [PID2])
        self.assertTrue(all(
            next(row['ordered_target_pids'] for row in second_roots
                 if row['root_pid'] == pid)[:len(edges)] == edges
            for pid, edges in old_edges.items()))

    def test_previous_growth_replay_rejects_changed_raw_snapshot(self):
        run, snapshot_input, root_pid, targets = self.run_receipt(PID2, [PID3], 'first')
        previous, _, _, graph = self.make_previous_output(
            run, snapshot_input, root_pid, targets)
        Path(snapshot_input['path']).write_text('{"tampered":true}', encoding='utf-8')
        with patch.object(merger, 'collect_graphs', return_value=([graph], [], [snapshot_input])), \
                patch.object(merger, 'merge_graphs', return_value=[{
                    'root_pid': PID2, 'ordered_target_pids': [PID3]}]):
            with self.assertRaisesRegex(ValueError, 'snapshot changed'):
                merger._replay_previous_growth(
                    previous, self.baseline, self.baseline_roots,
                    self.baseline_provenance, self.matcher, LIBRARY_HASH,
                    self.identity_hash)

    def test_authenticated_request_budget_stop_replays_only_saved_collected_graphs(self):
        run, snapshot_input, root_pid, targets = self.run_receipt(PID2, [PID3], 'boundary')
        summary_path = Path(run['collector_summary']['path'])
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        summary.update(status='stopped', http_requests=5, request_budget=5,
                       error_type='RuntimeError', error_location={'function': 'request'})
        self.write_json(summary_path, summary)
        run['collector_summary']['sha256'] = hashlib.sha256(
            summary_path.read_bytes()).hexdigest()
        graph = {'root_pid': root_pid, 'ordered_target_pids': targets,
                 'observations': [{}], 'input': snapshot_input}
        with patch.object(merger, 'collect_graphs', return_value=(
                [graph], [], [snapshot_input])), patch.object(merger, 'merge_graphs',
                    return_value=[{'root_pid': PID2, 'ordered_target_pids': [PID3]}]):
            roots, normalized = merger._validate_run_receipt(
                run, self.tracks, self.matcher, LIBRARY_HASH, self.identity_hash)
        self.assertEqual(roots, [{'root_pid': PID2, 'ordered_target_pids': [PID3]}])
        self.assertEqual(normalized['collector_summary']['http_requests'], 5)

        summary['http_requests'] = 4
        self.write_json(summary_path, summary)
        run['collector_summary']['sha256'] = hashlib.sha256(
            summary_path.read_bytes()).hexdigest()
        with patch.object(merger, 'collect_graphs', return_value=(
                [graph], [], [snapshot_input])):
            with self.assertRaisesRegex(ValueError, 'not authenticated'):
                merger._validate_run_receipt(
                    run, self.tracks, self.matcher, LIBRARY_HASH, self.identity_hash)

    def test_ambiguous_collected_snapshot_is_receipted_but_not_added_as_graph(self):
        run, snapshot_input, root_pid, targets = self.run_receipt(PID2, [PID3], 'ambiguous')
        graph = {'root_pid': root_pid, 'ordered_target_pids': targets,
                 'observations': [{}], 'input': snapshot_input}
        skipped = [{'path': str(self.root / 'ambiguous-root.json'),
                    'sha256': 'f' * 64, 'reason': 'no_unique_duration_supported_root'}]
        with patch.object(merger, 'collect_graphs', return_value=(
                [graph], skipped, [snapshot_input])):
            with self.assertRaisesRegex(ValueError, 'previous snapshot'):
                merger._validate_run_receipt(
                    run, self.tracks, self.matcher, LIBRARY_HASH, self.identity_hash)

        run['skipped_snapshots'] = skipped
        with patch.object(merger, 'collect_graphs', return_value=(
                [graph], skipped, [snapshot_input])), patch.object(merger, 'merge_graphs',
                    return_value=[{'root_pid': PID2, 'ordered_target_pids': [PID3]}]):
            roots, normalized = merger._validate_run_receipt(
                run, self.tracks, self.matcher, LIBRARY_HASH, self.identity_hash)
        self.assertEqual(roots, [{'root_pid': PID2, 'ordered_target_pids': [PID3]}])
        self.assertEqual(normalized['skipped_snapshots'], skipped)


if __name__ == '__main__':
    unittest.main()
