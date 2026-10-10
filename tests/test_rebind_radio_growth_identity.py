"""Typed identity-map rebinding invariants for genuine radio state."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_identity_map import IdentityMap, build_map
from plan_genius_update import _source, make_synthetic_state, plan_genius_update
from rebind_radio_growth_identity import (
    _rebind_state_map_hash, _stable_map_delta,
    rebind_album_alias_growth_source, verify_album_alias_growth_rebinding_report,
    verify_reviewed_album_alias_map_delta,
)


LIBRARY = 'a' * 64
PID1, PID2 = '0000000000000001', '0000000000000002'


class RadioGrowthIdentityRebindingTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [
            {'persistent_id': PID1, 'title': 'Root', 'artist': 'Local A',
             'album': 'Album', 'duration_ms': 100000},
            {'persistent_id': PID2, 'title': 'Target', 'artist': 'Local B',
             'album': 'Album', 'duration_ms': 101000},
        ]
        self.old_map = build_map(self.tracks, LIBRARY)
        self.new_map = copy.deepcopy(self.old_map)
        self.new_map['artist_credit_aliases'] = [{
            'remote_credit': 'Remote B', 'local_credit': 'Local B',
            'enabled': True, 'status': 'reviewed_whole_credit_alias',
            'evidence': {'source_report_sha256': '1' * 64}}]
        self.old_bytes = json.dumps(self.old_map, ensure_ascii=False).encode()
        self.new_bytes = json.dumps(self.new_map, ensure_ascii=False).encode()
        self.old_hash = hashlib.sha256(self.old_bytes).hexdigest()
        self.new_hash = hashlib.sha256(self.new_bytes).hexdigest()
        self.old_matcher = IdentityMap(self.old_map, self.tracks, LIBRARY)
        self.new_matcher = IdentityMap(self.new_map, self.tracks, LIBRARY)

    def test_only_append_only_credit_aliases_can_rebind(self):
        old_aliases, new_aliases, title_delta = _stable_map_delta(self.old_map, self.new_map)
        self.assertEqual(old_aliases, [])
        self.assertEqual(len(new_aliases), 1)
        self.assertEqual(title_delta, [])
        _, old_labels = _source(self.old_matcher, LIBRARY, self.old_hash)
        _, new_labels = _source(self.new_matcher, LIBRARY, self.new_hash)
        self.assertEqual(old_labels, new_labels)

        changed_local = copy.deepcopy(self.new_map)
        changed_local['artist_alias_groups'] = [{'enabled': True, 'aliases': ['Local A', 'Different']}]
        with self.assertRaisesRegex(ValueError, 'differ beyond'):
            _stable_map_delta(self.old_map, changed_local)
        removed = copy.deepcopy(self.new_map)
        removed['artist_credit_aliases'] = []
        with self.assertRaisesRegex(ValueError, 'differ beyond'):
            _stable_map_delta(self.new_map, removed)

    def test_only_append_only_reviewed_title_alias_rows_can_rebind(self):
        base = copy.deepcopy(self.old_map)
        base['track_title_aliases'] = [{
            'persistent_id': PID1, 'original_title': 'Root',
            'aliases': [{'value': 'Root EN', 'enabled': False, 'status': 'needs_review'}]}]
        titled = copy.deepcopy(self.new_map)
        titled['track_title_aliases'] = copy.deepcopy(base['track_title_aliases'])
        titled['track_title_aliases'].append({
            'persistent_id': PID2, 'original_title': 'Target',
            'aliases': [{'value': 'Target - Target EN', 'enabled': True,
                         'status': 'reviewed_spelling_alias', 'reason': 'Exact title suffix.'}],
        })
        old_aliases, new_aliases, title_delta = _stable_map_delta(base, titled)
        self.assertEqual(len(new_aliases) - len(old_aliases), 1)
        self.assertEqual(len(title_delta), 1)
        mutated = copy.deepcopy(titled)
        mutated['track_title_aliases'][0]['original_title'] = 'Changed'
        with self.assertRaisesRegex(ValueError, 'append-only'):
            _stable_map_delta(base, mutated)

    def test_rebound_state_changes_only_map_hash_and_preserves_gid_and_edges(self):
        roots = [{'root_pid': PID1, 'ordered_target_pids': [PID2]},
                 {'root_pid': PID2, 'ordered_target_pids': []}]
        old_state = make_synthetic_state(roots, self.old_matcher, LIBRARY, self.old_hash)
        rebound = _rebind_state_map_hash(old_state, self.new_hash)
        self.assertNotEqual(old_state['state_sha256'], rebound['state_sha256'])
        self.assertEqual(old_state['rows'], rebound['rows'])
        self.assertEqual(old_state['source']['library_sha256'], rebound['source']['library_sha256'])
        self.assertEqual(rebound['source']['identity_map_sha256'], self.new_hash)
        self.assertEqual(plan_genius_update(rebound, roots, self.new_matcher,
                                            LIBRARY, self.new_hash)['next_state']['state_sha256'],
                         rebound['state_sha256'])

    def test_reviewed_album_transition_binds_exact_append_only_map_and_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {key: root / (key + '.json') for key in (
                'snapshot', 'input', 'output', 'proposal', 'evidence', 'audit', 'review', 'receipt')}
            paths['snapshot'].write_text('{}', encoding='utf-8')
            old_map = build_map(self.tracks, LIBRARY)
            new_map = copy.deepcopy(old_map)
            new_map['album_title_aliases'] = [{
                'enabled': True, 'canonical_library_album': 'Album',
                'exact_remote_album_aliases': ['REMOTE ALBUM'],
                'status': 'root_reviewed_metadata_alias'}]
            paths['input'].write_text(json.dumps(old_map), encoding='utf-8')
            paths['output'].write_text(json.dumps(new_map), encoding='utf-8')
            for key in ('proposal', 'evidence', 'audit', 'review'):
                paths[key].write_text(json.dumps({'source': key}), encoding='utf-8')

            def digest(path):
                return hashlib.sha256(path.read_bytes()).hexdigest()

            receipt = {
                'operation': 'apply_root_reviewed_exact_whole_album_metadata_alias',
                'library_sha256': LIBRARY,
                'track_snapshot_path': str(paths['snapshot'].resolve()),
                'track_snapshot_sha256': digest(paths['snapshot']),
                'input_identity_map_path': str(paths['input'].resolve()),
                'input_identity_map_sha256': digest(paths['input']),
                'output_identity_map_path': str(paths['output'].resolve()),
                'output_identity_map_sha256': digest(paths['output']),
                'proposals_path': str(paths['proposal'].resolve()),
                'proposal_sha256': digest(paths['proposal']),
                'source_evidence_path': str(paths['evidence'].resolve()),
                'source_evidence_sha256': digest(paths['evidence']),
                'candidate_audit_path': str(paths['audit'].resolve()),
                'candidate_audit_sha256': digest(paths['audit']),
                'review_decisions_path': str(paths['review'].resolve()),
                'review_decisions_sha256': digest(paths['review']),
                'canonical_library_fields_changed': False,
                'recording_identity_verified': False,
                'recording_edges_added': 0,
            }
            paths['receipt'].write_text(json.dumps(receipt), encoding='utf-8')
            rebuilt = {
                'input_identity_map_sha256': digest(paths['input']),
                'output_identity_map_sha256': digest(paths['output']),
                'recording_identity_verified': False, 'recording_edges_added': 0,
            }
            with patch('rebind_radio_growth_identity.load_library',
                       return_value=(self.tracks, LIBRARY, None)), \
                    patch('rebind_radio_growth_identity.verify_album_alias_transition',
                          return_value=rebuilt) as verify:
                proof = verify_reviewed_album_alias_map_delta(
                    track_snapshot=paths['snapshot'], input_identity_map_path=paths['input'],
                    output_identity_map_path=paths['output'], proposals_path=paths['proposal'],
                    source_evidence_path=paths['evidence'], candidate_audit_path=paths['audit'],
                    review_decisions_path=paths['review'], receipt_path=paths['receipt'])
            self.assertEqual(proof['appended_album_alias_count'], 1)
            self.assertEqual(proof['input_identity_map']['sha256'], digest(paths['input']))
            self.assertEqual(proof['output_identity_map']['sha256'], digest(paths['output']))
            self.assertFalse(proof['recording_identity_verified'])
            self.assertEqual(proof['recording_edges_added'], 0)
            verify.assert_called_once()

            original_input = paths['input'].read_bytes()
            original_output = paths['output'].read_bytes()
            original_proposal = paths['proposal'].read_bytes()
            with patch('rebind_radio_growth_identity.load_library',
                       return_value=(self.tracks, LIBRARY, None)):
                paths['input'].write_text(json.dumps({**old_map, 'other_field': True}), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'append-only|receipt does not bind'):
                    verify_reviewed_album_alias_map_delta(
                        track_snapshot=paths['snapshot'], input_identity_map_path=paths['input'],
                        output_identity_map_path=paths['output'], proposals_path=paths['proposal'],
                        source_evidence_path=paths['evidence'], candidate_audit_path=paths['audit'],
                        review_decisions_path=paths['review'], receipt_path=paths['receipt'])
                paths['input'].write_bytes(original_input)

                paths['proposal'].write_text('{"tampered": true}', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'receipt does not bind'):
                    verify_reviewed_album_alias_map_delta(
                        track_snapshot=paths['snapshot'], input_identity_map_path=paths['input'],
                        output_identity_map_path=paths['output'], proposals_path=paths['proposal'],
                        source_evidence_path=paths['evidence'], candidate_audit_path=paths['audit'],
                        review_decisions_path=paths['review'], receipt_path=paths['receipt'])
                paths['proposal'].write_bytes(original_proposal)

                paths['output'].write_text(json.dumps({**new_map, 'other_field': True}), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'beyond an append-only album alias'):
                    verify_reviewed_album_alias_map_delta(
                        track_snapshot=paths['snapshot'], input_identity_map_path=paths['input'],
                        output_identity_map_path=paths['output'], proposals_path=paths['proposal'],
                        source_evidence_path=paths['evidence'], candidate_audit_path=paths['audit'],
                        review_decisions_path=paths['review'], receipt_path=paths['receipt'])
                paths['output'].write_bytes(original_output)

                changed_alias = copy.deepcopy(new_map)
                changed_alias['album_title_aliases'][0]['exact_remote_album_aliases'] = ['TAMPERED']
                paths['output'].write_text(json.dumps(changed_alias), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'receipt does not bind'):
                    verify_reviewed_album_alias_map_delta(
                        track_snapshot=paths['snapshot'], input_identity_map_path=paths['input'],
                        output_identity_map_path=paths['output'], proposals_path=paths['proposal'],
                        source_evidence_path=paths['evidence'], candidate_audit_path=paths['audit'],
                        review_decisions_path=paths['review'], receipt_path=paths['receipt'])

    def test_album_alias_growth_rebind_preserves_parent_graph_and_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_map = build_map(self.tracks, LIBRARY)
            new_map = copy.deepcopy(old_map)
            new_map['album_title_aliases'] = [{
                'enabled': True, 'canonical_library_album': 'Album',
                'exact_remote_album_aliases': ['Remote Album'],
                'status': 'root_reviewed_metadata_alias'}]
            old_map_path, new_map_path = root / 'old.json', root / 'new.json'
            snapshot_path = root / 'snapshot.json'
            old_map_path.write_text(json.dumps(old_map), encoding='utf-8')
            new_map_path.write_text(json.dumps(new_map), encoding='utf-8')
            snapshot_path.write_text('{}', encoding='utf-8')
            old_hash = hashlib.sha256(old_map_path.read_bytes()).hexdigest()
            new_hash = hashlib.sha256(new_map_path.read_bytes()).hexdigest()
            matcher = IdentityMap(old_map, self.tracks, LIBRARY)
            roots = [{'root_pid': pid, 'ordered_target_pids': []}
                     for pid in (PID1, PID2)]
            parent_state = json.loads(json.dumps(
                make_synthetic_state(roots, matcher, LIBRARY, old_hash)))
            child_state = _rebind_state_map_hash(parent_state, new_hash)
            state_rows_path = root / 'parent-state.json'
            state_rows_path.write_text(json.dumps(parent_state), encoding='utf-8')
            child_state_path = root / 'child-state.json'
            child_state_path.write_text(json.dumps(child_state), encoding='utf-8')
            parent_graph = {'accepted_roots': roots}
            parent_graph_path = root / 'parent-graph.json'
            parent_graph_path.write_text(json.dumps(parent_graph), encoding='utf-8')
            parent_report = {
                'identity_map_rebinding': {
                    'old_identity_map': {'path': str(old_map_path.resolve()), 'sha256': old_hash},
                    'new_identity_map': {'path': str(old_map_path.resolve()), 'sha256': old_hash}},
                'relation_revalidation': {}, 'raw_input_provenance': [],
            }
            parent_report_path = root / 'parent-report.json'
            parent_report_path.write_text(json.dumps(parent_report), encoding='utf-8')
            source_paths = {}
            for key in ('receipt', 'proposal', 'evidence', 'audit', 'review'):
                source_paths[key] = root / (key + '.json')
                source_paths[key].write_text(json.dumps({'kind': key}), encoding='utf-8')
            def receipt(key):
                path = source_paths[key]
                return {'path': str(path.resolve()),
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            album_proof = {
                'schema_version': 1,
                'producer': 'opengenius-typed-reviewed-album-alias-map-transition-v1',
                'library_sha256': LIBRARY,
                'track_snapshot': {'path': str(snapshot_path.resolve()),
                                   'sha256': hashlib.sha256(snapshot_path.read_bytes()).hexdigest()},
                'input_identity_map': {'path': str(old_map_path.resolve()), 'sha256': old_hash},
                'output_identity_map': {'path': str(new_map_path.resolve()), 'sha256': new_hash},
                'application_receipt': receipt('receipt'), 'proposals': receipt('proposal'),
                'source_evidence': receipt('evidence'), 'candidate_audit': receipt('audit'),
                'review_decisions': receipt('review'), 'appended_album_alias_count': 1,
                'recording_identity_verified': False, 'recording_edges_added': 0,
            }
            child_binding = {
                'schema_version': 1,
                'parent_typed_growth': {
                    'graph_export': {'path': str(parent_graph_path.resolve()),
                                     'sha256': hashlib.sha256(parent_graph_path.read_bytes()).hexdigest()},
                    'rebase_report': {'path': str(parent_report_path.resolve()),
                                      'sha256': hashlib.sha256(parent_report_path.read_bytes()).hexdigest()},
                    'state': {'path': str(state_rows_path.resolve()),
                              'sha256': hashlib.sha256(state_rows_path.read_bytes()).hexdigest()},
                    'old_identity_map': {'path': str(old_map_path.resolve()), 'sha256': old_hash},
                    'new_identity_map': {'path': str(old_map_path.resolve()), 'sha256': old_hash},
                },
                'album_alias_transition': album_proof,
                'output_identity_map': album_proof['output_identity_map'],
                'track_snapshot': album_proof['track_snapshot'],
                'old_state_sha256': parent_state['state_sha256'],
                'new_state_sha256': child_state['state_sha256'],
                'accepted_input_manifest_sha256': 'manifest',
                'authenticated_observation_inputs': [],
                'authenticated_observation_input_count': 0,
                'local_track_metadata_unchanged': True,
                'local_equivalence_labels_unchanged': True,
                'pid_gid_assignments_unchanged': True,
                'metadata_bytes_unchanged': True,
                'relation_bytes_unchanged': True,
                'old_growth_replayed_with_parent_map': True,
                'recording_identity_verified': False,
                'recording_edges_added_by_rebinding': 0,
            }
            child_report = {
                'producer': 'opengenius-typed-album-alias-growth-rebinding-v1',
                'zero_id_library_sha256': LIBRARY, 'identity_map_sha256': new_hash,
                'next_state_sha256': child_state['state_sha256'],
                'source_state_path': str(child_state_path.resolve()),
                'album_alias_growth_rebinding': child_binding,
                'relation_revalidation': {}, 'raw_input_provenance': [],
            }
            child_graph = {'accepted_roots': roots}
            parent_result = {
                'verified': True, 'old_identity_map_sha256': old_hash,
                'new_identity_map_sha256': old_hash, 'proof_input_receipts': [],
            }
            with patch('rebind_radio_growth_identity.load_library',
                       return_value=(self.tracks, LIBRARY, None)), \
                    patch('merge_rebased_radio_growth.verify_typed_identity_map_rebinding',
                          return_value=parent_result), \
                    patch('rebind_radio_growth_identity.verify_reviewed_album_alias_map_delta',
                          return_value=album_proof):
                proof = verify_album_alias_growth_rebinding_report(
                    graph_doc=child_graph, report=child_report, state=child_state,
                    library_hash=LIBRARY, identity_hash=new_hash,
                    state_path=child_state_path)
            self.assertGreaterEqual(len(proof), 10)
            self.assertIn(album_proof['output_identity_map'], proof)
            tampered = copy.deepcopy(child_state)
            tampered['rows'][0]['title'] = 'tampered'
            with patch('rebind_radio_growth_identity.load_library',
                       return_value=(self.tracks, LIBRARY, None)), \
                    patch('merge_rebased_radio_growth.verify_typed_identity_map_rebinding',
                          return_value=parent_result), \
                    patch('rebind_radio_growth_identity.verify_reviewed_album_alias_map_delta',
                          return_value=album_proof):
                with self.assertRaisesRegex(ValueError, 'changed fields beyond'):
                    verify_album_alias_growth_rebinding_report(
                        graph_doc=child_graph, report=child_report, state=tampered,
                        library_hash=LIBRARY, identity_hash=new_hash,
                        state_path=child_state_path)


if __name__ == '__main__':
    unittest.main()
