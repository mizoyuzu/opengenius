"""Guards the opt-in actual-radio-edge MusicCore ordering experiment."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_native_affinity import affinity_order_graphs
from music_identity_map import IdentityMap, build_map


class AffinityEdgeOrderTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [
            {'persistent_id': 'ROOT', 'title': 'Seed', 'artist': 'A', 'album': 'Seed Album'},
            {'persistent_id': 'OUTSIDE', 'title': 'Other', 'artist': 'B', 'album': 'Other Album'},
            {'persistent_id': 'CLOSE', 'title': 'Close', 'artist': 'A', 'album': 'Seed Album'},
            {'persistent_id': 'OTHER_ROOT', 'title': 'Other root', 'artist': 'B', 'album': 'Other Album'},
        ]
        self.matcher = IdentityMap(build_map(self.tracks, 'hash'), self.tracks, 'hash')

    def test_reorders_only_selected_actual_targets_with_bounded_movement(self):
        graphs = [
            {'root_pid': 'ROOT', 'ordered_target_pids': ['OUTSIDE', 'CLOSE'],
             'source_relation': 'radio', 'observations': [{'position': 1}]},
            {'root_pid': 'OTHER_ROOT', 'ordered_target_pids': ['OUTSIDE'],
             'source_relation': 'radio', 'observations': [{'position': 1}]},
        ]
        before = copy.deepcopy(graphs)
        reordered, provenance = affinity_order_graphs(
            graphs, self.tracks, self.matcher, ['ROOT'], 'slight')
        self.assertEqual(graphs, before)
        self.assertEqual(reordered[0]['ordered_target_pids'], ['CLOSE', 'OUTSIDE'])
        self.assertEqual(reordered[0]['observations'], before[0]['observations'])
        self.assertEqual(reordered[0]['source_relation'], 'radio')
        self.assertEqual(reordered[1], before[1])
        self.assertEqual(provenance['ROOT']['original_target_order'], ['OUTSIDE', 'CLOSE'])
        self.assertEqual(provenance['ROOT']['affinity_target_order'], ['CLOSE', 'OUTSIDE'])
        self.assertTrue(provenance['ROOT']['candidate_pid_set_unchanged'])
        self.assertEqual(set(reordered[0]['ordered_target_pids']),
                         set(before[0]['ordered_target_pids']))

    def test_requires_observed_unique_pid_edges_and_known_roots(self):
        graphs = [{'root_pid': 'ROOT', 'ordered_target_pids': ['OUTSIDE', 'OUTSIDE']}]
        with self.assertRaisesRegex(ValueError, 'unique'):
            affinity_order_graphs(graphs, self.tracks, self.matcher, ['ROOT'])
        graphs = [{'root_pid': 'ROOT', 'ordered_target_pids': ['OUTSIDE']}]
        with self.assertRaisesRegex(ValueError, 'not present'):
            affinity_order_graphs(graphs, self.tracks, self.matcher, ['UNKNOWN'])
        with self.assertRaisesRegex(ValueError, 'slight or moderate'):
            affinity_order_graphs(graphs, self.tracks, self.matcher, ['ROOT'], 'neutral')


if __name__ == '__main__':
    unittest.main()
