import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from audit_genius_graph_topology_v01 import verify_topology
from update_genius_bundle import _hash, state_rows


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'data/ytmusic/shiny-ost-title-alias-growth-child-20261014-v01'


class CopiedGeniusGraphTopologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = json.loads((BASELINE / 'accepted-baseline-graphs.json').read_bytes())
        cls.state = json.loads((BASELINE / 'state.json').read_bytes())
        _, _, cls.relations = state_rows(cls.state)

    def test_actual_pid_vectors_match_state_sql_with_empty_extra_assignments(self):
        result = verify_topology(self.graph, self.state, self.relations,
                                 minimum_active_roots=782)
        self.assertEqual(result['active_graph_roots'], 782)
        self.assertEqual(result['ordered_edges'], 18271)
        self.assertEqual(result['graph_pid_rows'], 943)
        self.assertEqual(result['output_state_assignments'], 972)
        self.assertEqual(result['empty_state_only_assignments'], 29)

    def test_reordered_pid_edge_is_rejected_even_when_edge_set_is_same(self):
        graph = copy.deepcopy(self.graph)
        row = next(row for row in graph['accepted_roots']
                   if len(row['ordered_target_pids']) > 1)
        row['ordered_target_pids'][0], row['ordered_target_pids'][1] = (
            row['ordered_target_pids'][1], row['ordered_target_pids'][0])
        with self.assertRaisesRegex(ValueError, 'ordered relations'):
            verify_topology(graph, self.state, self.relations,
                            minimum_active_roots=782)

    def test_state_only_nonempty_and_sql_state_mismatch_are_rejected(self):
        ids, _, relations = state_rows(self.state)
        graph_pids = {row['root_pid'] for row in self.graph['accepted_roots']}
        extra_pid = next(pid for pid in ids if pid not in graph_pids)
        altered = copy.deepcopy(self.state)
        row = next(row for row in altered['rows'] if row['persistent_id'] == extra_pid)
        row['similarities_hex'] = next(
            row['similarities_hex'] for row in altered['rows']
            if row['persistent_id'] in graph_pids and row.get('similarities_hex'))
        altered['state_sha256'] = _hash({k: v for k, v in altered.items()
                                         if k != 'state_sha256'})
        _, _, altered_relations = state_rows(altered)
        with self.assertRaisesRegex(ValueError, 'ordered relations'):
            verify_topology(self.graph, altered, altered_relations,
                            minimum_active_roots=782)
        with self.assertRaisesRegex(ValueError, 'SQL relation rows'):
            verify_topology(self.graph, self.state, {}, minimum_active_roots=782)

    def test_threshold_refuses_below_target(self):
        with self.assertRaisesRegex(ValueError, 'below the minimum'):
            verify_topology(self.graph, self.state, self.relations,
                            minimum_active_roots=1000)


if __name__ == '__main__':
    unittest.main()
