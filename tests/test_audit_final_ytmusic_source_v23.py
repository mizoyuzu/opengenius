import json
import copy
import tempfile
import unittest
from pathlib import Path

from scripts import audit_final_ytmusic_source_v23 as audit


class WholeSourceFinalAuditV23Tests(unittest.TestCase):
    def test_v15_verified_radio_baseline_transition_uses_structural_fixture_only(self):
        fixture = Path('/tmp/opengenius-v13-whole44-structural-guard-v05/accepted-graphs.json')
        graph = json.loads(fixture.read_text(encoding='utf-8'))
        result = audit._validate_verified_radio_baseline_policy_transition(graph)
        self.assertEqual(result['producer'], 'opengenius-radio-verified-baseline-policy-inheritance-v1')
        self.assertEqual(result['baseline_nonempty_roots'], 782)
        self.assertEqual(result['baseline_edges'], 18271)
        self.assertEqual(result['baseline_assignment_rows'], 972)
        self.assertEqual(result['source_credit_added_by_transition'], 0)
        broken = copy.deepcopy(graph)
        broken['source_policy_transition']['inheritance_attempt_delta'] = 1
        with self.assertRaisesRegex(ValueError, 'differs from its exact proof receipt'):
            audit._validate_verified_radio_baseline_policy_transition(broken)

    def test_actual_expander_target_reached_status_is_accepted_verbatim(self):
        audit._require_final_progress_status('target_reached')

    def test_running_or_unknown_progress_status_is_rejected(self):
        for status in ('running', 'target-reach', None):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, 'not a frozen completed checkpoint'):
                    audit._require_final_progress_status(status)
    def test_runtime_manifest_and_contracts_match_v15_frozen_consumer(self):
        manifest = audit.verify_runtime()
        self.assertEqual(manifest['producer'], audit.RUNTIME_PRODUCER)
        self.assertEqual(len(manifest['modules']), 184)
        contracts = audit.verify_contracts(manifest)
        self.assertEqual(contracts['contract_status'], 'passed')
        self.assertEqual(contracts['runtime_module_count'], 184)
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

    def test_historical_mode_accepts_only_exact_exhausted_870_input(self):
        progress = audit.EXPECTED_PROGRESS_DIR / 'progress.json'
        accepted = audit._precheck_target(progress, 'total-genuine', historical_exhausted_870=True)
        self.assertEqual(accepted['status'], 'exhausted_candidates')
        self.assertEqual(accepted['successful_outgoing_roots'], 870)
        with self.assertRaisesRegex(ValueError, 'below 1000 total roots'):
            audit._precheck_target(progress, 'total-genuine')

    def test_historical_pilot_keeps_its_earlier_map_provenance(self):
        receipt = audit._verify_historical_pilot()
        self.assertEqual(receipt['profile_sha256'], audit.PILOT_PROFILE_SHA256)
        self.assertEqual(audit.sha(audit.HISTORICAL_SOURCE_MAP),
                         audit.HISTORICAL_SOURCE_MAP_SHA256)

    def test_changed_workspace_code_resolves_only_to_exact_hash_pinned_archive_copy(self):
        original = audit.ROOT / 'scripts/apply_artist_title_aliases.py'
        archive = audit.HISTORICAL_CODE_ARCHIVE / 'scripts/apply_artist_title_aliases.py'
        expected = '853e3c8a415e8a7910f60b046ab24c1ca643d16d7875c67f2fd6c1042d39c680'
        self.assertEqual(audit._resolve_frozen_code_copy(original, expected), archive)
        self.assertEqual(audit.sha(archive), expected)
        self.assertIsNone(audit._resolve_frozen_code_copy(original, '0' * 64))
        test_source = audit.ROOT / 'tests/test_expand_genuine_roots.py'
        test_archive = audit.HISTORICAL_CODE_ARCHIVE / 'tests/test_expand_genuine_roots.py'
        test_sha = '777166a5dde0437cce11946e9ee0b23eafccfa621e62154f09592145709ba3b4'
        self.assertEqual(audit._resolve_frozen_code_copy(test_source, test_sha), test_archive)
        merge_source = audit.ROOT / 'scripts/merge_rebased_radio_growth.py'
        merge_archive = audit.HISTORICAL_CODE_ARCHIVE / 'scripts/merge_rebased_radio_growth.py'
        merge_sha = '630b3b1d338e1512d79a4f3da2ec34b3cbbe77b09105692dff8a430ed57fdfbf'
        self.assertEqual(audit._resolve_frozen_code_copy(merge_source, merge_sha), merge_archive)

    def test_historical_mode_rejects_mutated_checkpoint_semantics(self):
        progress = audit.EXPECTED_PROGRESS_DIR / 'progress.json'
        original_load = audit.load
        try:
            def mutated_load(path):
                value = original_load(path)
                value['status'] = 'target_reached'
                return value
            audit.load = mutated_load
            with self.assertRaisesRegex(ValueError, 'exact exhausted 870-root progress checkpoint'):
                audit._precheck_target(progress, 'total-genuine', historical_exhausted_870=True)
        finally:
            audit.load = original_load

    def test_historical_partition_is_exact_and_never_meets_final_target(self):
        source = {'counts': {'native_only': 159, 'ytmusic_source_backed': 711,
                             'native_ytmusic_overlap': 0, 'unknown': 0, 'total_active': 870}}
        self.assertEqual(audit._require_historical_870_partition(source, 'total-genuine'), 870)
        with self.assertRaisesRegex(ValueError, 'fewer than 1000'):
            audit._require_goal(source, 'total-genuine')
        source['counts']['ytmusic_source_backed'] = 710
        source['counts']['native_only'] = 160
        with self.assertRaisesRegex(ValueError, 'exact Native159 plus YT711'):
            audit._require_historical_870_partition(source, 'total-genuine')


    def test_live_v15_process_receipt_matches_invocation_exactly(self):
        invocation = json.loads(audit.V15_INVOCATION.read_text(encoding='utf-8'))
        process = json.loads(audit.V15_PROCESS.read_text(encoding='utf-8'))
        self.assertEqual(set(process), set(invocation) | {'started_unix', 'worker_pid'})
        self.assertTrue(all(process[key] == value for key, value in invocation.items()))
        self.assertIs(type(process['worker_pid']), int)
        self.assertGreater(process['worker_pid'], 0)

    def test_v16_runner_and_runtime_are_pinned_without_claiming_completion(self):
        self.assertEqual(audit.sha(audit.V16_RECOVERY_RUNNER), audit.V16_RECOVERY_RUNNER_SHA256)
        self.assertEqual(audit.sha(audit.RUNTIME_MANIFEST), audit.RUNTIME_MANIFEST_SHA256)
        manifest = json.loads(audit.RUNTIME_MANIFEST.read_text(encoding='utf-8'))
        self.assertEqual(len(manifest['modules']), 184)
        self.assertEqual(audit.V16_TERMINAL.name, 'v16-recovery-continuation-terminal-v01.json')

    def test_final_audit_outputs_are_separate_from_live_campaign(self):
        output_dir = audit.ROOT / 'data/ytmusic/final-source-audits-20261015-v23'
        output = output_dir / 'final-source-audit-v23.json'
        self.assertNotEqual(output_dir.resolve(), audit.EXPECTED_PROGRESS_DIR.resolve())
        self.assertFalse(output.resolve().is_relative_to(audit.EXPECTED_PROGRESS_DIR.resolve()))
        self.assertEqual(output_dir.parent, audit.ROOT / 'data/ytmusic')



if __name__ == '__main__':
    unittest.main()
