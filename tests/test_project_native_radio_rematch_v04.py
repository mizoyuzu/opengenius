"""Guards for the v04-pinned candidate adapter and actual native verifier result."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import project_native_radio_rematch_v04 as projection


class NativeRadioRematchV04Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-radio-rematch-v04-test-')
        self.output = Path(self.temp.name) / 'candidate'

    def tearDown(self):
        self.temp.cleanup()

    def test_approved_v04_baseline_is_actually_reverified_and_pinned(self):
        result = projection.create_projection_candidate(self.output)
        self.assertEqual(projection.NATIVE_BASELINE, Path('/tmp/opengenius-final-campaign-native-v13-child-v04'))
        proof = json.loads((self.output / 'projection-proof.json').read_bytes())
        accepted = json.loads((self.output / 'accepted-graphs.json').read_bytes())
        provenance = accepted['baseline_rebase_provenance']
        self.assertEqual(result['status'], 'native_projection_candidate_static_consumer_pass')
        self.assertEqual(result['nonempty_root_count'], 767)
        self.assertEqual(result['ordered_edge_count'], 18021)
        self.assertEqual(result['native_assignment_count'], 515)
        self.assertEqual(result['projected_assignment_count'], 957)
        self.assertEqual(result['source_only_assignment_count'], 442)
        self.assertEqual(provenance['graph_export_sha256'], '8d14cf54cc75acb17b7fc15757691b2c7b4528d539dfdf86960475d6068207e3')
        self.assertEqual(provenance['rebase_report_sha256'], 'bdfc0d0789119e58543ff1d2691920e96e7a7ff98b6c00fcfbc06997254578dc')
        self.assertEqual(provenance['state_file_sha256'], '0ff818736f942e4e849487dd486fdf7a4b39c993c32369a889c60f6e3e383b95')
        self.assertGreater(len(provenance['proof_input_receipts']), 0)
        self.assertEqual(proof['status'], 'candidate_native_projection_not_growth_acceptance')
        self.assertEqual(result['http_requests_added'], 0)
        self.assertEqual(result['attempted_pid_delta'], 0)

    def test_static_consumer_rejects_modified_ordered_edge(self):
        projection.create_projection_candidate(self.output)
        path = self.output / 'accepted-graphs.json'
        graph = json.loads(path.read_bytes())
        graph['accepted_roots'][0]['ordered_target_pids'].reverse()
        path.write_text(json.dumps(graph))
        with self.assertRaisesRegex(ValueError, 'trusted recomputation'):
            projection._verify_projection_candidate(self.output)


if __name__ == '__main__':
    unittest.main()
