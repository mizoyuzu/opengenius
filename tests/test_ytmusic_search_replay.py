import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from replay_ytmusic_search import replay_search_cache


class SearchReplayTests(unittest.TestCase):
    def test_search_identity_evidence_keeps_query_order_without_private_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = {'endpoint': 'search', 'arguments': {'query': 'Song Artist', 'filter': 'songs'},
                     'observed_at': 'original-time', 'response': [
                         {'playlistId': 'not-a-song'},
                         {'videoId': 'example1234', 'title': 'Song', 'artists': [{'name': 'Artist'}],
                          'feedbackTokens': {'add': 'secret'}, 'inLibrary': True}]}
            (root / 'search.json').write_text(json.dumps(value))
            (root / 'radio.json').write_text(json.dumps({'endpoint': 'get_watch_playlist'}))
            result = replay_search_cache(root)
            row, = result['observations']
            self.assertEqual(row['position'], 2)
            self.assertEqual(row['relation'], 'search_candidate')
            self.assertEqual(row['search_query'], 'Song Artist')
            self.assertEqual(row['observed_at'], 'original-time')
            self.assertIsNone(row['genius_rank'])
            self.assertNotIn('secret', json.dumps(result))
            self.assertEqual(result['network_requests'], 0)
            self.assertEqual(len(result['input_files']), 1)

    def test_non_song_searches_do_not_become_song_identity_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'artists.json').write_text(json.dumps({'endpoint': 'search', 'arguments': {'filter': 'artists'}}))
            with self.assertRaises(ValueError):
                replay_search_cache(root)


if __name__ == '__main__':
    unittest.main()
