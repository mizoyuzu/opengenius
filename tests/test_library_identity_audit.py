import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_library_identity import audit_library_identity
from music_identity_map import build_map


class LibraryIdentityAuditTests(unittest.TestCase):
    def test_audit_lists_aliases_facets_and_duplicate_pids_without_resolving(self):
        tracks = [
            {'persistent_id': '0000000000000001', 'title': 'Song (Game Size)',
             'artist': 'Artist', 'album': 'Release A', 'duration_ms': 100000},
            {'persistent_id': '0000000000000002', 'title': 'Song (Game Size)',
             'artist': 'Artist', 'album': 'Release B', 'duration_ms': 100000},
            {'persistent_id': '0000000000000003', 'title': 'Song (Off Vocal)',
             'artist': 'Artist', 'album': 'Release A', 'duration_ms': 100000},
        ]
        identity_map = build_map(tracks, 'library-hash')
        identity_map['track_title_aliases'] = [
            {'persistent_id': tracks[0]['persistent_id'], 'original_title': tracks[0]['title'],
             'aliases': [{'value': 'Song (Game Size) - English', 'enabled': True,
                          'status': 'reviewed_spelling_alias'}]},
            {'persistent_id': tracks[2]['persistent_id'], 'original_title': tracks[2]['title'],
             'aliases': [{'value': 'Song (Off Vocal) - English', 'enabled': False,
                          'status': 'needs_review'}]},
        ]
        report = audit_library_identity(
            tracks, identity_map, 'library-hash', identity_map_sha256='map-hash',
            input_hashes={'identity_map_sha256': 'map-hash'}, script_sha256='script-hash')

        self.assertEqual(report['summary']['track_count'], 3)
        self.assertEqual(report['summary']['enabled_alias_count'], 1)
        self.assertEqual(report['summary']['pending_alias_count'], 1)
        self.assertEqual(report['summary']['duplicate_title_artist_group_count'], 1)
        duplicate, = report['duplicate_title_artist_groups']
        self.assertEqual(duplicate['persistent_ids'], [tracks[0]['persistent_id'], tracks[1]['persistent_id']])
        self.assertFalse(duplicate['auto_resolved'])
        self.assertEqual(report['identity_status'], 'unverified')
        self.assertEqual(report['recording_identity_claims'], 0)
        self.assertEqual(report['identity_map_sha256'], 'map-hash')
        self.assertEqual(report['network_requests'], 0)
        self.assertIn('game_size', report['tracks'][0]['version_facets']['markers'])
        self.assertEqual(report['tracks'][2]['version_facets']['kinds'], ['off_vocal'])
        self.assertEqual(report['tracks'][0]['title_aliases'][0]['status'], 'reviewed_spelling_alias')


if __name__ == '__main__':
    unittest.main()
