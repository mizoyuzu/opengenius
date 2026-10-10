"""Whole-album metadata aliases only affect the album evidence field."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_identity_map import IdentityMap
from apply_reviewed_album_title_aliases import _candidate_pid_set


class AlbumTitleAliasTests(unittest.TestCase):
    def setUp(self):
        self.tracks = [
            {'persistent_id': 'PID1', 'title': 'Song (Instrument Version)',
             'artist': 'Artist A', 'album': '日本語盤', 'duration_ms': 180000},
        ]
        self.document = {'schema_version': 1, 'library_sha256': 'library',
                         'album_title_aliases': [{
                             'enabled': True,
                             'canonical_library_album': '日本語盤',
                             'exact_remote_album_aliases': ['ENGLISH ORIGINAL ALBUM'],
                             'status': 'root_reviewed_metadata_alias',
                         }]}
        self.remote = {'title': 'Song (Instrument Version)',
                       'artists': [{'name': 'Artist A'}],
                       'album': {'name': 'ENGLISH ORIGINAL ALBUM'},
                       'length': '3:00'}

    def test_alias_only_changes_album_match_and_keeps_local_metadata_unchanged(self):
        original_tracks = copy.deepcopy(self.tracks)
        matcher = IdentityMap(self.document, self.tracks, 'library')
        (candidate,) = matcher.match(self.remote)
        self.assertTrue(candidate['album_match'])
        self.assertEqual(candidate['persistent_id'], 'PID1')
        self.assertEqual(candidate['local_metadata'], {
            'title': 'Song (Instrument Version)', 'artist': 'Artist A',
            'album': '日本語盤', 'duration_ms': 180000})
        self.assertEqual(matcher.tracks, original_tracks)
        self.assertNotIn('recording_links', self.document)

    def test_no_album_alias_keeps_exact_album_behavior(self):
        document = {'schema_version': 1, 'library_sha256': 'library'}
        (candidate,) = IdentityMap(document, self.tracks, 'library').match(self.remote)
        self.assertFalse(candidate['album_match'])

    def test_alias_never_weakens_version_or_full_credit_gates(self):
        matcher = IdentityMap(self.document, self.tracks, 'library')
        self.assertEqual(matcher.match({**self.remote, 'title': 'Song'}), [])
        self.assertEqual(_candidate_pid_set(matcher, {**self.remote, 'title': 'Song'},
                                            'ENGLISH ORIGINAL ALBUM'), set())
        (partial,) = matcher.match({**self.remote,
                                    'artists': [{'name': 'Artist A'}, {'name': 'Guest'}]})
        self.assertEqual(partial['artist_credit_match'], 'partial')
        self.assertTrue(partial['album_match'])
        self.assertEqual(_candidate_pid_set(
            matcher, {**self.remote, 'artists': [{'name': 'Artist A'}, {'name': 'Guest'}]},
            'ENGLISH ORIGINAL ALBUM'), set())
        # IdentityMap returns review candidates with their evidence fields;
        # consumers must reject incomplete credits before treating them as
        # an identity match.

    def test_alias_matches_observed_whole_string_only(self):
        matcher = IdentityMap(self.document, self.tracks, 'library')
        (case_variant,) = matcher.match({**self.remote,
                                         'album': {'name': 'English Original Album'}})
        (partial_title,) = matcher.match({**self.remote,
                                          'album': {'name': 'prefix ENGLISH ORIGINAL ALBUM'}})
        self.assertFalse(case_variant['album_match'])
        self.assertFalse(partial_title['album_match'])

    def test_rejects_noncanonical_or_ambiguous_target(self):
        bad = copy.deepcopy(self.document)
        bad['album_title_aliases'][0]['canonical_library_album'] = 'missing album'
        with self.assertRaisesRegex(ValueError, 'not one existing canonical'):
            IdentityMap(bad, self.tracks, 'library')
        ambiguous_tracks = self.tracks + [{**self.tracks[0],
                                           'persistent_id': 'PID2',
                                           'album': '日本語盤 '}]
        with self.assertRaisesRegex(ValueError, 'not one existing canonical'):
            IdentityMap(self.document, ambiguous_tracks, 'library')

    def test_rejects_alias_shadowing_existing_album_and_conflicts(self):
        tracks = self.tracks + [{'persistent_id': 'PID2', 'title': 'Other',
                                 'artist': 'Other', 'album': 'Another local album',
                                 'duration_ms': 180000}]
        bad = copy.deepcopy(self.document)
        bad['album_title_aliases'][0]['exact_remote_album_aliases'] = ['Another local album']
        with self.assertRaisesRegex(ValueError, 'shadows an existing local album'):
            IdentityMap(bad, tracks, 'library')
        conflict = copy.deepcopy(self.document)
        conflict['album_title_aliases'].append(copy.deepcopy(conflict['album_title_aliases'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate or conflicting'):
            IdentityMap(conflict, self.tracks, 'library')


if __name__ == '__main__':
    unittest.main()
