import json
import copy
import unittest

from scripts import preflight_genius_package_v18 as gate


class FinalPackagePreflightV18Tests(unittest.TestCase):
    def test_original_library_native_baseline_and_latest_map_are_pinned(self):
        report = gate.check_native()
        self.assertEqual(report['library_track_count'], 3004)
        self.assertEqual(report['baseline_assignments'], 515)
        self.assertEqual(report['baseline_ordered_edges'], 3341)
        self.assertEqual(report['identity_map_sha256'], gate.PINNED['map'])
        self.assertEqual(gate.sha(gate.MAP), gate.PINNED['map'])

    def test_package_origin_recompute_accepts_current782_checkpoint(self):
        graph = json.loads((gate.ACCEPTED_PARENT_DIR / 'batch-0001/growth/accepted-graphs.json').read_text())
        active = {row['root_pid'] for row in graph['accepted_roots'] if row['ordered_target_pids']}
        result = gate._recompute_source_origin(graph, active)
        self.assertEqual(result['counts'], {
            'native_only': 159, 'ytmusic_source_backed': 623,
            'native_ytmusic_overlap': 0, 'unknown': 0, 'total_active': 782,
        })
        self.assertEqual(len(result['inventory_receipts']) >= 600, True)

    def test_unknown_root_cannot_be_hidden_by_empty_assignment(self):
        graph = json.loads((gate.ACCEPTED_PARENT_DIR / 'batch-0001/growth/accepted-graphs.json').read_text())
        active = {row['root_pid'] for row in graph['accepted_roots'] if row['ordered_target_pids']}
        graph['accepted_roots'].append({'root_pid': 'FFFFFFFFFFFFFFFF', 'ordered_target_pids': ['ABCDEF0123456789']})
        with self.assertRaisesRegex(ValueError, 'without verified Native or YTMusic origin'):
            gate._recompute_source_origin(graph, active | {'FFFFFFFFFFFFFFFF'})

    def test_snapshot_credit_requires_nested_authenticated_radio_proof(self):
        graph = json.loads((gate.ACCEPTED_PARENT_DIR / 'batch-0001/growth/accepted-graphs.json').read_text())
        active = {row['root_pid'] for row in graph['accepted_roots'] if row['ordered_target_pids']}
        broken = copy.deepcopy(graph)
        broken['growth_runs'][2]['snapshot_inputs'][0]['radio_only_source_proof']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'radio source proof changed'):
            gate._recompute_source_origin(broken, active)

    def test_no_source_snapshot_receipts_means_no_incremental_root_credit(self):
        graph = json.loads((gate.ACCEPTED_PARENT_DIR / 'batch-0001/growth/accepted-graphs.json').read_text())
        active = {row['root_pid'] for row in graph['accepted_roots'] if row['ordered_target_pids']}
        broken = copy.deepcopy(graph)
        broken['growth_runs'][2]['snapshot_inputs'] = []
        with self.assertRaisesRegex(ValueError, 'lacks snapshot receipts'):
            gate._recompute_source_origin(broken, active)


if __name__ == '__main__':
    unittest.main()
