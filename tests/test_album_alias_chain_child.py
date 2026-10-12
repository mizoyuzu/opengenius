import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import rebind_radio_growth_alias_chain as chain
import merge_rebased_radio_growth as merger
from music_identity_map import IdentityMap, build_map
from plan_genius_update import make_synthetic_state


LIBRARY = 'a' * 64
PID1, PID2, PID3 = '0000000000000001', '0000000000000002', '0000000000000003'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def receipt(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': digest(path)}


class AlbumAliasChainChildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tracks = [
            {'persistent_id': PID1, 'title': 'Root', 'artist': 'Artist A',
             'album': 'Album', 'duration_ms': 100000},
            {'persistent_id': PID2, 'title': 'Target', 'artist': 'Artist B',
             'album': 'Album', 'duration_ms': 101000},
            {'persistent_id': PID3, 'title': 'Metadata-only', 'artist': 'Artist C',
             'album': 'Album', 'duration_ms': 102000},
        ]
        self.snapshot = self.write('snapshot.json', {'tracks': self.tracks})
        self.map11_doc = build_map(self.tracks, LIBRARY)
        self.map12_doc = copy.deepcopy(self.map11_doc)
        self.map12_doc['album_title_aliases'] = [{
            'enabled': True, 'canonical_library_album': 'Album',
            'exact_remote_album_aliases': ['REMOTE ALBUM ONE'],
            'status': 'root_reviewed_metadata_alias'}]
        self.map13_doc = copy.deepcopy(self.map12_doc)
        self.map13_doc['album_title_aliases'].append({
            'enabled': True, 'canonical_library_album': 'Album',
            'exact_remote_album_aliases': ['REMOTE ALBUM TWO'],
            'status': 'root_reviewed_metadata_alias'})
        self.map11 = self.write('map-v11.json', self.map11_doc)
        self.map12 = self.write('map-v12.json', self.map12_doc)
        self.map13 = self.write('map-v13.json', self.map13_doc)
        self.transitions = [self.transition('one', self.map11, self.map12),
                            self.transition('two', self.map12, self.map13)]
        roots = [{'root_pid': PID1, 'ordered_target_pids': [PID2]},
                 {'root_pid': PID2, 'ordered_target_pids': []}]
        state_roots = roots + [{'root_pid': PID3, 'ordered_target_pids': []}]
        self.state = make_synthetic_state(
            state_roots, IdentityMap(self.map11_doc, self.tracks, LIBRARY), LIBRARY,
            digest(self.map11))
        self.parent_state = self.write('parent/state.json', self.state)
        self.parent_report = self.write('parent/identity-map-rebinding-report.json', {
            'schema_version': 1, 'producer': chain.CHAIN_PRODUCER,
            'zero_id_library_sha256': LIBRARY, 'identity_map_sha256': digest(self.map11),
            'next_state_sha256': self.state['state_sha256'],
            'source_state_path': str(self.parent_state.resolve()),
            'source_policy': 'authenticated-watch-radio-only-v1',
            'relation_revalidation': {
                'all_original_observation_hashes_verified': True,
                'all_retained_edges_same_pid_and_fresh_metadata_match': True,
                'output_directed_edge_count': 1,
                'dropped_old_directed_edge_count': 0,
                'new_matched_edge_count': 0,
                'source_observation_count': 0,
                'raw_input_provenance': [],
                'accepted_input_manifest': {'anchored_observation_inputs': []}},
            'identity_map_rebinding': {
                'source_policy': 'authenticated-watch-radio-only-v1',
                'old_identity_map': receipt(self.map11),
                'new_identity_map': receipt(self.map11)},
            'raw_input_provenance': [], 'accepted_root_count': 1,
            'directed_edge_count': 1})
        self.parent_graph = self.write('parent/accepted-baseline-graphs.json', {
            'schema_version': 1, 'producer': chain.GRAPH_PRODUCER,
            'source_state_path': str(self.parent_state.resolve()),
            'source_state_sha256': digest(self.parent_state),
            'source_identity_map_rebinding_report_sha256': digest(self.parent_report),
            'accepted_roots': roots, 'identity_status': 'unverified_metadata_candidate'})
        parent_graph_doc = json.loads(self.parent_graph.read_bytes())
        parent_graph_doc['source_policy'] = 'authenticated-watch-radio-only-v1'
        self.parent_graph.write_text(json.dumps(parent_graph_doc, ensure_ascii=False,
                                                sort_keys=True), encoding='utf-8')
        self.parent_receipts = [receipt(p) for p in (
            self.parent_graph, self.parent_report, self.parent_state, self.map11)]

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, doc):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        return path.resolve()

    def transition(self, stem, input_map, output_map):
        files = {key: self.write(f'{stem}/{key}.json', {'kind': key}) for key in (
            'proposals', 'source_evidence', 'candidate_audit', 'review_decisions', 'receipt')}
        return {
            'input_identity_map_path': str(input_map),
            'output_identity_map_path': str(output_map),
            'proposals_path': str(files['proposals']),
            'source_evidence_path': str(files['source_evidence']),
            'candidate_audit_path': str(files['candidate_audit']),
            'review_decisions_path': str(files['review_decisions']),
            'receipt_path': str(files['receipt']),
        }

    def fake_delta(self, **kwargs):
        input_map = Path(kwargs['input_identity_map_path']).resolve()
        output_map = Path(kwargs['output_identity_map_path']).resolve()
        in_doc = json.loads(input_map.read_bytes())
        out_doc = json.loads(output_map.read_bytes())
        if (in_doc.get('artist_credit_aliases') != out_doc.get('artist_credit_aliases')
                or in_doc.get('track_title_aliases') != out_doc.get('track_title_aliases')
                or out_doc['album_title_aliases'][:len(in_doc.get('album_title_aliases', []))]
                != in_doc.get('album_title_aliases', [])):
            raise ValueError('fake source verifier rejects non-append-only delta')
        keys = {
            'track_snapshot': kwargs['track_snapshot'],
            'input_identity_map': input_map, 'output_identity_map': output_map,
            'application_receipt': kwargs['receipt_path'],
            'proposals': kwargs['proposals_path'],
            'source_evidence': kwargs['source_evidence_path'],
            'candidate_audit': kwargs['candidate_audit_path'],
            'review_decisions': kwargs['review_decisions_path'],
        }
        return {
            'schema_version': 1,
            **{name: receipt(path) for name, path in keys.items()},
            'appended_album_alias_count': len(out_doc['album_title_aliases'])
            - len(in_doc.get('album_title_aliases', [])),
            'recording_identity_verified': False, 'recording_edges_added': 0,
        }

    def run_rebind(self, transitions=None, output_name='child', output_map=None):
        destination = self.root / output_name
        with patch.object(chain, 'load_library', return_value=(self.tracks, LIBRARY, None)), \
                patch.object(chain, 'verify_alias_chain_growth_rebinding_report',
                             return_value=self.parent_receipts), \
                patch.object(chain, 'verify_reviewed_album_alias_map_delta',
                             side_effect=self.fake_delta):
            return chain.rebind_album_alias_chain_child(
                parent_graph_path=self.parent_graph,
                parent_report_path=self.parent_report,
                parent_state_path=self.parent_state,
                input_identity_map=self.map11, output_identity_map=self.map13 if output_map is None else output_map,
                track_snapshot=self.snapshot,
                transitions=self.transitions if transitions is None else transitions,
                output_dir=destination)

    def verify_child(self, result, output_map=None):
        graph = json.loads(Path(result['graph_path']).read_bytes())
        report = json.loads(Path(result['report_path']).read_bytes())
        state = json.loads(Path(result['state_path']).read_bytes())
        with patch.object(chain, 'load_library', return_value=(self.tracks, LIBRARY, None)), \
                patch.object(chain, 'verify_alias_chain_growth_rebinding_report',
                             return_value=self.parent_receipts), \
                patch.object(chain, 'verify_reviewed_album_alias_map_delta',
                             side_effect=self.fake_delta):
            return chain.verify_album_alias_chain_growth_rebinding_report(
                graph_doc=graph, report=report, state=state, library_hash=LIBRARY,
                identity_hash=digest(self.map13 if output_map is None else output_map), state_path=Path(result['state_path']))

    def test_two_ordered_deltas_write_and_freshly_replay_zero_edge_child(self):
        result = self.run_rebind()
        receipts = self.verify_child(result)
        graph = json.loads(Path(result['graph_path']).read_bytes())
        report = json.loads(Path(result['report_path']).read_bytes())
        self.assertEqual(result['preserved_directed_edges'], 1)
        self.assertEqual(result['accepted_root_count'], 1)
        self.assertEqual(result['preserved_tracks'], 3)
        self.assertFalse(result['recording_identity_verified'])
        self.assertEqual(result['recording_edges_added'], 0)
        binding = report['album_alias_chain_growth_rebinding']
        self.assertEqual(len(binding['album_alias_transitions']), 2)
        self.assertEqual(binding['album_alias_transitions'][0]['output_identity_map']['path'],
                         str(self.map12))
        self.assertEqual(binding['album_alias_transitions'][1]['input_identity_map']['path'],
                         str(self.map12))
        self.assertEqual(receipts, binding['proof_input_receipts'])
        self.assertEqual(report['source_policy'], 'authenticated-watch-radio-only-v1')
        self.assertEqual(binding['source_policy'], 'authenticated-watch-radio-only-v1')
        self.assertEqual(graph['source_policy'], 'authenticated-watch-radio-only-v1')
        with patch.object(chain, 'verify_alias_chain_growth_rebinding_report',
                          return_value=self.parent_receipts), \
                patch.object(chain, 'verify_reviewed_album_alias_map_delta',
                             side_effect=self.fake_delta), \
                patch.object(chain, 'load_library',
                             return_value=(self.tracks, LIBRARY, None)):
            _, dispatched_report, dispatched_state, provenance = merger._verify_rebase_receipt(
                result['graph_path'], result['report_path'], result['state_path'],
                LIBRARY, digest(self.map13))
        self.assertEqual(dispatched_report['source_policy'],
                         'authenticated-watch-radio-only-v1')
        self.assertEqual(dispatched_state['state_sha256'], result['state_sha256'])
        self.assertGreaterEqual(len(provenance['proof_input_receipts']), 1)

    def test_single_reviewed_sanoba_delta_writes_and_replays_child(self):
        result = self.run_rebind([self.transitions[0]], 'single-delta', self.map12)
        receipts = self.verify_child(result, self.map12)
        binding = json.loads(Path(result['report_path']).read_bytes())[
            'album_alias_chain_growth_rebinding']
        self.assertEqual(len(binding['album_alias_transitions']), 1)
        self.assertEqual(binding['album_alias_transitions'][0]['output_identity_map']['path'],
                         str(self.map12))
        self.assertEqual(binding['proof_input_receipts'], receipts)
        self.assertEqual(result['preserved_directed_edges'], 1)
        self.assertEqual(result['recording_edges_added'], 0)
        saved = json.loads(Path(result['state_path']).read_bytes())
        self.assertEqual(len(saved['rows']), 3)
        self.assertIn(PID3, {row['persistent_id'] for row in saved['rows']})
        self.assertNotIn(PID3, {row['root_pid'] for row in json.loads(
            Path(result['graph_path']).read_bytes())['accepted_roots']})

    def test_state_projection_rejects_missing_graph_pid_and_state_only_edges(self):
        graph = [{'root_pid': PID1, 'ordered_target_pids': [PID2]},
                 {'root_pid': PID2, 'ordered_target_pids': []}]
        state_rows = graph + [{'root_pid': PID3, 'ordered_target_pids': []}]
        checked, _ = chain._validate_state_graph_relation_projection(
            graph, state_rows, 'fixture')
        self.assertEqual(checked, {PID1: [PID2], PID2: []})
        with self.assertRaisesRegex(ValueError, 'missing from complete state'):
            chain._validate_state_graph_relation_projection(graph, [graph[0]], 'missing')
        with self.assertRaisesRegex(ValueError, 'state-only PID has outgoing edges'):
            chain._validate_state_graph_relation_projection(
                graph, graph + [{'root_pid': PID3, 'ordered_target_pids': [PID1]}], 'extra-edge')

    def test_single_delta_rejects_missing_or_tampered_gid_rows(self):
        result = self.run_rebind([self.transitions[0]], 'state-tamper', self.map12)
        state_path = Path(result['state_path'])
        state = json.loads(state_path.read_bytes())
        state['rows'].pop()
        state['state_sha256'] = chain.state_hash({k: v for k, v in state.items()
                                                  if k != 'state_sha256'})
        state_path.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True),
                              encoding='utf-8')
        with self.assertRaises(ValueError):
            self.verify_child(result, self.map12)

        result = self.run_rebind([self.transitions[0]], 'gid-tamper', self.map12)
        state_path = Path(result['state_path'])
        state = json.loads(state_path.read_bytes())
        state['rows'][0]['genius_id'] += 1
        state['state_sha256'] = chain.state_hash({k: v for k, v in state.items()
                                                  if k != 'state_sha256'})
        state_path.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True),
                              encoding='utf-8')
        with self.assertRaises(ValueError):
            self.verify_child(result, self.map12)

    def test_reordered_or_skipped_intermediate_delta_rejects(self):
        reordered = list(self.transitions)
        reordered.reverse()
        with self.assertRaisesRegex(ValueError, 'reordered, skipped, or cyclic'):
            self.run_rebind(reordered, 'reordered')
        skipped = [self.transitions[0]]
        with self.assertRaisesRegex(ValueError, 'does not terminate'):
            self.run_rebind(skipped, 'skipped')

    def test_intermediate_map_or_source_tampering_fails_fresh_replay(self):
        result = self.run_rebind()
        original = self.map12.read_bytes()
        self.map12.write_text(json.dumps({'tampered': True}), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.verify_child(result)
        self.map12.write_bytes(original)
        proposal = Path(self.transitions[1]['proposals_path'])
        original = proposal.read_bytes()
        proposal.write_text('{"tampered": true}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.verify_child(result)
        proposal.write_bytes(original)
        self.assertEqual(self.verify_child(result),
                         json.loads(Path(result['report_path']).read_bytes())[
                             'album_alias_chain_growth_rebinding']['proof_input_receipts'])

    def test_forged_parent_report_receipt_fails(self):
        result = self.run_rebind()
        graph = json.loads(Path(result['graph_path']).read_bytes())
        report = json.loads(Path(result['report_path']).read_bytes())
        state = json.loads(Path(result['state_path']).read_bytes())
        binding = report['album_alias_chain_growth_rebinding']
        binding['parent_typed_growth']['rebase_report']['sha256'] = '0' * 64
        with patch.object(chain, 'load_library', return_value=(self.tracks, LIBRARY, None)), \
                patch.object(chain, 'verify_alias_chain_growth_rebinding_report',
                             return_value=self.parent_receipts), \
                patch.object(chain, 'verify_reviewed_album_alias_map_delta',
                             side_effect=self.fake_delta):
            with self.assertRaisesRegex(ValueError, 'parent report changed'):
                chain.verify_album_alias_chain_growth_rebinding_report(
                    graph_doc=graph, report=report, state=state, library_hash=LIBRARY,
                    identity_hash=digest(self.map13), state_path=Path(result['state_path']))

    def test_child_policy_omission_or_downgrade_fails(self):
        result = self.run_rebind()
        graph = json.loads(Path(result['graph_path']).read_bytes())
        report = json.loads(Path(result['report_path']).read_bytes())
        state = json.loads(Path(result['state_path']).read_bytes())
        graph.pop('source_policy')
        graph['source_identity_map_rebinding_report_sha256'] = digest(result['report_path'])
        with patch.object(chain, 'load_library', return_value=(self.tracks, LIBRARY, None)), \
                patch.object(chain, 'verify_alias_chain_growth_rebinding_report',
                             return_value=self.parent_receipts), \
                patch.object(chain, 'verify_reviewed_album_alias_map_delta',
                             side_effect=self.fake_delta):
            with self.assertRaisesRegex(ValueError, 'did not inherit exact parent source policy'):
                chain.verify_album_alias_chain_growth_rebinding_report(
                    graph_doc=graph, report=report, state=state, library_hash=LIBRARY,
                    identity_hash=digest(self.map13), state_path=Path(result['state_path']))

    def test_merge_dispatch_rejects_erased_policy(self):
        result = self.run_rebind()
        report_path = Path(result['report_path'])
        report = json.loads(report_path.read_bytes())
        report.pop('source_policy')
        report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True),
                               encoding='utf-8')
        graph_path = Path(result['graph_path'])
        graph = json.loads(graph_path.read_bytes())
        state = json.loads(Path(result['state_path']).read_bytes())
        graph['source_identity_map_rebinding_report_sha256'] = digest(report_path)
        graph_path.write_text(json.dumps(graph, ensure_ascii=False, sort_keys=True),
                              encoding='utf-8')
        with patch.object(chain, 'verify_alias_chain_growth_rebinding_report',
                          return_value=self.parent_receipts), \
                patch.object(chain, 'verify_reviewed_album_alias_map_delta',
                             side_effect=self.fake_delta), \
                patch.object(chain, 'load_library',
                             return_value=(self.tracks, LIBRARY, None)):
            with self.assertRaisesRegex(ValueError, 'did not inherit exact parent source policy'):
                merger._verify_rebase_receipt(
                    result['graph_path'], result['report_path'], result['state_path'],
                    LIBRARY, digest(self.map13))
        graph['source_policy'] = 'authenticated-watch-radio-only-v1'
        report['source_policy'] = None
        report_path = Path(result['report_path'])
        report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True),
                               encoding='utf-8')
        graph['source_identity_map_rebinding_report_sha256'] = digest(report_path)
        with patch.object(chain, 'load_library', return_value=(self.tracks, LIBRARY, None)), \
                patch.object(chain, 'verify_alias_chain_growth_rebinding_report',
                             return_value=self.parent_receipts), \
                patch.object(chain, 'verify_reviewed_album_alias_map_delta',
                             side_effect=self.fake_delta):
            with self.assertRaises(ValueError):
                chain.verify_album_alias_chain_growth_rebinding_report(
                    graph_doc=graph, report=report, state=state, library_hash=LIBRARY,
                    identity_hash=digest(self.map13), state_path=Path(result['state_path']))


if __name__ == '__main__':
    unittest.main()
