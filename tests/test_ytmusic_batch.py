import copy
import hashlib
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
                                 create_paced_session, main, resolve_seed_search, select_observed_seeds, select_planned_seeds, select_seeds, verify_account)


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
    def test_explicit_seed_plan_preserves_order_metadata_and_original_tracks(self):
        tracks = [{'persistent_id': f'{index:016X}', 'title': f'Original {index}', 'artist': 'Artist',
                   'album': 'Album', 'duration_ms': 100000, 'nested': {'value': index}} for index in (1, 2)]
        before = copy.deepcopy(tracks)
        plan = {'schema_version': 1, 'source_library_sha256': 'a' * 64, 'seeds': [
            {'persistent_id': tracks[index]['persistent_id'], 'series': 'Series', 'sample_kind': 'album_track'}
            for index in (1, 0)]}
        chosen = select_planned_seeds(plan, tracks, 'a' * 64, 2)
        self.assertEqual([row['title'] for row in chosen], ['Original 2', 'Original 1'])
        self.assertEqual(chosen[0]['seed_series'], 'Series')
        self.assertEqual(chosen[0]['seed_sample_kind'], 'album_track')
        chosen[0]['nested']['value'] = 999
        self.assertEqual(tracks, before)
        invalid = []
        foreign = copy.deepcopy(plan); foreign['source_library_sha256'] = 'b' * 64; invalid.append(foreign)
        duplicate = copy.deepcopy(plan); duplicate['seeds'][1]['persistent_id'] = tracks[1]['persistent_id']; invalid.append(duplicate)
        unknown = copy.deepcopy(plan); unknown['seeds'][0]['persistent_id'] = 'F' * 16; invalid.append(unknown)
        override = copy.deepcopy(plan); override['seeds'][0]['title'] = 'Injected title'; invalid.append(override)
        lower = copy.deepcopy(plan); lower['seeds'][0]['persistent_id'] = 'abcdef0123456789'; invalid.append(lower)
        empty_label = copy.deepcopy(plan); empty_label['seeds'][0]['series'] = ' '; invalid.append(empty_label)
        invalid_kind = copy.deepcopy(plan); invalid_kind['seeds'][0]['sample_kind'] = None; invalid.append(invalid_kind)
        invalid.extend([{**plan, 'seeds': []}, {**plan, 'seeds': {}}, {**plan, 'schema_version': True}])
        for index, document in enumerate(invalid):
            with self.subTest(index=index), self.assertRaises(ValueError):
                select_planned_seeds(document, tracks, 'a' * 64, 2)
        with self.assertRaisesRegex(ValueError, 'exactly --seeds'):
            select_planned_seeds(plan, tracks, 'a' * 64, 1)

    def test_explicit_seed_plan_cli_is_network_free_and_records_input_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            track = {'persistent_id': '0000000000000001', 'title': 'Original song', 'artist': 'Artist',
                     'album': 'Album', 'duration_ms': 100000}
            snapshot = root / 'tracks.json'
            snapshot.write_text(json.dumps({'source_sha256': 'a' * 64, 'tracks': [track]}))
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'schema_version': 1, 'source_library_sha256': 'a' * 64, 'seeds': [
                {'persistent_id': track['persistent_id'], 'series': 'Series', 'sample_kind': 'album_track'}]}))
            output = root / 'experiment'
            argv = ['probe', '--track-snapshot', str(snapshot), '--seed-plan', str(plan), '--seeds', '1',
                    '--output', str(output), '--auth', str(root / 'absent-auth'), '--plan-only']
            with patch.object(sys, 'argv', argv), patch('sys.stdout', new=io.StringIO()), \
                    patch('probe_ytmusic_batch.verify_account', side_effect=AssertionError('No auth')), \
                    patch('probe_ytmusic_batch.create_paced_session', side_effect=AssertionError('No HTTP')):
                self.assertEqual(main(), 0)
            manifest = json.loads((output / 'seeds.json').read_text())
            self.assertEqual(manifest['seed_plan_sha256'], hashlib.sha256(plan.read_bytes()).hexdigest())
            self.assertEqual(manifest['selection'], 'explicit_diverse_library_seed_plan')
            self.assertEqual(manifest['seeds'][0]['title'], track['title'])
            self.assertEqual(manifest['seeds'][0]['seed_series'], 'Series')
            summary = json.loads(next(output.glob('summary-*.json')).read_text())
            self.assertEqual(summary['http_requests'], 0)
            self.assertFalse(summary['account_authentication_verified'])
            with patch.object(sys, 'argv', [*argv, '--seed-observations', str(root / 'absent-observations')]), \
                    patch('sys.stderr', new=io.StringIO()), patch('probe_ytmusic_batch.load_library') as library:
                with self.assertRaises(SystemExit) as rejected:
                    main()
                self.assertEqual(rejected.exception.code, 2)
                library.assert_not_called()

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
                            patch('probe_ytmusic_batch.load_library') as decode:
                        with self.assertRaises(SystemExit) as stopped:
                            main()
                        self.assertEqual(stopped.exception.code, 2)
                        decode.assert_not_called()
                    self.assertFalse((root / 'output').exists())

    def test_localized_filtered_search_stops_before_auth_or_library_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            argv = ['probe', '--track-snapshot', str(root / 'missing.json'),
                    '--output', str(root / 'output'), '--language', 'ja']
            with patch.object(sys, 'argv', argv), patch('sys.stderr', new=io.StringIO()), \
                    patch('probe_ytmusic_batch.load_library') as load:
                with self.assertRaises(SystemExit) as stopped:
                    main()
                self.assertEqual(stopped.exception.code, 2)
                load.assert_not_called()
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

    def test_saved_search_seeds_deduplicate_videos_and_spread_canonical_credits(self):
        from music_identity_map import IdentityMap, build_map
        tracks = [dict(persistent_id=str(i), title=title, artist=artist, album='Album', duration_ms=100000)
                  for i, (title, artist) in enumerate([('First', 'A'), ('Second', 'A'), ('Third', 'B')])]
        matcher = IdentityMap(build_map(tracks, 'hash'), tracks, 'hash')
        def row(index, video):
            track = tracks[index]
            return {'source': 'ytmusic', 'relation': 'search_candidate', 'track': {
                'video_id': video, 'title': track['title'], 'artists': [{'name': track['artist']}],
                'album': {'name': 'Album'}, 'duration_seconds': 100}}
        snapshot = {'observations': [row(0, 'abcdefghij0'), row(0, 'abcdefghij0'),
                                     row(1, 'abcdefghij1'), row(2, 'abcdefghij2')]}
        seeds = select_observed_seeds(snapshot, matcher, 3)
        self.assertEqual([seed['artist'] for seed in seeds], ['A', 'B', 'A'])
        self.assertEqual(len({seed['seed_video_id'] for seed in seeds}), 3)
        snapshot['observations'].append(row(0, 'abcdefghij3'))
        self.assertNotIn('0', [seed['persistent_id'] for seed in select_observed_seeds(snapshot, matcher, 3)])
        duplicated = [*tracks, dict(tracks[2], persistent_id='duplicate', album='Other album')]
        ambiguous_matcher = IdentityMap(build_map(duplicated, 'hash'), duplicated, 'hash')
        self.assertNotIn('2', [seed['persistent_id'] for seed in select_observed_seeds(snapshot, ambiguous_matcher, 3)])
        snapshot['observations'][0]['relation'] = 'radio'
        with self.assertRaisesRegex(ValueError, 'search candidates'):
            select_observed_seeds(snapshot, matcher, 3)

    def test_seed_album_preference_preserves_ambiguity_without_unique_album(self):
        from music_identity_map import IdentityMap, build_map
        seed = dict(persistent_id='ROOT', title='Song', artist='Artist', album='Deluxe', duration_ms=100000)
        matcher = IdentityMap(build_map([seed], 'hash'), [seed], 'hash')
        def row(video, album, duration=100):
            return {'videoId': video, 'title': 'Song', 'artists': [{'name': 'Artist'}],
                    'album': {'name': album}, 'duration_seconds': duration}
        found = [row('original001', 'Original'), row('deluxe00001', 'Deluxe')]
        chosen, count, status = resolve_seed_search(found, seed, [seed], matcher)
        self.assertEqual((chosen, count, status), ('deluxe00001', 2, 'unique_album_metadata_preference_unverified'))
        found.append(row('deluxe00002', 'Deluxe'))
        self.assertEqual(resolve_seed_search(found, seed, [seed], matcher)[0], None)
        found[1]['duration_seconds'] = 120
        found[2]['duration_seconds'] = 120
        self.assertEqual(resolve_seed_search(found, seed, [seed], matcher)[0], 'original001')

    def test_snapshot_plan_with_map_never_authenticates(self):
        from music_identity_map import build_map
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tracks = [{'persistent_id': '0000000000000001', 'title': 'Song', 'artist': 'Artist',
                       'album': 'Album', 'duration_ms': 100000}]
            snapshot = root / 'tracks.json'
            snapshot.write_text(json.dumps({'source_sha256': 'a' * 64, 'tracks': tracks}))
            aliases = root / 'map.json'
            aliases.write_text(json.dumps(build_map(tracks, 'a' * 64)))
            output = root / 'plan'
            argv = ['probe', '--track-snapshot', str(snapshot), '--identity-map', str(aliases),
                    '--output', str(output), '--auth', str(root / 'nonexistent-auth'), '--plan-only']
            with patch.object(sys, 'argv', argv), patch('sys.stdout', new=io.StringIO()), \
                    patch('probe_ytmusic_batch.verify_account', side_effect=AssertionError('No authentication')), \
                    patch('probe_ytmusic_batch.create_paced_session', side_effect=AssertionError('No HTTP')):
                self.assertEqual(main(), 0)
            manifest = json.loads((output / 'seeds.json').read_text())
            self.assertEqual(manifest['library_input']['kind'], 'track_snapshot')
            self.assertIsNotNone(manifest['identity_map_sha256'])
            report = json.loads(next(output.glob('summary-*.json')).read_text())
            self.assertEqual(report['http_requests'], 0)
            self.assertFalse(report['account_authentication_verified'])


if __name__ == '__main__':
    unittest.main()
