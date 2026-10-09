"""Offline provenance and strict matching checks for album catalog seed discovery."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from discover_album_catalog_seeds import (build_album_catalog_evidence,
    build_album_plan, catalog_album, validate_album_plan)
from music_identity_map import IdentityMap, build_map
from plan_genius_update import make_synthetic_state
from discover_album_catalog_seeds import run_discovery
from search_unindexed_album_sources import build_album_search_evidence, build_album_search_plan

LIBRARY_HASH = 'a' * 64
VIDEO = 'catalog0001'
ALBUM_ID = 'MPREknownalbum01'


def track(pid, title, *, artist='Artist', album='Album', duration=100000):
    return {'persistent_id': pid, 'title': title, 'artist': artist,
            'album': album, 'duration_ms': duration}


def snapshot(rows, *, library_hash=LIBRARY_HASH, map_hash='b' * 64):
    return {'schema_version': 1, 'library_sha256': library_hash,
            'identity_map_sha256': map_hash, 'observations': rows}


def radio_row(title='Observed song', album_id=ALBUM_ID, album_name='Album', video=VIDEO):
    return {'source': 'ytmusic', 'relation': 'radio', 'track': {
        'video_id': video, 'title': title, 'artists': [{'name': 'Artist'}],
        'album': {'id': album_id, 'name': album_name}, 'duration_seconds': 100}}


class DiscoverAlbumCatalogSeedsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tracks = [track('0000000000000001', 'Observed song'),
                       track('0000000000000002', 'Local song two'),
                       track('0000000000000003', 'Local song three')]
        self.map_document = build_map(self.tracks, LIBRARY_HASH)
        self.map_bytes = json.dumps(self.map_document).encode()
        self.map_hash = hashlib.sha256(self.map_bytes).hexdigest()
        self.matcher = IdentityMap(self.map_document, self.tracks, LIBRARY_HASH)
        self.snapshot_path = self.root / 'radio.json'
        self.snapshot_path.write_text(json.dumps(snapshot([radio_row()], map_hash=self.map_hash)),
                                      encoding='utf-8')
        self.plan = build_album_plan(self.tracks, LIBRARY_HASH, self.map_hash,
            {'0000000000000001'}, [self.snapshot_path], self.matcher)

    def tearDown(self):
        self.temp.cleanup()

    def saved_response(self, response, album_id=ALBUM_ID):
        path = self.root / ('catalog-' + album_id + '.json')
        raw = json.dumps({'endpoint': 'get_album', 'arguments': {'browseId': album_id},
                          'observed_at': '2026-10-09T00:00:00+00:00',
                          'response': response}).encode()
        path.write_bytes(raw)
        return {'album_id': album_id, 'response': response, 'path': str(path),
                'sha256': hashlib.sha256(raw).hexdigest()}

    def test_plan_uses_only_saved_radio_album_ids_and_prioritizes_local_coverage(self):
        plan = build_album_plan(self.tracks, LIBRARY_HASH, self.map_hash,
            {'0000000000000001'}, [self.snapshot_path], self.matcher)
        self.assertEqual([row['album_id'] for row in plan['albums']], [ALBUM_ID])
        album, = plan['albums']
        self.assertEqual(album['candidate_pids'], ['0000000000000002', '0000000000000003'])
        self.assertEqual(album['estimated_unindexed_local_tracks'], 2)
        self.assertEqual(album['source_inputs'][0]['source_key'], 'observations[0].track.album.id')
        self.assertEqual(plan['recommendation_edges_created'], 0)

    def test_non_radio_album_ids_are_not_planned(self):
        self.snapshot_path.write_text(json.dumps(snapshot([
            {**radio_row(), 'relation': 'related'},
            {**radio_row(album_id='MPREotheralbum02'), 'relation': 'related'}
        ], map_hash=self.map_hash)), encoding='utf-8')
        plan = build_album_plan(self.tracks, LIBRARY_HASH, self.map_hash,
            {'0000000000000001'}, [self.snapshot_path], self.matcher)
        self.assertEqual(plan['albums'], [])

    def test_plan_validation_rechecks_saved_source_hash_and_exact_album_key(self):
        validate_album_plan(self.plan, LIBRARY_HASH, self.map_hash)
        self.snapshot_path.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            validate_album_plan(self.plan, LIBRARY_HASH, self.map_hash)

    def test_search_album_evidence_can_seed_catalog_plan_without_radio_observations(self):
        search_plan = build_album_search_plan(
            self.tracks, LIBRARY_HASH, self.map_hash, self.matcher,
            {'0000000000000001'}, state_hash='c' * 64,
            track_snapshot_hash='d' * 64, limit=10)
        album_group = next(row for row in search_plan['albums']
                           if row['album_title'] == 'Album')
        response = [{'browseId': ALBUM_ID, 'title': 'Album',
                     'artists': [{'name': 'Artist'}]}]
        response_path = self.root / 'search-response.json'
        raw = json.dumps({'endpoint': 'search',
            'arguments': {'query': album_group['query'], 'filter': 'albums', 'limit': 5},
            'observed_at': '2026-10-09T00:00:00+00:00',
            'response': response}).encode()
        response_path.write_bytes(raw)
        search_evidence = build_album_search_evidence(search_plan, [{
            'query': album_group['query'], 'response': response,
            'path': str(response_path), 'sha256': hashlib.sha256(raw).hexdigest()}],
            self.tracks, self.matcher, LIBRARY_HASH, self.map_hash)
        self.assertEqual(search_evidence['search_album_candidates'][0]['source_kind'], 'search_album')
        evidence_path = self.root / 'search-evidence.json'
        evidence_path.write_text(json.dumps(search_evidence), encoding='utf-8')

        plan = build_album_plan(self.tracks, LIBRARY_HASH, self.map_hash,
            {'0000000000000001'}, [], self.matcher, [evidence_path])
        validate_album_plan(plan, LIBRARY_HASH, self.map_hash)
        self.assertEqual([row['album_id'] for row in plan['albums']], [ALBUM_ID])
        self.assertEqual(plan['albums'][0]['source_inputs'][0]['source_kind'], 'search_album')
        self.assertEqual(plan['recommendation_edges_created'], 0)

    def test_catalog_rows_require_unique_complete_credit_duration_and_same_version(self):
        response = {'title': 'Album', 'tracks': [
            {'videoId': 'video000001', 'title': 'Local song two',
             'artists': [{'name': 'Artist'}], 'album': 'Album', 'duration_seconds': 100},
            {'videoId': 'video000002', 'title': 'Local song three',
             'artists': [{'name': 'Wrong artist'}], 'album': 'Album', 'duration_seconds': 100},
            {'videoId': 'video000003', 'title': 'Local song three',
             'artists': [{'name': 'Artist'}], 'album': 'Album', 'duration_seconds': 104},
            {'videoId': 'video000004', 'title': 'Local song three (Live)',
             'artists': [{'name': 'Artist'}], 'album': 'Album', 'duration_seconds': 100},
        ]}
        result = self.saved_response(response)
        evidence = build_album_catalog_evidence(self.plan, [result], self.tracks,
                                                 self.matcher, LIBRARY_HASH, self.map_hash)
        self.assertEqual([row['persistent_id'] for row in evidence['rows']],
                         ['0000000000000002'])
        observation, = evidence['catalog_observations'][:1]
        self.assertEqual(observation['relation'], 'album_catalog_candidate')
        self.assertEqual(observation['candidate_pids'], ['0000000000000002'])
        self.assertEqual(evidence['audit']['recommendation_edges_created'], 0)
        self.assertEqual(evidence['audit']['recommendation_observations_created'], 0)
        self.assertEqual(evidence['rows'][0]['source_album_id_inputs'],
                         self.plan['albums'][0]['source_inputs'])

    def test_ambiguous_video_mapping_is_omitted(self):
        duplicate = track('0000000000000004', 'Local song two')
        tracks = [*self.tracks, duplicate]
        matcher = IdentityMap(self.map_document | {'tracks': tracks}, tracks, LIBRARY_HASH)
        plan = build_album_plan(tracks, LIBRARY_HASH, self.map_hash,
            {'0000000000000001'}, [self.snapshot_path], matcher)
        response = {'title': 'Album', 'tracks': [
            {'videoId': 'video000009', 'title': 'Local song two',
             'artists': [{'name': 'Artist'}], 'album': 'Album', 'duration_seconds': 100}]}
        result = self.saved_response(response)
        evidence = build_album_catalog_evidence(plan, [result], tracks, matcher,
                                                 LIBRARY_HASH, self.map_hash)
        self.assertEqual(evidence['rows'], [])
        self.assertEqual(evidence['audit']['ambiguous_video_id_count'], 1)

    def test_catalog_call_uses_saved_browse_id_key(self):
        class Client:
            def __init__(self):
                self.calls = []
            def get_album(self, **kwargs):
                self.calls.append(kwargs)
                return {'title': 'Album', 'feedbackTokens': {'secret': 'drop'},
                        'tracks': [{'videoId': 'video000001', 'title': 'Track',
                                    'feedbackTokens': 'drop', 'duration_seconds': 100}]}
        client = Client()
        result = catalog_album(client, ALBUM_ID)
        self.assertEqual(client.calls, [{'browseId': ALBUM_ID}])
        self.assertNotIn('feedbackTokens', json.dumps(result))
        self.assertEqual(result['tracks'][0]['videoId'], 'video000001')

    def test_http_error_stops_without_retry_and_keeps_error_details_private(self):
        album_plan_path = self.root / 'album-plan.json'
        album_plan_path.write_text(json.dumps(self.plan), encoding='utf-8')
        state = make_synthetic_state(
            [{'root_pid': '0000000000000001', 'ordered_target_pids': []}],
            self.matcher, LIBRARY_HASH, self.map_hash)
        state_path = self.root / 'state.json'
        state_path.write_text(json.dumps(state), encoding='utf-8')
        map_path = self.root / 'identity-map.json'
        map_path.write_bytes(self.map_bytes)

        class Session:
            count = 0
            def close(self):
                pass

        session = Session()

        class Client:
            def __init__(self):
                self.album_calls = 0
            def get_account_info(self):
                session.count += 1
                return {'accountName': 'private-account'}
            def get_album(self, **kwargs):
                self.album_calls += 1
                session.count += 1
                raise RuntimeError('private response and auth data')

        client = Client()
        with patch('discover_album_catalog_seeds.load_library',
                   return_value=(self.tracks, LIBRARY_HASH, {})):
            report = run_discovery(album_plan_path=album_plan_path,
                track_snapshot=self.root / 'tracks.json', identity_map_path=map_path,
                output_dir=self.root / 'run', auth_path=self.root / 'auth.json',
                state_path=state_path, request_budget=10,
                client_factory=lambda *args, **kwargs: client,
                session_factory=lambda interval, budget: session)
        self.assertEqual(client.album_calls, 1)
        self.assertEqual(report['http_requests'], 2)
        self.assertEqual(report['status'], 'stopped')
        self.assertEqual(report['error_type'], 'RuntimeError')
        serialized = (self.root / 'run' / 'summary.json').read_text()
        self.assertNotIn('private response', serialized)
        self.assertNotIn('private-account', serialized)


if __name__ == '__main__':
    unittest.main()
