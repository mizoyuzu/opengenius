import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import audit_work_video_radio as audit
import audit_work_video_prefix_candidates as prefix


PROFILE = ROOT / (
    'data/ytmusic/post870-kirby-playlist-stage2-inputs-20261015-v01/'
    'root-reviewed-capture-profile.json')
PROFILE_SHA = '115182969c289ca4eb1a20fed2190ac9abe8848b35116456434d37e91951a594'
STAGE = ROOT / (
    'data/ytmusic/post870-work-video-radio-20261015-v01/stage-0002-kirby-v01')


class WorkVideoPrefixCandidateTests(unittest.TestCase):
    def test_actual_five_pair_prefix_and_partial_radio_are_replayed_without_credit(self):
        receipt, projection = prefix.replay_prefix(PROFILE, PROFILE_SHA)
        self.assertEqual(receipt['status'], 'interrupted_prefix_validated_candidate_only')
        self.assertEqual(receipt['validated_complete_pairs'], 5)
        self.assertEqual(receipt['validated_candidate_graphs'], 5)
        self.assertEqual(receipt['http_requests'], 26)
        self.assertEqual(receipt['cumulative_http'], 5156)
        self.assertTrue(receipt['partial_radio_in_global_ambiguity_registry'])
        self.assertEqual(receipt['partial_pair_root_credit'], 0)
        self.assertEqual(projection['candidate_graph_count'], 5)
        self.assertFalse(projection['recording_identity_verified'])
        for key in ('root_credit', 'edge_credit', 'source_credit'):
            self.assertEqual(receipt[key], 0)
            self.assertEqual(projection[key], 0)

    def test_saved_own_request_binds_exact_video_and_cache_hash(self):
        process = json.loads((STAGE / 'process.json').read_text())
        listed = prefix._manifest(STAGE, process)
        result = json.loads((STAGE / 'progress.json').read_text())['results_so_far'][0]
        row = result['watch_radio']
        accepted = audit._verify_request(
            STAGE, row['request_key'], row['receipt_sha256'],
            result['seed_video_id'], True, 100, listed)
        self.assertEqual(accepted['arguments']['videoId'], result['seed_video_id'])
        with self.assertRaisesRegex(audit.AuditError, 'request arguments do not bind'):
            audit._verify_request(
                STAGE, row['request_key'], row['receipt_sha256'],
                'AAAAAAAAAAA', True, 100, listed)

    def test_mutated_raw_cache_fails_manifest_replay(self):
        process = json.loads((STAGE / 'process.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            copied_stage = Path(tmp) / 'stage'
            shutil.copytree(STAGE / 'requests', copied_stage / 'requests')
            first = next((copied_stage / 'requests').glob('*.json'))
            value = json.loads(first.read_text())
            value['observed_at'] = 'tampered'
            first.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaisesRegex(prefix.PrefixAuditError, 'saved request bytes differ'):
                prefix._manifest(copied_stage, process)


if __name__ == '__main__':
    unittest.main()
