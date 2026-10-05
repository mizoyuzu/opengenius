import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_identity_map import IdentityMap
from plan_ytmusic_expansion import plan_expansion
from probe_ytmusic_batch import select_planned_seeds


class ExpansionPlanTests(unittest.TestCase):
    def setUp(self):
        self.digest = 'a' * 64
        self.tracks = [{'persistent_id': f'{i:016X}', 'title': f'Track {i}',
                        'artist': artist, 'album': f'Album {i}', 'duration_ms': 120000}
                       for i, artist in enumerate(['Root', 'Popular', 'Popular', 'Alias', 'Other'], 1)]
        self.identity = IdentityMap({'schema_version': 1, 'library_sha256': self.digest,
                                     'artist_alias_groups': [{'enabled': True, 'aliases': ['Popular', 'Alias']}]},
                                    self.tracks, self.digest)
        self.root = self.tracks[0]['persistent_id']
        self.targets = [track['persistent_id'] for track in self.tracks[1:]]
        self.evaluation = {'library_sha256': self.digest,
                           'relation_rows': [{'root_pid': self.root, 'ordered_target_pids': self.targets}],
                           'root_results': [{'root_pid': self.root, 'playlist_pids': [self.root]}]}

    def test_canonical_artist_round_robin_and_batch_compatible(self):
        plan, audit = plan_expansion(self.tracks, self.digest, self.identity, self.evaluation, 3)
        self.assertEqual([row['persistent_id'] for row in plan['seeds']],
                         [self.targets[0], self.targets[3], self.targets[1]])
        self.assertEqual(len(select_planned_seeds(plan, self.tracks, self.digest, 3)), 3)
        self.assertEqual(audit['related_only_count'], 4)
        self.assertEqual(audit['selected'][0]['reachable_from_short_roots'], [self.root])
        self.assertTrue(all(set(row) == {'persistent_id', 'series', 'sample_kind'} for row in plan['seeds']))

    def test_weakest_roots_get_priority_without_reordering_edges(self):
        other_root = {'persistent_id': '0000000000000006', 'title': 'Second root',
                      'artist': 'Second root artist', 'album': 'Second album', 'duration_ms': 120000}
        self.tracks.append(other_root)
        self.evaluation['relation_rows'][0]['ordered_target_pids'] = self.targets[:3]
        self.evaluation['root_results'][0]['playlist_pids'] = [self.root, *self.targets[:3]]
        self.evaluation['relation_rows'].append({'root_pid': other_root['persistent_id'],
                                                'ordered_target_pids': [self.targets[-1]]})
        self.evaluation['root_results'].append({'root_pid': other_root['persistent_id'],
                                                'playlist_pids': [other_root['persistent_id']]})
        plan, _ = plan_expansion(self.tracks, self.digest, self.identity, self.evaluation, 1)
        self.assertEqual(plan['seeds'][0]['persistent_id'], self.targets[-1])
        self.assertEqual(self.evaluation['relation_rows'][0]['ordered_target_pids'], self.targets[:3])

    def test_explicit_kind_exclusion_keeps_unknown(self):
        kinds = {track['persistent_id']: {'kind': 'unknown', 'tags': []} for track in self.tracks}
        kinds[self.targets[0]]['kind'] = 'off_vocal'
        plan, audit = plan_expansion(self.tracks, self.digest, self.identity, self.evaluation, 10,
                                     kinds, ['off_vocal'])
        self.assertNotIn(self.targets[0], [row['persistent_id'] for row in plan['seeds']])
        self.assertEqual(audit['selected_seed_count'], 3)
        self.assertEqual(audit['excluded_counts'], {'excluded_kind': 1})

    def test_different_library_and_unclassified_exclusion_rejected(self):
        with self.assertRaises(ValueError):
            plan_expansion(self.tracks, 'b' * 64, self.identity, self.evaluation)
        with self.assertRaises(ValueError):
            plan_expansion(self.tracks, self.digest, self.identity, self.evaluation,
                           excluded_kinds=['off_vocal'])

    def test_stale_or_duplicate_graph_rejected(self):
        self.evaluation['relation_rows'][0]['ordered_target_pids'].append(self.targets[0])
        with self.assertRaises(ValueError):
            plan_expansion(self.tracks, self.digest, self.identity, self.evaluation)

    def test_already_observed_target_is_not_replanned(self):
        observed = self.targets[0]
        self.evaluation['relation_rows'].append({'root_pid': observed, 'ordered_target_pids': [self.targets[-1]]})
        self.evaluation['root_results'].append({'root_pid': observed, 'playlist_pids': [observed]})
        plan, _ = plan_expansion(self.tracks, self.digest, self.identity, self.evaluation, 10)
        self.assertNotIn(observed, [row['persistent_id'] for row in plan['seeds']])

    def test_explicit_total_playlist_target_changes_deficit_without_claiming_growth(self):
        _, audit = plan_expansion(self.tracks, self.digest, self.identity, self.evaluation,
                                  playlist_target=2)
        self.assertEqual(audit['playlist_target'], 2)
        self.assertEqual(audit['selected'][0]['reachable_short_root_deficit_sum'], 1)
        _, reached = plan_expansion(self.tracks, self.digest, self.identity, self.evaluation,
                                    playlist_target=1)
        self.assertEqual(reached['short_playlist_roots'], [])
        self.assertEqual(reached['selected'][0]['reachable_short_root_deficit_sum'], 0)
        for invalid in (0, 101, True):
            with self.subTest(target=invalid), self.assertRaises(ValueError):
                plan_expansion(self.tracks, self.digest, self.identity, self.evaluation,
                               playlist_target=invalid)

    def test_search_unresolved_history_is_explicit_deduplicated_and_not_replanned(self):
        summary = dict(schema_version=1, library_sha256=self.digest,
                       results=[dict(persistent_id=self.targets[0], status='search_unresolved'),
                                dict(persistent_id=self.targets[1], status='collected')])
        plan, audit = plan_expansion(self.tracks, self.digest, self.identity, self.evaluation,
                                     attempt_summaries=[summary, summary])
        self.assertNotIn(self.targets[0], [row['persistent_id'] for row in plan['seeds']])
        self.assertIn(self.targets[1], [row['persistent_id'] for row in plan['seeds']])
        self.assertEqual(audit['excluded_counts']['search_unresolved_attempt'], 1)
        without_history, _ = plan_expansion(self.tracks, self.digest, self.identity, self.evaluation)
        self.assertIn(self.targets[0], [row['persistent_id'] for row in without_history['seeds']])

    def test_foreign_missing_hash_and_unknown_pid_history_are_rejected(self):
        for summary in (dict(schema_version=1, results=[]),
                        dict(schema_version=1, library_sha256='b' * 64, results=[]),
                        dict(schema_version=1, library_sha256=self.digest,
                             results=[dict(persistent_id='absent', status='search_unresolved')])):
            with self.subTest(summary=summary), self.assertRaises(ValueError):
                plan_expansion(self.tracks, self.digest, self.identity, self.evaluation,
                               attempt_summaries=[summary])


if __name__ == '__main__':
    unittest.main()
