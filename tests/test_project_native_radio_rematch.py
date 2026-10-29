"""Actual-artifact guards for the fixed v16 native radio-rematch adapter."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import project_native_radio_rematch as projection


class NativeRadioRematchProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-radio-rematch-test-')
        self.output = Path(self.temp.name) / 'candidate'

    def tearDown(self):
        self.temp.cleanup()

    def test_real_accepted_child_projects_with_exact_native_prefix(self):
        result = projection.create_projection_candidate(self.output)
        self.assertEqual(result['status'], 'native_projection_candidate_static_consumer_pass')
        self.assertEqual(result['nonempty_root_count'], 767)
        self.assertEqual(result['ordered_edge_count'], 18021)
        self.assertEqual(result['native_assignment_count'], 515)
        self.assertEqual(result['projected_assignment_count'], 957)
        self.assertEqual(result['source_only_assignment_count'], 442)
        self.assertEqual(result['native_nonempty_root_prefix_count'], 159)
        self.assertEqual(result['native_ordered_edge_prefix_count'], 3341)
        self.assertEqual(result['new_from_source_parent_root_count'], 35)
        self.assertEqual(result['new_from_source_parent_edge_count'], 1045)
        self.assertEqual(result['http_requests_added'], 0)
        self.assertEqual(result['attempted_pid_delta'], 0)
        self.assertFalse(result['recording_identity_verified'])
        self.assertEqual(result['recording_edges_added'], 0)

    def test_static_consumer_rejects_modified_ordered_edge(self):
        projection.create_projection_candidate(self.output)
        path = self.output / 'accepted-graphs.json'
        graph = json.loads(path.read_bytes())
        graph['accepted_roots'][0]['ordered_target_pids'].reverse()
        path.write_text(json.dumps(graph))
        with self.assertRaisesRegex(ValueError, 'trusted recomputation'):
            projection._verify_projection_candidate(self.output)

    def test_candidate_remains_explicitly_unaccepted_growth(self):
        projection.create_projection_candidate(self.output)
        proof = json.loads((self.output / 'projection-proof.json').read_bytes())
        self.assertEqual(proof['status'], 'candidate_native_projection_not_growth_acceptance')
        self.assertEqual(proof['added_source_root_count_over_native_baseline'], 608)
        self.assertEqual(proof['added_source_edge_count_over_native_baseline'], 14680)
        self.assertFalse(proof['recording_identity_verified'])
        self.assertEqual(proof['recording_edges_added'], 0)


if __name__ == '__main__':
    unittest.main()
