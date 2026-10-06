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


if __name__ == '__main__':
    unittest.main()
