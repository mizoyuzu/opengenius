import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from probe_ytmusic_batch import (AuthenticationUnconfirmed, CachedClient,
                                 create_paced_session, main, select_seeds, verify_account)


class OfflineAdapter(requests.adapters.BaseAdapter):
    def __init__(self, status=200, clock=None):
        self.status, self.clock = status, clock
        self.calls = []

    def send(self, request, **kwargs):
        self.calls.append({'time': self.clock[0] if self.clock else None,
                           'timeout': kwargs['timeout']})
        response = requests.Response()
        response.status_code = self.status
        response.url = request.url
        response.request = request
        response._content = b'{}'
        if 300 <= self.status < 400:
            response.headers['Location'] = '/redirected'
        return response

    def close(self):
        pass


class BatchTests(unittest.TestCase):
    def test_redirects_stop_without_an_unpaced_followup_send(self):
        for status in (300, 301, 302, 303, 304, 307, 308):
            with self.subTest(status=status):
                with create_paced_session(5, 1) as session:
                    adapter = OfflineAdapter(status)
                    session.mount('https://', adapter)
                    with self.assertRaises(requests.HTTPError):
                        session.get('https://example.invalid/test', allow_redirects=True)
                    self.assertEqual(len(adapter.calls), 1)
                    self.assertEqual(session.count, 1)

    def test_pacing_and_budget_apply_to_each_send(self):
        clock = [100.0]
        def sleep(seconds):
            clock[0] += seconds
        with patch('probe_ytmusic_batch.time.monotonic', side_effect=lambda: clock[0]), \
                patch('probe_ytmusic_batch.time.sleep', side_effect=sleep):
            with create_paced_session(5, 2) as session:
                adapter = OfflineAdapter(clock=clock)
                session.mount('https://', adapter)
                session.get('https://example.invalid/first', timeout=1)
                clock[0] += 2
                session.get('https://example.invalid/second')
                with self.assertRaisesRegex(RuntimeError, 'budget'):
                    session.get('https://example.invalid/third')
                self.assertEqual([call['time'] for call in adapter.calls], [100.0, 105.0])
                self.assertEqual([call['timeout'] for call in adapter.calls], [25, 25])
                self.assertEqual(session.count, 2)

    def test_nonfinite_intervals_rejected_before_reading_library(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for interval in ('nan', 'inf', '-inf'):
                with self.subTest(interval=interval):
                    argv = ['probe', str(root / 'library'), '--output', str(root / 'output'),
                            '--interval=' + interval, '--plan-only']
                    with patch.object(sys, 'argv', argv), patch('sys.stderr', new=io.StringIO()), \
                            patch('probe_ytmusic_batch.decode_musicdb') as decode:
                        with self.assertRaises(SystemExit) as stopped:
                            main()
                        self.assertEqual(stopped.exception.code, 2)
                        decode.assert_not_called()
                    self.assertFalse((root / 'output').exists())

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
