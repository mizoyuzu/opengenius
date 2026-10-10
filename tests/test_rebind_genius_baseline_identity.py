"""Regression tests for typed Genius baseline map rebinding."""
import struct
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from genius_format import pack_similarities
from plan_genius_update import _hash
from rebind_genius_baseline_identity import (
    _assert_sql_state_rows_unchanged, _reseal_map_hash,
)


class BaselineIdentityRebindTests(unittest.TestCase):
    def setUp(self):
        unsigned = {
            'schema_version': 1,
            'producer': 'opengenius-synthetic-rows-v1',
            'source': {
                'library_sha256': 'a' * 64,
                'identity_map_sha256': 'b' * 64,
                'tracks_sha256': 'c' * 64,
                'equivalence_labels_sha256': 'd' * 64,
            },
            'rows': [{
                'persistent_id': '0123456789ABCDEF',
                'genius_id': 1,
                'metadata_hex': struct.pack('<4Q', 0, 1, 1, 1).hex(),
                'similarities_hex': pack_similarities(0, []).hex(),
                'equivalence_labels': [[['literal', 'artist']], 'album', [['literal', 'artist'], 'title']],
            }],
        }
        self.original = {**unsigned, 'state_sha256': _hash(unsigned)}

    def test_rebind_changes_only_map_provenance_and_seals_state(self):
        rebound = _reseal_map_hash(self.original, 'e' * 64)
        self.assertEqual(rebound['source']['identity_map_sha256'], 'e' * 64)
        self.assertNotEqual(rebound['state_sha256'], self.original['state_sha256'])
        self.assertEqual(rebound['rows'], self.original['rows'])
        self.assertEqual(rebound['state_sha256'], _hash({
            key: value for key, value in rebound.items() if key != 'state_sha256'
        }))
        _assert_sql_state_rows_unchanged(self.original, rebound)

    def test_sql_metadata_or_ordered_relations_cannot_change(self):
        rebound = _reseal_map_hash(self.original, 'e' * 64)
        tampered = {**rebound, 'rows': [dict(rebound['rows'][0])]}
        tampered['rows'][0]['metadata_hex'] = struct.pack('<4Q', 0, 9, 1, 1).hex()
        tampered['state_sha256'] = _hash({key: value for key, value in tampered.items()
                                          if key != 'state_sha256'})
        with self.assertRaisesRegex(ValueError, 'changed SQL PID/GID'):
            _assert_sql_state_rows_unchanged(self.original, tampered)


if __name__ == '__main__':
    unittest.main()
