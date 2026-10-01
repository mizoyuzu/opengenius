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


if __name__ == "__main__":
    unittest.main()
