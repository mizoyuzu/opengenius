"""Offline tests for exact-source album search planning and evidence."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_identity_map import IdentityMap, build_map
from search_unindexed_album_sources import (build_album_search_evidence,
    build_album_search_plan, sanitize_album_search_response,
    validate_album_search_plan)

LIBRARY_HASH = 'a' * 64


def track(pid, title, *, artist='Artist', album='Album', duration=100000):
    return {'persistent_id': pid, 'title': title, 'artist': artist,
            'album': album, 'duration_ms': duration}


class SearchUnindexedAlbumSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tracks = [track('pid000000000001', 'One'),
                       track('pid000000000002', 'Two'),
                       track('pid000000000003', 'Three'),
                       track('pid000000000004', 'Live version (Live)'),
                       track('pid000000000005', 'Song (Off Vocal)', album='Other')]
        self.map_doc = build_map(self.tracks, LIBRARY_HASH)
        self.map_bytes = json.dumps(self.map_doc).encode()
        self.map_hash = hashlib.sha256(self.map_bytes).hexdigest()
        self.matcher = IdentityMap(self.map_doc, self.tracks, LIBRARY_HASH)
        self.plan = build_album_search_plan(self.tracks, LIBRARY_HASH,
            self.map_hash, self.matcher, {'pid000000000001'}, state_hash='c' * 64,
            track_snapshot_hash='d' * 64, limit=5)

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_groups_exact_album_and_excludes_indexed_and_explicit_versions(self):
        self.assertEqual(self.plan['eligible_unindexed_track_count'], 3)
        self.assertEqual(self.plan['album_group_count'], 1)
        row, = self.plan['albums']
        self.assertEqual(row['candidate_pids'], ['pid000000000002', 'pid000000000003',
                                                 'pid000000000004'])
        self.assertEqual(row['query'], 'Album Artist')
        self.assertEqual(self.plan['recommendation_edges_created'], 0)

    def test_same_artist_credits_share_diversified_query_slots(self):
        plan = build_album_search_plan(self.tracks + [
            track('pid000000000006', 'Four', album='Album 2'),
            track('pid000000000007', 'Five', album='Album 3', artist='Second')],
            LIBRARY_HASH, self.map_hash, self.matcher, {'pid000000000001'},
            state_hash='c' * 64, track_snapshot_hash='d' * 64, limit=3)
        self.assertEqual(len(plan['albums']), 3)
        self.assertNotEqual(plan['albums'][0]['canonical_artists'],
                            plan['albums'][1]['canonical_artists'])

    def test_sanitizer_explicitly_keeps_valid_mpre_and_drops_private_fields(self):
        rows = sanitize_album_search_response([
            {'browseId': 'MPREvalid123', 'title': 'Album',
             'artists': [{'name': 'Artist', 'id': 'artist1'}],
             'feedbackTokens': {'secret': 'no'}, 'year': '2020'},
            {'browseId': 'bad', 'title': 'Album', 'artists': [{'name': 'Artist'}]},
            {'browseId': 'MPREnoartist123', 'title': 'Album', 'artists': []}])
        self.assertEqual(rows, [{'browseId': 'MPREvalid123', 'title': 'Album',
            'artists': [{'name': 'Artist', 'id': 'artist1'}], 'year': '2020'}])
        self.assertNotIn('secret', json.dumps(rows))

    def test_saved_result_requires_full_exact_title_and_artist_set(self):
        response = [
            {'browseId': 'MPREexactalbum123', 'title': 'Album',
             'artists': [{'name': 'Artist'}]},
            {'browseId': 'MPREwrongartist123', 'title': 'Album',
             'artists': [{'name': 'Other'}]},
            {'browseId': 'MPREpartialtitle123', 'title': 'Album Deluxe',
             'artists': [{'name': 'Artist'}]},
            {'browseId': 'MPREbad?browseid', 'title': 'Album',
             'artists': [{'name': 'Artist'}]},
        ]
        response = sanitize_album_search_response(response)
        envelope = {'endpoint': 'search',
            'arguments': {'query': self.plan['albums'][0]['query'],
                          'filter': 'albums', 'limit': 5},
            'observed_at': '2026-10-09T00:00:00+00:00', 'response': response}
        path = self.root / 'search.json'
        raw = json.dumps(envelope, ensure_ascii=False).encode()
        path.write_bytes(raw)
        evidence = build_album_search_evidence(self.plan,
            [{'query': envelope['arguments']['query'], 'path': str(path),
              'sha256': hashlib.sha256(raw).hexdigest(), 'response': response}],
            self.tracks, self.matcher, LIBRARY_HASH, self.map_hash)
        candidate, = evidence['search_album_candidates']
        self.assertEqual(candidate['browse_id'], 'MPREexactalbum123')
        self.assertEqual(candidate['source']['source_key'], 'response[0].browseId')
        self.assertEqual(candidate['source_kind'], 'search_album')
        self.assertEqual(evidence['audit']['recommendation_edges_created'], 0)
        self.assertEqual(evidence['audit']['recording_identity_claims_created'], 0)

    def test_saved_search_hash_and_arguments_are_revalidated(self):
        validate_album_search_plan(self.plan, LIBRARY_HASH, self.map_hash)
        path = self.root / 'bad.json'
        path.write_text(json.dumps({'endpoint': 'search', 'arguments': {},
                                    'observed_at': 'now', 'response': []}))
        with self.assertRaisesRegex(ValueError, 'provenance'):
            build_album_search_evidence(self.plan,
                [{'query': self.plan['albums'][0]['query'], 'path': str(path),
                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'response': []}],
                self.tracks, self.matcher, LIBRARY_HASH, self.map_hash)


if __name__ == '__main__':
    unittest.main()
