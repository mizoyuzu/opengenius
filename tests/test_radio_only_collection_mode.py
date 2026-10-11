"""The opt-in radio-only mode performs one seed-bound radio method call."""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from probe_ytmusic import collect
from probe_ytmusic_batch import _http_request_category


class RadioOnlyCollectorModeTests(unittest.TestCase):
    def test_http_categories_distinguish_radio_seed_and_continuations(self):
        base = 'https://music.youtube.com/youtubei/v1/'
        self.assertEqual(_http_request_category(base + 'next?alt=json', {'params': 'wAEB'}),
                         'radio_page')
        self.assertEqual(_http_request_category(base + 'next?ctoken=secret&alt=json',
                                                {'params': 'wAEB'}),
                         'radio_continuation')
        self.assertEqual(_http_request_category(base + 'next?alt=json',
                                                {'continuation': 'secret', 'params': 'wAEB'}),
                         'radio_continuation')
        self.assertEqual(_http_request_category(base + 'browse?alt=json',
                                                {'browseId': 'hidden',
                                                 'continuation': 'secret'}),
                         'related_continuation')
        self.assertEqual(_http_request_category(base + 'search?alt=json',
                                                {'query': 'hidden',
                                                 'continuation': 'secret'}),
                         'search_continuation')
        self.assertEqual(_http_request_category(base + 'next?alt=json', {'videoId': 'hidden'}),
                         'watch_seed_page')
        self.assertEqual(_http_request_category(base + 'browse?ctoken=secret', {}),
                         'related_continuation')
        self.assertEqual(_http_request_category(base + 'account/account_menu?key=secret', {}),
                         'account_preflight')
        self.assertEqual(_http_request_category(base + 'search?key=secret', {}),
                         'search_page')

    def test_unknown_collection_mode_fails_before_client_calls(self):
        class Client:
            calls = 0
            def get_watch_playlist(self, **kwargs):
                self.calls += 1
                return {'tracks': []}
        client = Client()
        with self.assertRaisesRegex(ValueError, 'Unsupported collection mode'):
            collect(client, {'persistent_id': 'A'}, [], 'abcdefghijk', 100,
                    collection_mode='unrecognized')
        self.assertEqual(client.calls, 0)

    def test_radio_only_skips_seed_watch_and_related_methods(self):
        class Client:
            def __init__(self):
                self.calls = []
            def get_watch_playlist(self, **kwargs):
                self.calls.append(('get_watch_playlist', kwargs.copy()))
                if kwargs.get('radio') is not True:
                    raise AssertionError('radio-only mode attempted an auxiliary watch request')
                return {'tracks': [{'videoId': 'abcdefghijk', 'title': 'Seed',
                                    'artists': [], 'length': '1:00'}]}
            def get_song_related(self, browse_id):
                raise AssertionError('radio-only mode attempted related browse')

        client = Client()
        snapshot = collect(client, {'persistent_id': 'A'}, [], 'abcdefghijk', 100,
                          collection_mode='radio-only-v1')
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][1],
                         {'videoId': 'abcdefghijk', 'radio': True, 'limit': 100})
        self.assertEqual(snapshot['related_status'], 'not_requested')
        self.assertEqual([row['endpoint'] for row in snapshot['requests']], ['watch_radio'])
        self.assertEqual({row['relation'] for row in snapshot['observations']}, {'radio'})


if __name__ == '__main__':
    unittest.main()
