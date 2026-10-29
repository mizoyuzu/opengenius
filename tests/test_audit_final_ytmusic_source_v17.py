import json
import unittest
from pathlib import Path

from scripts import audit_final_ytmusic_source_v17 as audit


class Accepted782LineageTests(unittest.TestCase):
    def test_real_parent_lineage_uses_variable_native_plus_yt_origin_sets(self):
        result = audit.verify_accepted_782_parent()
        self.assertEqual(result['base_total_genuine_roots'], 782)
        self.assertEqual(result['native_only_roots'], 159)
        self.assertEqual(result['ytmusic_source_roots'], 623)
        self.assertEqual(result['native_ytmusic_overlap'], 0)
        self.assertEqual(result['unknown_roots'], 0)
        self.assertEqual(result['ordered_edges'], 18271)
        self.assertEqual(result['remaining_genuine_roots'], 218)

    def test_real_title_child_projection_counts_empty_state_assignments_separately(self):
        graph = json.loads(audit.TITLE_GRAPH.read_text(encoding='utf-8'))
        state = json.loads(audit.TITLE_STATE.read_text(encoding='utf-8'))
        projection = audit._validate_title_child_projection(graph, state)
        self.assertEqual(projection, {
            'graph_rows': 943,
            'state_rows': 972,
            'active_roots': 782,
            'state_only_empty_rows': 29,
        })

    def test_active_root_counter_ignores_empty_assigned_rows(self):
        vectors = {'A000000000000001': ['B000000000000001'],
                   'A000000000000002': [],
                   'A000000000000003': []}
        self.assertEqual(audit._active_root_pids(vectors), {'A000000000000001'})

    def test_partition_accepts_overlap_once_and_rejects_unknowns(self):
        counts = audit._source_partition({'A', 'B'}, {'B', 'C'}, {'A', 'B', 'C'})
        self.assertEqual(counts, {
            'native_only_roots': 1,
            'ytmusic_source_roots': 2,
            'native_ytmusic_overlap': 1,
            'unknown_roots': 0,
            'total_active_roots': 3,
        })
        with self.assertRaisesRegex(ValueError, 'lacks a verified'):
            audit._source_partition({'A'}, {'B'}, {'A', 'B', 'C'})

    def test_partition_rejects_origin_pid_not_active(self):
        with self.assertRaisesRegex(ValueError, 'includes inactive roots'):
            audit._source_partition({'A'}, set(), set())

    def test_changed_or_missing_graph_vector_rejected_by_projection_contract(self):
        helper_path = audit.BASELINE_RUNTIME / 'scripts/baseline_graph_projection_v01.py'
        helper_spec = __import__('importlib.util', fromlist=['spec_from_file_location']) \
            .spec_from_file_location('_projection_tamper_test', helper_path)
        helper = __import__('importlib.util', fromlist=['module_from_spec']).module_from_spec(helper_spec)
        helper_spec.loader.exec_module(helper)
        graph = {'A': ['B'], 'C': []}
        state = {'A': ['B'], 'B': [], 'C': [], 'D': []}
        self.assertEqual(helper.validate_projection(graph, state), 4)
        for tampered in (
                {'A': ['C'], 'C': []},
                {'C': []},
                {'A': ['B'], 'C': [], 'D': ['A']}):
            with self.subTest(tampered=tampered), self.assertRaises(ValueError):
                helper.validate_projection(tampered, state)


if __name__ == '__main__':
    unittest.main()
