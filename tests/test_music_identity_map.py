import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_identity_map import IdentityMap, build_map


class IdentityMapTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [
            {'persistent_id': 'A', 'title': '曲 (Off Vocal) - Song (Off Vocal)',
             'artist': "L'Antica", 'duration_ms': 100000, 'album': 'Album'},
            {'persistent_id': 'B', 'title': '曲 - Song',
             'artist': "L'Antica", 'duration_ms': 100000, 'album': 'Album'},
        ]
        self.document = build_map(self.tracks, 'library-hash', [{
            'from': 'アンティーカ', 'to': "L'Antica", 'matching_title_album_duration_records': 61}])

    def test_map_rejects_other_library_and_stale_track_title(self):
        with self.assertRaises(ValueError):
            IdentityMap(self.document, self.tracks, 'another-library')
        stale = copy.deepcopy(self.document)
        stale['track_title_aliases'][0]['original_title'] = 'changed'
        with self.assertRaises(ValueError):
            IdentityMap(stale, self.tracks, 'library-hash')

    def test_pending_translation_aliases_are_inactive_and_preserve_versions(self):
        self.assertEqual(self.document['track_title_aliases'][0]['aliases'][0]['value'], '曲 (Off Vocal)')
        matcher = IdentityMap(self.document, self.tracks, 'library-hash')
        remote = {'title': '曲', 'artists': [{'name': 'アンティーカ'}], 'duration_seconds': 100}
        self.assertEqual(matcher.match(remote), [])
        enabled = copy.deepcopy(self.document)
        enabled['track_title_aliases'][1]['aliases'][0]['enabled'] = True
        rows = IdentityMap(enabled, self.tracks, 'library-hash').match(remote)
        self.assertEqual([row['persistent_id'] for row in rows], ['B'])
        self.assertEqual(rows[0]['title_match'], 'explicit_alias')
        self.assertEqual(rows[0]['identity_status'], 'unverified')

    def test_artist_alias_requires_duration_and_reports_album_discrepancy(self):
        matcher = IdentityMap(self.document, self.tracks, 'library-hash')
        remote = {'title': '曲 - Song', 'artists': [{'name': 'アンティーカ'}],
                  'duration_seconds': 101, 'album': {'name': 'Other release'}}
        row, = matcher.match(remote)
        self.assertEqual(row['persistent_id'], 'B')
        self.assertEqual(row['artist_match'], 'explicit_alias')
        self.assertEqual(row['duration_difference_seconds'], 1)
        self.assertFalse(row['album_match'])
        self.assertEqual(matcher.match({**remote, 'duration_seconds': None}), [])
        self.assertEqual(matcher.match({**remote, 'duration_seconds': 104}), [])
        self.assertEqual(matcher.match({**remote, 'artists': []}), [])

    def test_conflicting_alias_groups_are_rejected(self):
        document = copy.deepcopy(self.document)
        document['artist_alias_groups'].append({'enabled': True, 'aliases': ["L'Antica", 'Different Artist']})
        with self.assertRaises(ValueError):
            IdentityMap(document, self.tracks, 'library-hash')


if __name__ == '__main__':
    unittest.main()
