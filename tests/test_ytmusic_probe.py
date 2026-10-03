import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from probe_ytmusic import clean_track, collect, local_candidates, observe


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.local = [
            {"persistent_id": "A", "title": "Song", "artist": "Artist", "duration_ms": 100000},
            {"persistent_id": "B", "title": "Song", "artist": "Artist", "duration_ms": 200000},
            {"persistent_id": "C", "title": "Song (Off Vocal)", "artist": "Artist", "duration_ms": 100000},
            {"persistent_id": "D", "title": "Song", "artist": "Artist", "duration_ms": 100100},
        ]
        self.row = {"videoId": "example1234", "title": "Song",
                    "artists": [{"name": "Artist"}], "length": "1:40"}

    def test_different_recording_rejected_and_duplicates_remain_ambiguous(self):
        self.assertEqual(local_candidates(self.row, self.local), ["A", "D"])
        instrumental = {**self.row, "title": "Song (Off Vocal)"}
        self.assertEqual(local_candidates(instrumental, self.local), ["C"])
        self.assertEqual(local_candidates({**self.row, "artists": []}, self.local), [])

    def test_observations_preserve_source_position_and_unknown_genius_rank(self):
        rows = [{"playlistId": "not-a-song"}, self.row]
        result = observe(rows, "related", "timestamp", self.local, "example1234")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["position"], 2)
        self.assertTrue(result[0]["is_seed"])
        self.assertIsNone(result[0]["genius_rank"])
        self.assertEqual(result[0]["identity_status"], "unverified")

    def test_feedback_and_account_fields_are_not_stored(self):
        row = {**self.row, "feedbackTokens": {"add": "secret"}, "inLibrary": True}
        self.assertNotIn("secret", str(clean_track(row)))
        self.assertNotIn("inLibrary", clean_track(row))

    def test_missing_related_is_distinct_from_radio_failure(self):
        row = self.row

        class Client:
            def __init__(self):
                self.modes = []

            def get_watch_playlist(self, **kwargs):
                self.modes.append(kwargs["radio"])
                return {"tracks": [row]}

            def get_song_related(self, browse_id):
                raise AssertionError("No related ID was returned")

        client = Client()
        result = collect(client, self.local[0], self.local, "example1234", 25)
        self.assertEqual(client.modes, [True, False])
        self.assertFalse(result["requests"][1]["related_available"])
        self.assertEqual(len(result["observations"]), 1)
        self.assertFalse(result["account_authentication_verified"])

    def test_optional_related_parser_error_retains_radio_but_http_errors_stop(self):
        import requests
        row = self.row
        class Client:
            failure = KeyError('private exception content')
            def get_watch_playlist(self, **kwargs):
                return {'tracks': [row], 'related': 'browse-id'}
            def get_song_related(self, browse_id):
                raise self.failure
        client = Client()
        result = collect(client, self.local[0], self.local, 'example1234', 25)
        self.assertEqual(result['related_status'], 'parser_error')
        self.assertEqual(len(result['observations']), 1)
        self.assertEqual(result['observations'][0]['relation'], 'radio')
        self.assertNotIn('private exception content', str(result))
        client.failure = requests.HTTPError('private HTTP content')
        with self.assertRaises(requests.HTTPError):
            collect(client, self.local[0], self.local, 'example1234', 25)

    def test_collect_applies_explicit_map_to_radio_and_related_without_confirming(self):
        from music_identity_map import IdentityMap, build_map
        tracks = [{'persistent_id': 'A', 'title': 'Song', 'artist': 'A & B', 'duration_ms': 100000}]
        document = build_map(tracks, 'hash')
        document['artist_credit_sets'] = [{'enabled': True, 'labels': ['A & B'], 'components': ['A', 'B']}]
        matcher = IdentityMap(document, tracks, 'hash')
        remote = {'videoId': 'example1234', 'title': 'Song',
                  'artists': [{'name': 'A'}, {'name': 'B'}], 'length': '1:40'}
        class Client:
            def get_watch_playlist(self, **kwargs):
                return {'tracks': [remote], 'related': 'browse-id'}
            def get_song_related(self, browse_id):
                return [{'title': 'Related', 'contents': [remote]}]
        result = collect(Client(), tracks[0], tracks, 'other123456', 25, candidate_matcher=matcher.match)
        self.assertEqual({o['relation'] for o in result['observations']}, {'radio', 'related'})
        for row in result['observations']:
            self.assertEqual(row['local_metadata_candidates'], ['A'])
            self.assertEqual(row['candidate_details'][0]['artist_match'], 'explicit_credit_set')
            self.assertEqual(row['identity_status'], 'unverified')
            self.assertIsNone(row['genius_rank'])


if __name__ == "__main__":
    unittest.main()
