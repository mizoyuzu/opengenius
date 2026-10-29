import json
import tempfile
import unittest
from pathlib import Path

from scripts import audit_final_ytmusic_source_v19 as audit


class WholeSourceFinalAuditV18Tests(unittest.TestCase):
    def test_actual_expander_target_reached_status_is_accepted_verbatim(self):
        audit._require_final_progress_status('target_reached')

    def test_running_or_unknown_progress_status_is_rejected(self):
        for status in ('running', 'target-reach', None):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, 'not a frozen completed checkpoint'):
                    audit._require_final_progress_status(status)
    def test_runtime_manifest_and_contracts_match_v12_frozen_consumer(self):
        manifest = audit.verify_runtime()
        self.assertEqual(manifest['producer'], audit.RUNTIME_PRODUCER)
        self.assertEqual(len(manifest['modules']), 180)
        contracts = audit.verify_contracts(manifest)
        self.assertEqual(contracts['contract_status'], 'passed')
        self.assertEqual(contracts['runtime_module_count'], 180)
        self.assertEqual(contracts['projection_dispatch'], 'present')

    def test_accepted_parent_source_sets_are_native159_plus_yt623(self):
        inventory, _, native, legacy_yt = audit._source_origin_inventory()
        accepted_yt = audit._accepted_782_yt_origin_pids()
        parent = json.loads((audit.ACCEPTED_PARENT_GROWTH / 'accepted-graphs.json').read_text())
        active = {row['root_pid'] for row in parent['accepted_roots']
                  if row.get('ordered_target_pids')}
        self.assertEqual(len(native), 159)
        self.assertEqual(len(legacy_yt), 573)
        self.assertEqual(len(accepted_yt), 623)
        self.assertTrue(legacy_yt <= accepted_yt)
        self.assertFalse(native & accepted_yt)
        self.assertEqual(len(native | accepted_yt), 782)
        self.assertEqual(native | accepted_yt, active)

    def test_actual_782_parent_source_rows_reconcile_native_and_radio_proofs(self):
        inventory, _, native, legacy_yt = audit._source_origin_inventory()
        graph = json.loads((audit.ACCEPTED_PARENT_GROWTH / 'accepted-graphs.json').read_text())
        active = {row['root_pid'] for row in graph['accepted_roots']
                  if row.get('ordered_target_pids')}
        result = audit._classify_final_source_roots(
            active, native, legacy_yt, graph['growth_runs'])
        self.assertEqual(result['counts'], {
            'native_only': 159, 'ytmusic_source_backed': 623,
            'native_ytmusic_overlap': 0, 'unknown': 0, 'total_active': 782,
        })
        self.assertEqual(set(result['native_only_pids']) | set(result['ytmusic_source_backed_pids']), active)

    def test_nonempty_root_counter_ignores_assignment_only_rows(self):
        self.assertEqual(audit._active_root_pids({'A': ['B'], 'C': [], 'D': []}), {'A'})

    def test_new_normal_radio_suffix_requires_same_pid_authenticated_snapshots(self):
        pid = 'F000000000000001'
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / 'snapshot.json'
            proof = Path(directory) / 'proof.json'
            targets = ['F000000000000002']
            snapshot.write_text(json.dumps({
                'local_seed': {'persistent_id': pid},
                'account_authentication_verified': True,
            }), encoding='utf-8')
            proof.write_text(json.dumps({
                'producer': 'opengenius-authenticated-radio-snapshot-proof-v1',
                'policy': 'authenticated-watch-radio-only-v1',
                'recording_identity_verified': False,
                'library_sha256': audit.EXPECTED['library'],
                'identity_map': {'path': str(audit.EXPECTED_MAP),
                                 'sha256': audit.EXPECTED['map']},
                'outgoing_target_pids': targets,
                'outgoing_recommendation_edges': [{
                    'source_pid': pid, 'target_pid': targets[0],
                    'source_relation': 'authenticated_watch_radio',
                    'video_ids': ['video-fixture'],
                }],
                'selected_seed': {'persistent_id': pid, 'video_id': 'video-fixture'},
                'radio_request': {'endpoint': 'get_watch_playlist',
                                  'arguments': {'radio': True, 'videoId': 'video-fixture'}},
            }), encoding='utf-8')
            digest = audit.sha(snapshot)
            run = {
                'source_policy': 'authenticated-watch-radio-only-v1',
                'new_graphs': [{'root_pid': pid, 'ordered_target_pids': targets}],
                'snapshot_inputs': [{'path': str(snapshot), 'sha256': digest,
                                     'radio_only_source_proof': {
                                         'path': str(proof), 'sha256': audit.sha(proof)}}],
            }
            sourced, runs = audit._verified_run_source_pids([run], {pid})
            self.assertEqual(sourced, {pid})
            self.assertEqual(runs, 1)
            proof_doc = json.loads(proof.read_text(encoding='utf-8'))
            proof_doc['outgoing_target_pids'] = ['F000000000000003']
            proof_doc['outgoing_recommendation_edges'][0]['target_pid'] = 'F000000000000003'
            proof.write_text(json.dumps(proof_doc), encoding='utf-8')
            run['snapshot_inputs'][0]['radio_only_source_proof']['sha256'] = audit.sha(proof)
            with self.assertRaisesRegex(ValueError, 'does not match its exact accepted ordered vector'):
                audit._verified_run_source_pids([run], {pid})
            run['snapshot_inputs'] = []
            with self.assertRaisesRegex(ValueError, 'no replayed snapshot receipts'):
                audit._verified_run_source_pids([run], {pid})

    def test_source_partition_rejects_unknowns_and_counts_overlap_once(self):
        overlapping = audit._classify_final_source_roots({'A', 'B'}, {'A', 'B'}, {'B'}, [])
        self.assertEqual(overlapping['counts'], {
            'native_only': 1, 'ytmusic_source_backed': 1,
            'native_ytmusic_overlap': 1, 'unknown': 0, 'total_active': 2,
        })
        with self.assertRaisesRegex(ValueError, 'no Native or replay-verified YT source origin'):
            audit._classify_final_source_roots({'A', 'X'}, {'A'}, set(), [])

    def test_total_genuine_goal_enforces_1000_and_zero_unknowns(self):
        source = {'counts': {'native_only': 159, 'ytmusic_source_backed': 841,
                             'native_ytmusic_overlap': 0, 'unknown': 0, 'total_active': 1000}}
        self.assertEqual(audit._require_goal(source, 'total-genuine'), 1000)
        source['counts'].update(native_only=159, ytmusic_source_backed=840, total_active=999)
        with self.assertRaisesRegex(ValueError, 'fewer than 1000'):
            audit._require_goal(source, 'total-genuine')
        source['counts']['unknown'] = 1
        with self.assertRaisesRegex(ValueError, 'exact final-root partition'):
            audit._require_goal(source, 'total-genuine')

    def test_current_under_target_checkpoint_refuses_before_full_replay(self):
        progress = audit.EXPECTED_PROGRESS_DIR / 'progress.json'
        with self.assertRaisesRegex(ValueError, 'below 1000 total roots'):
            audit.audit(progress, Path(tempfile.gettempdir()) / 'v18-must-not-write.json')


if __name__ == '__main__':
    unittest.main()
