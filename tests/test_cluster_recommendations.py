import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cluster_recommendations import evaluate_clusters
from genius_format import pack_config, parse_similarities
from music_identity_map import IdentityMap, build_map


class WalkingCore:
    def __init__(self, executable, config, metadata, similarities):
        self.similarities, self.steps = similarities, 0

    def generate(self, root, limit):
        self.steps += 1
        pending, result = [root], []
        while pending and len(result) < limit:
            gid = pending.pop(0)
            if gid in result:
                continue
            result.append(gid)
            pending.extend(parse_similarities(self.similarities[gid])[1])
        return {'result_genius_ids': [f'{gid:016X}' for gid in result]}


class ClusterSelectionTests(unittest.TestCase):
    def setUp(self):
        self.pids = [f'{i:016X}' for i in range(1, 5)]
        tracks = [dict(persistent_id=pid, title=str(i), artist='Artist', album='Album')
                  for i, pid in enumerate(self.pids)]
        self.matcher = IdentityMap(build_map(tracks, 'hash'), tracks, 'hash')
        self.labels = {pid: dict(tags=['Family'] if i >= 2 else [], kind='off_vocal' if i == 3 else 'bgm')
                       for i, pid in enumerate(self.pids)}
        self.graphs = [self.graph(0, [1, 2, 3]), self.graph(1, [2])]
        self.config = pack_config(dict(version=2, filters=[dict(type=1, parameters=[20,50,10,10])],
                                       flags=0, result_words=[10,20]))

    def graph(self, root, targets):
        return dict(root_pid=self.pids[root], ordered_target_pids=[self.pids[i] for i in targets],
                    observations=[], candidate_groups=[])

    def evaluate(self, mode='prefer', **options):
        return evaluate_clusters(self.graphs, self.matcher, self.config, 'unused', self.labels,
                                  tags=['Family'], mode=mode, root_pids=[self.pids[0]],
                                  excluded_kinds=['off_vocal'], core_factory=WalkingCore, **options)

    def test_outside_seed_preferred_first_fill_and_exclusion_are_distinct(self):
        before = copy.deepcopy(self.graphs)
        result, scope = self.evaluate()
        row = result['root_results'][0]
        self.assertEqual(row['playlist_pids'], [self.pids[0], self.pids[2], self.pids[1]])
        self.assertEqual((row['preferred_nonroot_count'], row['fallback_nonroot_count']), (1, 1))
        self.assertIsNone(row['core_result'])
        self.assertEqual(scope['excluded_kinds'], ['off_vocal'])
        self.assertEqual(self.graphs, before)
        with self.assertRaises(ValueError):
            self.evaluate(mode='only')

    def test_preferred_track_via_outside_bridge_keeps_actual_observed_path(self):
        self.graphs = [self.graph(0, [1]), self.graph(1, [2])]
        result, _ = self.evaluate()
        row = result['root_results'][0]
        self.assertEqual(row['playlist_pids'], [self.pids[0], self.pids[2], self.pids[1]])
        self.assertEqual(row['playlist'][1]['observed_relation_path'], self.pids[:3])
        self.assertEqual(row['playlist'][1]['observed_relation_hops'], 2)
        self.assertEqual(row['indirect_candidate_count'], 1)

    def test_limit_seed_dedup_and_full_graph_provenance(self):
        result, _ = self.evaluate(limit=2)
        row = result['root_results'][0]
        self.assertEqual(row['playlist_pids'], [self.pids[0], self.pids[2]])
        self.assertEqual(result['relation_rows'][0]['ordered_target_pids'], self.pids[1:3])
        self.assertEqual(result['root_overlaps'], [])

    def test_affinity_preference_is_forwarded_as_a_separate_selection_control(self):
        result, _ = self.evaluate(affinity_preference='moderate')
        self.assertEqual(result['affinity_preference'], 'moderate')
        self.assertEqual(result['root_results'][0]['affinity_preference'], 'moderate')
        self.assertEqual(result['root_results'][0]['application_affinity']['preference'], 'moderate')

    def test_affinity_composition_keeps_preferred_scope_ahead_of_closer_fallback(self):
        by_pid = {track['persistent_id']: track for track in self.matcher.tracks}
        by_pid[self.pids[0]].update(artist='Seed Artist', album='Seed Album')
        by_pid[self.pids[1]].update(artist='Seed Artist', album='Seed Album')
        by_pid[self.pids[2]].update(artist='Different Artist', album='Different Album')
        result, _ = self.evaluate(affinity_preference='moderate')
        row = result['root_results'][0]
        self.assertEqual(row['playlist_pids'], [self.pids[0], self.pids[2], self.pids[1]])
        self.assertTrue(row['post_composition_affinity']['preferred_before_fallback_preserved'])
        self.assertEqual(row['post_composition_affinity']['policy'],
                         'bounded_affinity_with_preferred_scope_before_fallback')

    def test_bounded_fallback_counts_nonroot_only_and_restricts_family(self):
        result, _ = self.evaluate(fallback_limit=0)
        self.assertEqual(result['root_results'][0]['playlist_pids'], [self.pids[0], self.pids[2]])
        result, _ = self.evaluate(fallback_limit=1)
        self.assertEqual(result['root_results'][0]['playlist_pids'], [self.pids[0], self.pids[2], self.pids[1]])
        self.labels[self.pids[1]]['tags'] = ['Other game']
        result, _ = self.evaluate(fallback_tags=['Family'], fallback_limit=1)
        self.assertEqual(result['root_results'][0]['playlist_pids'], [self.pids[0], self.pids[2]])
        result, _ = self.evaluate(fallback_tags=['Other game'], fallback_limit=1)
        row = result['root_results'][0]
        self.assertEqual(row['fallback_nonroot_count'], 1)
        self.assertEqual(row['selection_policy'], 'user_cluster_preferred_bounded_fallback')

    def test_invalid_fallback_constraints_are_rejected(self):
        for value in (True, -1, 26, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.evaluate(fallback_limit=value)
        with self.assertRaises(ValueError):
            self.evaluate(mode='only', fallback_limit=0)


if __name__ == '__main__':
    unittest.main()
