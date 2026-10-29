import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import prepare_native_baseline_for_title_map_v01 as baseline


class NativeBaselineTitleMapTests(unittest.TestCase):
    def test_real_original_sql_matches_projected_native_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'baseline.json'
            result = baseline.build(output)
            document = json.loads(output.read_text(encoding='utf-8'))
            state_path = Path(result['projected_state'])
            state = json.loads(state_path.read_text(encoding='utf-8'))
            state_file_sha = baseline.sha(state_path)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['native_assignments'], 515)
        self.assertEqual(result['ordered_edges'], 3341)
        self.assertEqual(document['native_relations_unchanged'], True)
        self.assertEqual(document['physical_sql_matches_baseline'], True)
        self.assertEqual(document['network_requests'], 0)
        self.assertEqual(state['source']['identity_map_sha256'], baseline.PIN['new_map'])
        self.assertEqual(document['projected_state_file']['sha256'], state_file_sha)

    def test_changed_native_graph_pin_refuses_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'must-not-exist.json'
            altered = dict(baseline.PIN, native_graph='0' * 64)
            with patch.object(baseline, 'PIN', altered):
                with self.assertRaisesRegex(ValueError, 'Pinned baseline input changed: native_graph'):
                    baseline.build(output)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
