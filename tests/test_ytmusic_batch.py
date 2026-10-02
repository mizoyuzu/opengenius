import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from probe_ytmusic_batch import AuthenticationUnconfirmed, CachedClient, select_seeds, verify_account


class BatchTests(unittest.TestCase):
    def test_missing_account_stops_instead_of_claiming_authenticated(self):
        class Client:
            def get_account_info(self):
                raise KeyError('missing account header')
        with self.assertRaises(AuthenticationUnconfirmed):
            verify_account(Client())
        class Valid:
            def get_account_info(self):
                return {'accountName': 'private name'}
        self.assertIs(verify_account(Valid()), True)

    def test_cache_reuses_response_without_private_fields_and_preserves_time(self):
        class Client:
            calls = 0
            def search(self, **kwargs):
                self.calls += 1
                return [{'videoId': 'example1234', 'title': 'Song',
                         'feedbackTokens': {'add': 'private token'}, 'inLibrary': True,
                         'artists': [{'name': 'Artist', 'id': 'artist-id'}]}]
        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            cached = CachedClient(client, Path(directory))
            first = cached.search(query='Song', filter='songs', limit=5)
            second = cached.search(query='Song', filter='songs', limit=5)
            self.assertEqual(first, second)
            self.assertEqual(client.calls, 1)
            self.assertEqual(cached.evidence[0]['observed_at'], cached.evidence[1]['observed_at'])
            self.assertFalse(cached.evidence[0]['cache_hit'])
            self.assertTrue(cached.evidence[1]['cache_hit'])
            stored = next(Path(directory).glob('*.json')).read_text()
            self.assertNotIn('private token', stored)
            self.assertNotIn('inLibrary', stored)
            self.assertEqual(json.loads(stored)['response'][0]['artists'][0]['id'], 'artist-id')

    def test_seeds_spread_artist_and_album_and_exclude_instrumentals(self):
        rows = [dict(persistent_id=str(i), title=title, artist=artist, album=album, duration_ms=1000)
                for i, (title, artist, album) in enumerate([
                    ('Song', 'A', 'X'), ('Other', 'A', 'Y'), ('Song (Off Vocal)', 'B', 'Z'),
                    ('Song', 'B', 'X'), ('Song', 'C', 'W')])]
        chosen = select_seeds(rows, 3)
        self.assertEqual(len(chosen), 2)
        self.assertEqual(len({t['artist'] for t in chosen}), 2)
        self.assertEqual(len({t['album'] for t in chosen}), 2)


if __name__ == '__main__':
    unittest.main()
