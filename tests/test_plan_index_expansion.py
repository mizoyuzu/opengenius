import copy
import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from genius_format import pack_similarities
from plan_genius_update import PRODUCER, _hash
from plan_index_expansion import plan_index_expansion
from probe_ytmusic_batch import select_planned_seeds


class PlanIndexExpansionTests(unittest.TestCase):
    def setUp(self):
        self.library_hash = 'a' * 64
        self.tracks = [
            {'persistent_id': f'{i:016X}', 'title': f'Track {i}', 'artist': artist,
             'album': f'Album {i}', 'duration_ms': 120000}
            for i, artist in enumerate(
                ['Root Artist', 'Target Artist', 'Target Artist', 'Quiet', 'New Artist', 'Other Artist'], 1)
        ]
        self.pids = [track['persistent_id'] for track in self.tracks]
        self.graph = {self.pids[0]: [self.pids[1], self.pids[2]],
                      self.pids[3]: []}
        self.state = self.make_state(self.graph, self.pids[:4])
        self.relations = self.make_relations(self.graph, self.state)

    def make_state(self, graph, indexed_pids, tracks=None):
        gids = {pid: i + 1 for i, pid in enumerate(indexed_pids)}
        rows = []
        for pid in indexed_pids:
            targets = graph.get(pid, [])
            rows.append({'persistent_id': pid, 'genius_id': gids[pid],
                         'metadata_hex': bytes(32).hex(),
                         'similarities_hex': pack_similarities(0, [gids[target] for target in targets]).hex(),
                         'equivalence_labels': []})
        state = {'schema_version': 1, 'producer': PRODUCER,
                 'source': {'library_sha256': self.library_hash, 'tracks_sha256': _hash(tracks if tracks is not None else self.tracks)}, 'rows': rows}
        state['state_sha256'] = _hash(state)
        return state

    def make_relations(self, graph, state):
        return {'schema_version': 1, 'source_library_sha256': self.library_hash,
                'source_state_sha256': state['state_sha256'],
                'relation_rows': [{'root_pid': pid, 'ordered_target_pids': targets}
                                  for pid, targets in graph.items()]}

    def test_indexed_target_only_roots_precede_unindexed_with_artist_album_round_robin(self):
        untouched = copy.deepcopy(self.tracks)
        plan, audit = plan_index_expansion(self.tracks, self.library_hash,
                                           self.state, self.relations, 3)
        selected = [seed['persistent_id'] for seed in plan['seeds']]
        self.assertEqual(selected[:2], self.pids[1:3])
        self.assertIn(selected[2], self.pids[4:6])
        self.assertEqual([seed['sample_kind'] for seed in plan['seeds']],
                         ['indexed_target_only_no_outgoing_relation'] * 2 + ['unindexed_track'])
        self.assertEqual(self.tracks, untouched)
        self.assertEqual(len(select_planned_seeds(plan, self.tracks, self.library_hash, 3)), 3)
        self.assertEqual(audit['selected_indexed_target_only_count'], 2)
        self.assertEqual(audit['selected_unindexed_track_count'], 1)
        self.assertEqual(audit['selected'][0]['current_incoming_relation_count'], 1)

    def test_no_observed_roots_uses_unindexed_library_candidates(self):
        graph = {pid: [] for pid in self.pids[:2]}
        state = self.make_state(graph, self.pids[:2])
        relations = self.make_relations(graph, state)
        plan, audit = plan_index_expansion(self.tracks, self.library_hash, state, relations, 2)
        self.assertEqual(len(plan['seeds']), 2)
        self.assertTrue(set(seed['persistent_id'] for seed in plan['seeds']) <= set(self.pids[3:6]))
        self.assertEqual(audit['current_directed_relation_count'], 0)
        self.assertEqual(audit['selected_indexed_target_only_count'], 0)

    def test_default_version_marker_exclusion_and_metadata_requirements(self):
        tracks = copy.deepcopy(self.tracks)
        tracks[4]['title'] = 'Track 5（Instrumental 【-Bass】）'
        tracks[5]['title'] = 'Track 6 Karaoke'
        tracks[3]['album'] = ''
        graph = {self.pids[0]: [self.pids[1], self.pids[2]]}
        state = self.make_state(graph, self.pids[:3], tracks=tracks)
        relations = self.make_relations(graph, state)
        plan, audit = plan_index_expansion(tracks, self.library_hash, state, relations, 1)
        self.assertEqual(plan['seeds'][0]['persistent_id'], self.pids[1])
        self.assertEqual(audit['excluded_counts'],
                         {'excluded_version_marker': 2, 'missing_search_metadata': 1})

    def test_previously_attempted_pids_are_skipped_and_audited(self):
        plan, audit = plan_index_expansion(
            self.tracks, self.library_hash, self.state, self.relations, 2,
            excluded_pids=[self.pids[1], self.pids[4]])
        selected = [seed['persistent_id'] for seed in plan['seeds']]
        self.assertNotIn(self.pids[1], selected)
        self.assertNotIn(self.pids[4], selected)
        self.assertEqual(audit['excluded_counts']['previously_attempted_pid'], 2)
        self.assertEqual(audit['excluded_pids'], sorted([self.pids[1], self.pids[4]]))

    def test_unindexed_quota_selects_new_frontiers_and_is_deterministic(self):
        first, audit = plan_index_expansion(
            self.tracks, self.library_hash, self.state, self.relations, 3,
            unindexed_count=1)
        second, _ = plan_index_expansion(
            self.tracks, self.library_hash, self.state, self.relations, 3,
            unindexed_count=1)
        self.assertEqual(first, second)
        self.assertEqual(audit['requested_unindexed_seed_count'], 1)
        self.assertEqual(audit['selected_unindexed_track_count'], 1)
        self.assertEqual(audit['selected_indexed_target_only_count'], 2)
        reasons = [seed['sample_kind'] for seed in first['seeds']]
        self.assertEqual(reasons.count('unindexed_track'), 1)
        self.assertEqual(reasons.count('indexed_target_only_no_outgoing_relation'), 2)

    def test_unavailable_unindexed_quota_is_filled_by_incoming_targets(self):
        tracks = copy.deepcopy(self.tracks)
        tracks[5]['duration_ms'] = 0
        state = self.make_state(self.graph, self.pids[:4], tracks=tracks)
        relations = self.make_relations(self.graph, state)
        plan, audit = plan_index_expansion(
            tracks, self.library_hash, state, relations, 3, unindexed_count=2)
        self.assertEqual(audit['requested_unindexed_seed_count'], 2)
        self.assertEqual(audit['selected_unindexed_track_count'], 1)
        self.assertEqual(audit['selected_indexed_target_only_count'], 2)
        self.assertIn(self.pids[1], [seed['persistent_id'] for seed in plan['seeds']])
        self.assertIn(self.pids[2], [seed['persistent_id'] for seed in plan['seeds']])

    def test_artist_with_fewer_observed_roots_is_preferred_over_alphabetical_order(self):
        tracks = copy.deepcopy(self.tracks)
        tracks[0]['artist'] = 'A Artist'
        tracks[1]['artist'] = 'A Artist'
        tracks[2]['artist'] = 'Z Artist'
        tracks[3]['artist'] = 'A Artist'
        graph = {self.pids[0]: [self.pids[1], self.pids[2]], self.pids[3]: []}
        state = self.make_state(graph, self.pids[:4], tracks=tracks)
        relations = self.make_relations(graph, state)
        plan, _ = plan_index_expansion(tracks, self.library_hash, state, relations, 1)
        # A Artist sorts first, but already has two observed roots in this fixture.
        self.assertEqual(plan['seeds'][0]['persistent_id'], self.pids[2])

    def test_unknown_or_duplicate_excluded_pids_are_rejected(self):
        for excluded in ([self.pids[1], self.pids[1]], ['FFFFFFFFFFFFFFFF'], 'not-a-list'):
            with self.subTest(excluded=excluded), self.assertRaises(ValueError):
                plan_index_expansion(self.tracks, self.library_hash, self.state,
                                     self.relations, 1, excluded_pids=excluded)

    def test_library_hash_state_hash_and_graph_binding_are_enforced(self):
        with self.assertRaises(ValueError):
            plan_index_expansion(self.tracks, 'b' * 64, self.state, self.relations, 1)
        foreign = copy.deepcopy(self.relations)
        foreign['source_state_sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            plan_index_expansion(self.tracks, self.library_hash, self.state, foreign, 1)
        tampered = copy.deepcopy(self.state)
        tampered['rows'][0]['genius_id'] += 100
        with self.assertRaises(ValueError):
            plan_index_expansion(self.tracks, self.library_hash, tampered, self.relations, 1)
        mismatch = copy.deepcopy(self.relations)
        mismatch['relation_rows'][0]['ordered_target_pids'] = [self.pids[3]]
        with self.assertRaises(ValueError):
            plan_index_expansion(self.tracks, self.library_hash, self.state, mismatch, 1)

    def test_changed_snapshot_metadata_is_rejected_even_with_same_library_hash(self):
        tracks = copy.deepcopy(self.tracks)
        tracks[0]['title'] = 'Changed title'
        with self.assertRaisesRegex(ValueError, 'track metadata differs'):
            plan_index_expansion(tracks, self.library_hash, self.state, self.relations, 1)

    def test_unknown_state_pid_and_invalid_counts_are_rejected(self):
        state = self.make_state(self.graph, self.pids[:4])
        state['rows'][0]['persistent_id'] = 'FFFFFFFFFFFFFFFF'
        state['state_sha256'] = _hash({k: v for k, v in state.items() if k != 'state_sha256'})
        with self.assertRaises(ValueError):
            plan_index_expansion(self.tracks, self.library_hash, state,
                                 self.make_relations(self.graph, state), 1)
        for count in (0, 21, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                plan_index_expansion(self.tracks, self.library_hash,
                                     self.state, self.relations, count)
        for unindexed_count in (-1, 3, True):
            with self.subTest(unindexed_count=unindexed_count), self.assertRaises(ValueError):
                plan_index_expansion(self.tracks, self.library_hash, self.state,
                                     self.relations, 2, unindexed_count=unindexed_count)

    def test_audit_describes_synthetic_evidence_without_db_trust_claim(self):
        _, audit = plan_index_expansion(self.tracks, self.library_hash,
                                        self.state, self.relations, 1)
        self.assertIn('synthetic state', audit['evidence_limit'])
        self.assertNotIn('database_trusted', json.dumps(audit))
        self.assertEqual(audit['network_requests'], 0)


if __name__ == '__main__':
    unittest.main()
