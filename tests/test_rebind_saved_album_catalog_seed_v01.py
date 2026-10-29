import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rebind_saved_album_catalog_seed_v01 as bridge


class SavedAlbumCatalogSeedBridgeTests(unittest.TestCase):
    def test_standard_typed_producer_and_selector_accept_exact_fresh_pairs(self):
        with tempfile.TemporaryDirectory(prefix='sfp-standard-seed-v01-') as temp:
            output = Path(temp) / 'seed-evidence'
            report = bridge.build(output)
            self.assertEqual(report['status'], 'independent_standard_consumer_pass')
            self.assertEqual(report['candidate_seed_count'], 14)
            self.assertTrue(report['all_candidates_fresh_unattempted_nonroot'])
            self.assertEqual(report['recording_identity_verified'], 0)
            self.assertEqual(report['authenticated_watch_radio_proofs'], 0)
            self.assertEqual(report['outgoing_roots_created'], 0)
            self.assertEqual(report['recommendation_edges_created'], 0)
            self.assertEqual(report['network_requests'], 0)
            combined = output / 'combined-seed-video-evidence.json'
            self.assertTrue(combined.is_file())
            self.assertEqual(report['source_receipts']['combined_seed_evidence_sha256'],
                             bridge._sha(combined.read_bytes()))

    def test_changed_saved_album_response_pin_fails_before_writing(self):
        with tempfile.TemporaryDirectory(prefix='sfp-standard-seed-negative-') as temp:
            output = Path(temp) / 'seed-evidence'
            pins = copy.copy(bridge.PINS)
            pins[bridge.ALBUM_RESPONSE] = '0' * 64
            with patch.object(bridge, 'PINS', pins):
                with self.assertRaisesRegex(ValueError, 'pinned source changed'):
                    bridge.build(output)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
