import copy
import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from rebind_radio_growth_album_append_chain import verify_reviewed_album_append_chain

CHAIN_ROOT = Path('/tmp/opengenius-album-alias-append-v03')
CHAIN_PATH = CHAIN_ROOT / 'v12-to-v13-chain-receipt.json'
SNAPSHOT = ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'

class AlbumAliasAppendChainTests(unittest.TestCase):
    def setUp(self):
        self.chain = json.loads(CHAIN_PATH.read_bytes())

    def verify(self, chain_path=CHAIN_PATH, chain=None):
        if chain is not None:
            Path(chain_path).write_text(json.dumps(chain, ensure_ascii=False), encoding='utf-8')
        return verify_reviewed_album_append_chain(
            track_snapshot=SNAPSHOT, chain_receipt_path=chain_path,
            start_map_path=self.chain['input_map_path'],
            final_map_path=self.chain['output_map_path'])

    def test_real_three_step_append_chain_replays_with_exact_prefixes(self):
        verified = self.verify()
        self.assertEqual(len(verified['transitions']), 3)
        self.assertEqual(verified['recording_edges_added'], 0)
        self.assertIs(verified['recording_identity_verified'], False)
        self.assertEqual(verified['output_identity_map']['sha256'], self.chain['output_map_sha256'])
        for step, proof in zip(self.chain['steps'], verified['transitions']):
            self.assertEqual(proof['input_identity_map']['sha256'], step['input_map_sha256'])
            self.assertEqual(proof['output_identity_map']['sha256'], step['output_map_sha256'])
            before = json.loads(Path(step['input_map_path']).read_bytes())
            after = json.loads(Path(step['output_map_path']).read_bytes())
            self.assertEqual(after['album_title_aliases'][:-1], before['album_title_aliases'])
            self.assertEqual({k:v for k,v in after.items() if k!='album_title_aliases'},
                             {k:v for k,v in before.items() if k!='album_title_aliases'})

    def test_reordered_steps_fail(self):
        chain = copy.deepcopy(self.chain)
        chain['steps'][0], chain['steps'][1] = chain['steps'][1], chain['steps'][0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'chain.json'
            with self.assertRaisesRegex(ValueError, 'steps are skipped, reordered'):
                self.verify(path, chain)

    def test_candidate_yield_tamper_fails(self):
        chain = copy.deepcopy(self.chain)
        chain['steps'][0]['candidate_yield']['distinct_pids'] += 1
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'chain.json'
            with self.assertRaisesRegex(ValueError, 'exact one-alias prefix extension'):
                self.verify(path, chain)

    def test_intermediate_output_hash_tamper_fails(self):
        chain = copy.deepcopy(self.chain)
        chain['steps'][0]['output_map_sha256'] = '0' * 64
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'chain.json'
            with self.assertRaisesRegex(ValueError, 'intermediate map changed'):
                self.verify(path, chain)

    def test_skipped_intermediate_fails(self):
        chain = copy.deepcopy(self.chain)
        chain['steps'] = [chain['steps'][0], chain['steps'][2]]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'chain.json'
            with self.assertRaisesRegex(ValueError, 'steps are skipped, reordered'):
                self.verify(path, chain)

if __name__ == '__main__':
    unittest.main()
