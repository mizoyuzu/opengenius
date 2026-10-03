"""Track and equality-group collision tests for the persisted prototype."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_genius_dataset import allocate_rows
from genius_format import pack_similarities, parse_metadata, parse_similarities
from music_identity_map import IdentityMap, build_map


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [dict(persistent_id=pid, title=pid, artist='Artist', album='Album',
                            duration_ms=100000, genius_id='0000000000000000') for pid in ('A', 'B')]
        self.graph = {'root_pid': 'A', 'ordered_target_pids': ['B']}

    def matcher(self):
        return IdentityMap(build_map(self.tracks, 'hash'), self.tracks, 'hash')

    def test_disjoint_ids_and_group_words_keep_equalities_and_order(self):
        self.tracks.append(dict(self.tracks[0], persistent_id='UNSELECTED', genius_id='0000000070000003'))
        legacy = {0x70000001: struct.pack('<4Q', 0, 1, 1, 1)}
        ids, metadata, relations = allocate_rows(self.graph, self.matcher(), legacy,
                                                 {0x70000002: pack_similarities(0, [])})
        self.assertEqual(ids, {'A': 0x70000004, 'B': 0x70000005})
        words = [parse_metadata(metadata[ids[pid]]) for pid in ('A', 'B')]
        self.assertEqual(words[0][1:3], words[1][1:3])
        self.assertNotEqual(words[0][3], words[1][3])
        self.assertTrue(all(value != 1 for row in words for value in row[1:]))
        self.assertEqual(parse_similarities(relations[ids['A']])[1], [ids['B']])
        self.assertEqual(parse_similarities(relations[ids['B']])[1], [])

    def test_occupied_selected_track_or_missing_gid_rejected(self):
        self.tracks[0]['genius_id'] = '0000000000000001'
        with self.assertRaisesRegex(ValueError, 'already has'):
            allocate_rows(self.graph, self.matcher(), {}, {})
        self.tracks[0].pop('genius_id')
        with self.assertRaisesRegex(ValueError, 'every track'):
            allocate_rows(self.graph, self.matcher(), {}, {})


if __name__ == '__main__':
    unittest.main()
