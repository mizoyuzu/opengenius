"""Checks source-separated coverage arithmetic and graph integrity failures."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from report_recommendation_sources import _coverage_counts, _edge_set


def graph(root, targets):
    return {'root_pid': root, 'ordered_target_pids': targets}


class RecommendationSourceReportTests(unittest.TestCase):
    def test_source_counts_keep_baseline_and_catalog_separate_and_deduplicate_edges(self):
        radio = [graph('A', ['B', 'C']), graph('B', []), graph('D', ['A']), graph('F', [])]
        remote = [graph('A', ['B', 'E']), graph('E', ['C'])]
        local = [graph('A', ['B', 'C', 'E']), graph('C', ['D'])]
        final_edges = _edge_set(radio) | _edge_set(remote) | _edge_set(local)
        final_by_root = {}
        for root, target in sorted(final_edges):
            final_by_root.setdefault(root, []).append(target)
        final = [graph(root, targets) for root, targets in final_by_root.items()]
        report = _coverage_counts(radio, remote, local, final)
        self.assertEqual(report['baseline_recommendation_seed_roots'], 4)
        self.assertEqual(report['baseline_recommendation_roots_with_nonempty_outgoing_edges'], 2)
        self.assertEqual(report['baseline_recommendation_touched_tracks'], 4)
        self.assertEqual(report['baseline_recommendation_incoming_only_tracks'], 2)
        self.assertEqual(report['baseline_recommendation_seed_roots_without_incident_edges'], 1)
        self.assertEqual(report['directed_edges']['baseline_remote_overlap'], 1)
        self.assertEqual(report['directed_edges']['baseline_local_overlap'], 2)
        self.assertEqual(report['directed_edges']['remote_local_overlap'], 2)
        self.assertEqual(report['directed_edges']['deduplicated_union'], len(final_edges))
        self.assertEqual(report['root_source_coverage']['overlap_buckets']['all_three'], 1)

    def test_rejects_duplicate_target_within_a_source_row(self):
        with self.assertRaisesRegex(ValueError, 'duplicate target'):
            _edge_set([graph('A', ['B', 'B'])])

    def test_rejects_self_edges(self):
        with self.assertRaisesRegex(ValueError, 'self edge'):
            _edge_set([graph('A', ['A'])])

    def test_rejects_incomplete_final_union(self):
        with self.assertRaisesRegex(ValueError, 'deduplicated typed source union'):
            _coverage_counts([graph('A', ['B'])], [], [], [graph('A', [])])


if __name__ == '__main__':
    unittest.main()
