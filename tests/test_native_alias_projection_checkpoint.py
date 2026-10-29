import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import merge_rebased_radio_growth as merger
import expand_genuine_roots as runner


LIBRARY = 'a' * 64


class NativeAliasProjectionCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.track = self.root / 'tracks.json'
        self.track.write_text(json.dumps({'source_sha256': LIBRARY}))
        self.identity = self.root / 'identity.json'
        self.identity.write_text('{"map":"v13"}')
        self.identity_hash = merger.sha(self.identity.read_bytes())
        self.baseline = self.root / 'baseline'
        self.baseline.mkdir()
        self.base_graph_path = self.baseline / 'accepted-baseline-graphs.json'
        self.base_report_path = self.baseline / 'baseline-rebase-report.json'
        self.base_state_path = self.baseline / 'baseline-state.json'
        self.base_graph_path.write_text('{}')
        self.base_report_path.write_text('{}')
        self.base_state_path.write_text('{}')
        self.child = self.root / 'alias-child'
        self.child.mkdir()
        self.child_graph_path = self.child / 'accepted-baseline-graphs.json'
        self.child_report_path = self.child / 'identity-map-rebinding-report.json'
        self.child_state_path = self.child / 'state.json'
        self.child_graph_path.write_text('{}')
        self.child_report_path.write_text('{}')
        self.child_state_path.write_text('{}')

        self.baseline_state = {
            'state_sha256': 'base-state',
            'rows': [
                {'persistent_id': 'P1', 'genius_id': 'G1', 'metadata_hex': 'm1',
                 'equivalence_labels': ['l1']},
                {'persistent_id': 'P2', 'genius_id': 'G2', 'metadata_hex': 'm2',
                 'equivalence_labels': ['l2']},
            ],
        }
        self.child_state = {
            'state_sha256': 'child-state',
            'rows': self.baseline_state['rows'] + [
                {'persistent_id': 'P3', 'genius_id': 'G3', 'metadata_hex': 'm3',
                 'equivalence_labels': ['l3']},
            ],
        }
        self.projected_state = {
            'state_sha256': 'projected-state',
            'rows': self.baseline_state['rows'] + [
                {'persistent_id': 'P3', 'genius_id': 'G3', 'metadata_hex': 'm3',
                 'equivalence_labels': ['l3']},
            ],
        }
        self.baseline_roots = [
            {'root_pid': 'P1', 'ordered_target_pids': ['P2']},
            {'root_pid': 'P2', 'ordered_target_pids': []},
        ]
        self.child_roots = [
            {'root_pid': 'P1', 'ordered_target_pids': ['P2', 'P3']},
            {'root_pid': 'P2', 'ordered_target_pids': []},
        ]
        self.projected_roots = self.child_roots + [
            {'root_pid': 'P3', 'ordered_target_pids': []},
        ]
        self.provenance = {
            'graph_export_path': str(self.base_graph_path),
            'rebase_report_path': str(self.base_report_path),
            'state_path': str(self.base_state_path),
            'proof_input_receipts': [],
        }
        self.baseline_doc = {'accepted_roots': self.baseline_roots}
        self.child_doc = {
            'producer': 'verified-identity-map-rebound-state-graph-export',
            'source_policy': 'authenticated-watch-radio-only-v1',
            'accepted_roots': self.child_roots,
        }
        self.child_report = {
            'producer': 'opengenius-typed-native-genius-baseline-alias-child-v1',
            'recording_identity_verified': False,
            'recording_edges_added': 0,
            'source_policy': 'authenticated-watch-radio-only-v1',
        }

    def tearDown(self):
        self.temp.cleanup()

    def _verify_rebase(self, graph_path, report_path, state_path, library_hash, identity_hash):
        if Path(graph_path).resolve() == self.base_graph_path.resolve():
            return self.baseline_doc, {}, self.baseline_state, self.provenance
        return self.child_doc, self.child_report, self.child_state, {
            'proof_input_receipts': [],
        }

    def _state_rows(self, state):
        if state['state_sha256'] == 'base-state':
            return self.baseline_roots
        return self.projected_roots

    def _invalid_state_rows(self, state):
        if state['state_sha256'] == 'base-state':
            return self.baseline_roots
        return self.projected_roots[:-1] + [
            {'root_pid': 'P3', 'ordered_target_pids': ['P1']},
        ]

    def test_projection_checkpoint_replays_native_state_and_empty_rows(self):
        output = self.root / 'projection'
        with (patch.object(merger, '_verify_rebase_receipt', side_effect=self._verify_rebase),
              patch.object(merger, 'load_library', return_value=([], LIBRARY, {})),
              patch.object(merger, 'IdentityMap', return_value=object()),
              patch.object(merger, '_state_graph_rows', side_effect=self._state_rows),
              patch.object(merger, 'plan_genius_update',
                           return_value={'next_state': self.projected_state})):
            produced = merger.create_native_alias_projection_checkpoint(
                track_snapshot=self.track, identity_map_path=self.identity,
                baseline_state_path=self.base_state_path,
                baseline_graph_export_path=self.base_graph_path,
                rebase_report_path=self.base_report_path,
                child_dir=self.child, output_dir=output)
            self.assertEqual(produced['producer'],
                             'opengenius-radio-native-alias-projection-checkpoint-v1')
            self.assertEqual(produced['outgoing_root_count'], 1)
            self.assertEqual(produced['edge_count'], 2)
            self.assertEqual(produced['http_requests_added'], 0)
            self.assertEqual(produced['attempted_pid_delta'], 0)
            runner._validate_growth_receipts(output)
            replayed = merger._replay_previous_growth(
                output, self.baseline_state, self.baseline_roots,
                self.provenance, object(), LIBRARY, self.identity_hash)
        self.assertEqual(replayed[0], self.projected_state)
        self.assertEqual(replayed[1], self.projected_roots)
        self.assertEqual(len(replayed[2]), 1)
        self.assertEqual(replayed[2][0]['http_requests'], 0)
        self.assertEqual(replayed[2][0]['attempted_pid_delta'], 0)
        transition = replayed[2][0]['source_policy_transition']
        self.assertEqual(merger._validate_source_policy_history(
            'authenticated-watch-radio-only-v1', transition,
            [replayed[2][0], {'source_policy': 'authenticated-watch-radio-only-v1'}]), 0)

    def test_projection_rejects_nonempty_unverified_state_row(self):
        output = self.root / 'projection'
        with (patch.object(merger, '_verify_rebase_receipt', side_effect=self._verify_rebase),
              patch.object(merger, 'load_library', return_value=([], LIBRARY, {})),
              patch.object(merger, 'IdentityMap', return_value=object()),
              patch.object(merger, '_state_graph_rows', side_effect=self._invalid_state_rows),
              patch.object(merger, 'plan_genius_update',
                           return_value={'next_state': self.projected_state})):
            with self.assertRaisesRegex(ValueError, 'unverified nonempty'):
                merger.create_native_alias_projection_checkpoint(
                    track_snapshot=self.track, identity_map_path=self.identity,
                    baseline_state_path=self.base_state_path,
                    baseline_graph_export_path=self.base_graph_path,
                    rebase_report_path=self.base_report_path,
                    child_dir=self.child, output_dir=output)
        self.assertFalse(output.exists())

    def test_projection_replay_rejects_checkpoint_tampering(self):
        output = self.root / 'projection'
        with (patch.object(merger, '_verify_rebase_receipt', side_effect=self._verify_rebase),
              patch.object(merger, 'load_library', return_value=([], LIBRARY, {})),
              patch.object(merger, 'IdentityMap', return_value=object()),
              patch.object(merger, '_state_graph_rows', side_effect=self._state_rows),
              patch.object(merger, 'plan_genius_update',
                           return_value={'next_state': self.projected_state})):
            merger.create_native_alias_projection_checkpoint(
                track_snapshot=self.track, identity_map_path=self.identity,
                baseline_state_path=self.base_state_path,
                baseline_graph_export_path=self.base_graph_path,
                rebase_report_path=self.base_report_path,
                child_dir=self.child, output_dir=output)
            evaluation_path = output / 'accepted-evaluation.json'
            evaluation = json.loads(evaluation_path.read_bytes())
            evaluation['http_requests_added'] = 1
            evaluation_path.write_text(json.dumps(evaluation))
            with self.assertRaisesRegex(ValueError, 'cannot be reproduced exactly'):
                merger._replay_previous_growth(
                    output, self.baseline_state, self.baseline_roots,
                    self.provenance, object(), LIBRARY, self.identity_hash)

    def test_projection_remains_first_run_in_a_later_growth_checkpoint(self):
        projection = self.root / 'projection'
        growth = self.root / 'growth'
        growth.mkdir()
        with (patch.object(merger, '_verify_rebase_receipt', side_effect=self._verify_rebase),
              patch.object(merger, 'load_library', return_value=([], LIBRARY, {})),
              patch.object(merger, 'IdentityMap', return_value=object()),
              patch.object(merger, '_state_graph_rows', side_effect=self._state_rows),
              patch.object(merger, 'plan_genius_update',
                           return_value={'next_state': self.projected_state})):
            merger.create_native_alias_projection_checkpoint(
                track_snapshot=self.track, identity_map_path=self.identity,
                baseline_state_path=self.base_state_path,
                baseline_graph_export_path=self.base_graph_path,
                rebase_report_path=self.base_report_path,
                child_dir=self.child, output_dir=projection)
            prior = json.loads((projection / 'accepted-graphs.json').read_bytes())
            projection_run = prior['growth_runs'][0]
            later_run = {'source_policy': 'authenticated-watch-radio-only-v1'}
            accepted = {
                'producer': 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1',
                'library_sha256': LIBRARY,
                'identity_map_sha256': self.identity_hash,
                'baseline_state_sha256': self.baseline_state['state_sha256'],
                'baseline_accepted_roots': self.baseline_roots,
                'baseline_rebase_provenance': self.provenance,
                'source_policy': 'authenticated-watch-radio-only-v1',
                'source_policy_transition': projection_run['source_policy_transition'],
                'growth_runs': [projection_run, later_run],
                'accepted_roots': self.projected_roots,
            }
            (growth / 'accepted-graphs.json').write_text(json.dumps(accepted))
            (growth / 'state.json').write_text(json.dumps(self.projected_state))
            (growth / 'relations.json').write_text(json.dumps({'relation_rows': self.projected_roots}))
            (growth / 'accepted-evaluation.json').write_text(json.dumps({
                'virtual_state_sha256': self.projected_state['state_sha256'],
                'relation_rows': self.projected_roots,
            }))
            with patch.object(merger, '_validate_run_receipt',
                              return_value=([], {'source_policy': 'authenticated-watch-radio-only-v1'})):
                state, roots, runs, _ = merger._replay_previous_growth(
                    growth, self.baseline_state, self.baseline_roots,
                    self.provenance, SimpleNamespace(tracks=[]), LIBRARY, self.identity_hash)
        self.assertEqual(state, self.projected_state)
        self.assertEqual(roots, self.projected_roots)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]['producer'], 'opengenius-radio-native-alias-projection-run-v1')


if __name__ == '__main__':
    unittest.main()
