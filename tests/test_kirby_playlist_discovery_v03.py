import json
from pathlib import Path
import tempfile
import unittest

from scripts.probe_kirby_playlist_discovery_v03 import (
    PlaylistMetadataCachedClient,
    exact_playlist_rows,
    sanitize_playlist_metadata,
)


class FakeClient:
    def __init__(self):
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(('search', kwargs))
        return [{
            'title': 'KIRBY STAR ALLIES: THE ORIGINAL SOUNDTRACK',
            'playlistId': 'PL_PUBLIC_EXAMPLE',
            'browseId': 'VLPL_PUBLIC_EXAMPLE',
            'authors': [{'name': 'Eternal', 'channelId': 'public-channel'}],
            'videoCount': '233 videos',
            'resultType': 'playlist',
            'privateAuthData': 'must-not-survive',
        }]


class PlaylistDiscoveryV03Tests(unittest.TestCase):
    def test_local_allowlist_preserves_playlist_and_track_metadata_only(self):
        raw = {
            'playlistId': 'PL1', 'author': {'name': 'Eternal'},
            'itemCount': 233, 'tracks': [{
                'videoId': 'abcdefghijk', 'title': '01-01. Green Gardens',
                'artists': [{'name': 'Eternal', 'id': 'UC1'}],
                'album': {'name': 'KIRBY STAR ALLIES: THE ORIGINAL SOUNDTRACK'},
                'duration': '5:12', 'privateAuthData': 'drop',
            }],
            'privateAuthData': 'drop',
        }
        clean = sanitize_playlist_metadata(raw)
        self.assertEqual(clean['playlistId'], 'PL1')
        self.assertEqual(clean['author'], {'name': 'Eternal'})
        self.assertEqual(clean['itemCount'], 233)
        self.assertEqual(clean['tracks'][0]['videoId'], 'abcdefghijk')
        self.assertNotIn('privateAuthData', clean)
        self.assertNotIn('privateAuthData', clean['tracks'][0])

    def test_playlist_cache_and_exact_row_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            cached = PlaylistMetadataCachedClient(client, Path(tmp))
            response = cached.search(query='Kirby', filter='playlists', limit=20)
            matches = exact_playlist_rows(response)
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]['playlist_id'], 'PL_PUBLIC_EXAMPLE')
            self.assertEqual(matches[0]['author'], 'Eternal')
            self.assertEqual(matches[0]['video_count'], 233)
            receipt = next(Path(tmp).glob('*.json'))
            stored = json.loads(receipt.read_text())
            self.assertEqual(stored['endpoint'], 'search')
            self.assertEqual(stored['response'][0]['playlistId'], 'PL_PUBLIC_EXAMPLE')
            self.assertNotIn('privateAuthData', stored['response'][0])

            replay = cached.search(query='Kirby', filter='playlists', limit=20)
            self.assertEqual(replay, response)
            self.assertEqual(cached.cache_hits, 1)
            self.assertEqual(len(client.calls), 1)

    def test_playlist_rows_without_identifier_are_not_candidates(self):
        rows = [{'title': 'KIRBY STAR ALLIES: THE ORIGINAL SOUNDTRACK',
                 'resultType': 'playlist'}]
        self.assertEqual(exact_playlist_rows(rows), [])

    def test_item_count_is_retained_as_playlist_size_fallback(self):
        rows = [{'title': 'KIRBY STAR ALLIES: THE ORIGINAL SOUNDTRACK',
                 'playlistId': 'PL2', 'itemCount': '233 items',
                 'resultType': 'playlist'}]
        self.assertEqual(exact_playlist_rows(rows)[0]['video_count'], 233)


if __name__ == '__main__':
    unittest.main()
