"""Source-chain checks for the offline v04→v11 metadata rebind wrapper."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from rebind_radio_growth_alias_chain import (
    _load_saved_parent_growth,
    staged_v04_to_v11_transitions,
    staged_v11_to_v12_transition,
    verify_v04_v11_identity_map_alias_chain,
)
from rebind_radio_growth_identity import verify_reviewed_album_alias_map_delta


class ReviewedAliasChainTests(unittest.TestCase):
    def setUp(self):
        self.maps = ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source'
        self.snapshot = self.maps / 'track-snapshot.json'
        self.old_map = self.maps / 'identity-map-aliases-v04.json'
        self.new_map = self.maps / 'identity-map-aliases-v11-nine-credit-thirty-title-staged.json'
        self.transitions = staged_v04_to_v11_transitions(ROOT)

    def test_replays_six_reviewed_deltas_without_recording_edges(self):
        proof = verify_v04_v11_identity_map_alias_chain(
            track_snapshot=self.snapshot,
            input_identity_map_path=self.old_map,
            output_identity_map_path=self.new_map,
            transitions=self.transitions)
        self.assertEqual(proof['transition_count'], 6)
        self.assertEqual(proof['input_identity_map']['sha256'],
                         '78cd35548bffe31b3f92a4953f9f81ffcd82a6540df2c1f2fc8d61b16142d64b')
        self.assertEqual(proof['output_identity_map']['sha256'],
                         '1914cc929c7c5fe3c6ecb3a720443b9238504532d30abfcabec3d3eabe0533b5')
        self.assertIs(proof['recording_identity_verified'], False)
        self.assertEqual(proof['recording_edges_added'], 0)
        self.assertGreater(len(proof['proof_input_receipts']), 250)

    def test_missing_review_receipt_rejects(self):
        changed = deepcopy(self.transitions)
        changed[3]['receipt_path'] = str(ROOT / 'missing-title-alias-receipt.json')
        with self.assertRaises((FileNotFoundError, OSError)):
            verify_v04_v11_identity_map_alias_chain(
                track_snapshot=self.snapshot, input_identity_map_path=self.old_map,
                output_identity_map_path=self.new_map, transitions=changed)

    def test_reordered_deltas_reject(self):
        changed = deepcopy(self.transitions)
        changed[2], changed[3] = changed[3], changed[2]
        with self.assertRaises(ValueError):
            verify_v04_v11_identity_map_alias_chain(
                track_snapshot=self.snapshot, input_identity_map_path=self.old_map,
                output_identity_map_path=self.new_map, transitions=changed)

    def test_modified_final_map_rejects(self):
        original = self.new_map.read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as temporary:
            altered = Path(temporary) / 'identity-map.json'
            altered.write_text(original.replace('"schema_version": 1',
                                                '"schema_version": 2', 1),
                               encoding='utf-8')
            with self.assertRaises(ValueError):
                verify_v04_v11_identity_map_alias_chain(
                    track_snapshot=self.snapshot, input_identity_map_path=self.old_map,
                    output_identity_map_path=altered, transitions=self.transitions)

    def test_album_is_a_separate_exact_zero_edge_child_delta(self):
        child = staged_v11_to_v12_transition(ROOT)
        proof = verify_reviewed_album_alias_map_delta(
            track_snapshot=self.snapshot,
            input_identity_map_path=child['input_identity_map_path'],
            output_identity_map_path=child['output_identity_map_path'],
            proposals_path=child['proposals_path'],
            source_evidence_path=child['source_evidence_path'],
            candidate_audit_path=child['candidate_audit_path'],
            review_decisions_path=child['review_decisions_path'],
            receipt_path=child['receipt_path'])
        self.assertEqual(proof['input_identity_map']['sha256'],
                         '1914cc929c7c5fe3c6ecb3a720443b9238504532d30abfcabec3d3eabe0533b5')
        self.assertEqual(proof['output_identity_map']['sha256'],
                         '0c3fcede71684e7dd9555b7d729f0d47ba786a21f47bfc1a3470ab8ca354b334')
        self.assertIs(proof['recording_identity_verified'], False)
        self.assertEqual(proof['recording_edges_added'], 0)

    def test_frozen_parent_binds_virtual_audit_and_keeps_graph_state_shapes(self):
        parent = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261010-v05-radio-promotion-v03/growth-batch-0002-recovered-v01'
        package = _load_saved_parent_growth(
            parent, '1ea4b3a0ee26495ddb5041d2c6683ec85327dfd6aca602a089b884be22c5c5b7',
            '78cd35548bffe31b3f92a4953f9f81ffcd82a6540df2c1f2fc8d61b16142d64b')
        self.assertEqual(len(package['roots']), 691)
        self.assertEqual(len(package['state_roots']), 747)
        self.assertEqual(package['state_sha256'],
                         '46a6962a87633f73ff77478c9988617d08a330cb9f02d8ebc5378b9c67eda530')
        self.assertEqual(package['virtual_update_audit']['path'],
                         str(parent / 'virtual-update-audit.json'))
        self.assertGreater(len(package['receipts']), 200)

    def test_tampered_virtual_next_state_rejects_without_growth_replay(self):
        import json

        parent = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261010-v05-radio-promotion-v03/growth-batch-0002-recovered-v01'
        names = ('accepted-graphs.json', 'state.json', 'virtual-update-audit.json',
                 'relations.json', 'accepted-evaluation.json', 'merge-audit.json', 'inputs.json')
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary)
            for name in names:
                (copy / name).write_bytes((parent / name).read_bytes())
            audit_path = copy / 'virtual-update-audit.json'
            audit = json.loads(audit_path.read_text(encoding='utf-8'))
            audit['next_state']['rows'][0]['metadata'] = {'tampered': True}
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'virtual-update audit'):
                _load_saved_parent_growth(
                    copy, '1ea4b3a0ee26495ddb5041d2c6683ec85327dfd6aca602a089b884be22c5c5b7',
                    '78cd35548bffe31b3f92a4953f9f81ffcd82a6540df2c1f2fc8d61b16142d64b')


if __name__ == '__main__':
    unittest.main()
