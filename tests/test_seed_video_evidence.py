"""Offline safety checks for saved radio-video seed evidence."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_identity_map import IdentityMap, build_map
from probe_ytmusic_batch import main as batch_main
from seed_video_evidence import (PRODUCER, build_seed_video_evidence,
                                 combine_seed_video_evidence,
                                 select_seed_video_evidence,
                                 validate_album_catalog_evidence)


LIBRARY_HASH = 'a' * 64
MAP_HASH = 'b' * 64
PID = '0000000000000001'
VIDEO = 'radio000001'
CATALOG_VIDEO = 'catalog0001'
ALBUM_ID = 'MPREsavedalbum01'


def track(pid=PID, title='Song'):
    return {'persistent_id': pid, 'title': title, 'artist': 'Artist',
            'album': 'Album', 'duration_ms': 100000}


def snapshot(video=VIDEO, *, library_hash=LIBRARY_HASH,
             map_hash=MAP_HASH, title='Song', artists=None,
             duration=100, relation='radio'):
    return {
        'schema_version': 1, 'selected_video_id': 'seedvideo01',
        'library_sha256': library_hash, 'identity_map_sha256': map_hash,
        'observations': [{
            'source': 'ytmusic', 'relation': relation, 'is_seed': False,
            'position': 1, 'observed_at': '2026-10-09T00:00:00+00:00',
            'track': {'video_id': video, 'title': title,
                      'artists': artists if artists is not None else [{'name': 'Artist'}],
                      'album': {'name': 'Album'}, 'duration_seconds': duration,
                      'length': '1:40'},
        }, {
            'source': 'ytmusic', 'relation': 'radio', 'is_seed': True,
            'position': 2, 'observed_at': '2026-10-09T00:00:01+00:00',
            'track': {'video_id': 'seedvideo01', 'title': 'Unmatched seed',
                      'artists': [{'name': 'Other Artist'}], 'album': None,
                      'duration_seconds': 100, 'length': '1:40'},
        }],
    }


class PartialMatcher:
    def __init__(self, tracks):
        self.tracks = tracks

    def match(self, remote):
        return [{'persistent_id': PID, 'artist_credit_match': 'partial',
                 'local_metadata': {'title': 'Song'}}]


class SeedVideoEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tracks = [track()]
        self.matcher = IdentityMap(build_map(self.tracks, LIBRARY_HASH),
                                   self.tracks, LIBRARY_HASH)
        self.source_path = self.root / 'radio.json'
        self.write_snapshot(snapshot())
        self.document = build_seed_video_evidence(
            [self.source_path], self.matcher, LIBRARY_HASH, MAP_HASH)
        self.sidecar_path = self.root / 'evidence.json'
        self.save_document(self.document)
        self.seeds = [self.tracks[0]]

    def tearDown(self):
        self.temp.cleanup()

    def write_snapshot(self, document):
        self.source_path.write_text(json.dumps(document), encoding='utf-8')

    def save_document(self, document):
        self.sidecar_path.write_text(json.dumps(document), encoding='utf-8')

    def select(self, document=None, matcher=None, tracks=None):
        return select_seed_video_evidence(
            self.document if document is None else document,
            self.sidecar_path, self.seeds,
            self.tracks if tracks is None else tracks,
            self.matcher if matcher is None else matcher,
            LIBRARY_HASH, MAP_HASH)

    def test_builds_and_revalidates_a_radio_seed_hint_with_source_provenance(self):
        row, = self.document['rows']
        self.assertEqual(row['persistent_id'], PID)
        self.assertEqual(row['video_id'], VIDEO)
        self.assertEqual(row['source_relation'], 'radio')
        self.assertEqual(row['input_sha256'], hashlib.sha256(self.source_path.read_bytes()).hexdigest())
        hint = self.select()[PID]
        self.assertEqual(hint['video_id'], VIDEO)
        self.assertEqual(hint['source'], 'saved_recommendation_metadata_candidate_unverified')
        self.assertEqual(self.document['audit']['edges_created'], 0)

    def test_rejects_foreign_library_or_identity_map_hash(self):
        for field in ('library_sha256', 'identity_map_sha256'):
            altered = copy.deepcopy(self.document)
            altered[field] = 'c' * 64
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'source hashes'):
                self.select(altered)

    def test_rejects_unknown_pid_and_invalid_video_id(self):
        altered = copy.deepcopy(self.document)
        altered['rows'][0]['persistent_id'] = 'FFFFFFFFFFFFFFFF'
        with self.assertRaisesRegex(ValueError, 'Invalid or duplicate'):
            self.select(altered)
        altered = copy.deepcopy(self.document)
        altered['rows'][0]['video_id'] = 'bad-video'
        with self.assertRaisesRegex(ValueError, 'Invalid or duplicate'):
            self.select(altered)

    def test_rejects_malformed_source_hash_or_changed_source_file(self):
        altered = copy.deepcopy(self.document)
        altered['rows'][0]['input_sha256'] = 'bad'
        with self.assertRaisesRegex(ValueError, 'Invalid or duplicate'):
            self.select(altered)
        self.write_snapshot(snapshot(title='Changed source'))
        with self.assertRaisesRegex(ValueError, 'snapshot hash changed'):
            self.select()

    def test_builder_omits_nonunique_duration_supported_candidates(self):
        duplicate_tracks = [track(), track('0000000000000002')]
        matcher = IdentityMap(build_map(duplicate_tracks, LIBRARY_HASH),
                              duplicate_tracks, LIBRARY_HASH)
        result = build_seed_video_evidence(
            [self.source_path], matcher, LIBRARY_HASH, MAP_HASH)
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['audit']['edges_created'], 0)

    def test_builder_omits_partial_artist_credit_matches(self):
        result = build_seed_video_evidence(
            [self.source_path], PartialMatcher(self.tracks), LIBRARY_HASH, MAP_HASH)
        self.assertEqual(result['rows'], [])

    def test_builder_keeps_version_markers_as_a_match_guard(self):
        self.write_snapshot(snapshot(title='Song (Instrumental)'))
        result = build_seed_video_evidence(
            [self.source_path], self.matcher, LIBRARY_HASH, MAP_HASH)
        self.assertEqual(result['rows'], [])

    def test_loader_rejects_a_source_that_is_not_saved_radio_evidence(self):
        changed = snapshot(relation='related')
        self.write_snapshot(changed)
        altered = copy.deepcopy(self.document)
        altered['rows'][0]['input_sha256'] = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'no longer has'):
            self.select(altered)

    def test_batch_cli_uses_hint_and_records_evidence_without_search(self):
        identity_path = self.root / 'identity-map.json'
        identity_path.write_text(json.dumps(build_map(self.tracks, LIBRARY_HASH)), encoding='utf-8')
        actual_map_hash = hashlib.sha256(identity_path.read_bytes()).hexdigest()
        source_doc = snapshot(map_hash=actual_map_hash)
        self.write_snapshot(source_doc)
        evidence = build_seed_video_evidence([self.source_path], self.matcher,
                                             LIBRARY_HASH, actual_map_hash)
        evidence_path = self.root / 'batch-evidence.json'
        evidence_path.write_text(json.dumps(evidence), encoding='utf-8')
        tracks_path = self.root / 'tracks.json'
        tracks_path.write_text(json.dumps({'source_sha256': LIBRARY_HASH,
                                           'tracks': self.tracks}), encoding='utf-8')
        plan_path = self.root / 'plan.json'
        plan_path.write_text(json.dumps({
            'schema_version': 1, 'source_library_sha256': LIBRARY_HASH,
            'seeds': [{'persistent_id': PID, 'series': 'fixture',
                       'sample_kind': 'indexed_target_only'}],
        }), encoding='utf-8')
        output = self.root / 'collector-plan'
        argv = ['probe', '--track-snapshot', str(tracks_path),
                '--identity-map', str(identity_path), '--seed-plan', str(plan_path),
                '--seed-video-evidence', str(evidence_path), '--seeds', '1',
                '--output', str(output), '--auth', str(self.root / 'missing-auth'),
                '--plan-only']
        with patch('sys.argv', argv), patch('sys.stdout', new=io.StringIO()):
            self.assertEqual(batch_main(), 0)
        manifest = json.loads((output / 'seeds.json').read_text())
        self.assertEqual(manifest['seeds'][0]['seed_video_id'], VIDEO)
        self.assertEqual(manifest['seeds'][0]['seed_video_source'],
                         'saved_recommendation_metadata_candidate_unverified')
        self.assertEqual(manifest['seed_video_evidence_sha256'],
                         hashlib.sha256(evidence_path.read_bytes()).hexdigest())
        self.assertEqual(manifest['seed_video_evidence_sources'][0]['input_path'],
                         str(self.source_path.resolve()))

    def catalog_fixture(self, *, persistent_id='0000000000000002', video=CATALOG_VIDEO,
                        duration=100, album='Album', relation='radio'):
        tracks = [track(), track('0000000000000002', 'Catalog song')]
        matcher = IdentityMap(build_map(tracks, LIBRARY_HASH), tracks, LIBRARY_HASH)
        source = snapshot(map_hash=MAP_HASH)
        source['observations'][0]['track']['album']['id'] = ALBUM_ID
        source['observations'][0]['track']['album']['name'] = 'Album'
        source['observations'][0]['relation'] = relation
        source_path = self.root / 'album-id-source.json'
        source_path.write_text(json.dumps(source), encoding='utf-8')
        source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        response = {'title': album, 'tracks': [{
            'videoId': video, 'title': tracks[1 if persistent_id != PID else 0]['title'],
            'artists': [{'name': 'Artist'}], 'album': album,
            'duration_seconds': duration, 'length': '1:40'}]}
        response_path = self.root / 'album-response.json'
        response_path.write_text(json.dumps({
            'endpoint': 'get_album', 'arguments': {'browseId': ALBUM_ID},
            'observed_at': '2026-10-09T00:00:00+00:00', 'response': response}),
            encoding='utf-8')
        response_sha = hashlib.sha256(response_path.read_bytes()).hexdigest()
        row = {'persistent_id': persistent_id, 'video_id': video,
            'input_path': str(response_path.resolve()), 'input_sha256': response_sha,
            'source_relation': 'album_catalog_candidate', 'album_id': ALBUM_ID,
            'source_key': {'endpoint': 'get_album', 'argument_key': 'browseId',
                           'argument_value': ALBUM_ID, 'track_position': 1},
            'source_album_id_inputs': [{
                'snapshot_path': str(source_path.resolve()), 'snapshot_sha256': source_sha,
                'observation_index': 0, 'source_key': 'observations[0].track.album.id',
                'source_video_id': source['observations'][0]['track']['video_id']}]}
        catalog = {'schema_version': 1,
            'producer': 'opengenius-album-catalog-seed-evidence-v1',
            'library_sha256': LIBRARY_HASH, 'identity_map_sha256': MAP_HASH,
            'source_plan_producer': 'opengenius-saved-radio-album-plan-v1',
            'album_plan_sha256': 'c' * 64, 'rows': [row],
            'catalog_observations': [{'relation': 'album_catalog_candidate'}],
            'audit': {'recommendation_edges_created': 0,
                      'recommendation_observations_created': 0}}
        catalog_path = self.root / 'catalog-evidence.json'
        catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
        return tracks, matcher, catalog, catalog_path

    def test_album_catalog_hint_revalidates_response_and_saved_radio_browse_id(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture()
        validated = validate_album_catalog_evidence(
            catalog, catalog_path, tracks, matcher, LIBRARY_HASH, MAP_HASH)
        self.assertEqual(validated[0]['persistent_id'], '0000000000000002')
        radio_doc = build_seed_video_evidence(
            [self.source_path], matcher, LIBRARY_HASH, MAP_HASH)
        combined = combine_seed_video_evidence(
            radio_doc, self.sidecar_path, catalog, catalog_path,
            tracks, matcher, LIBRARY_HASH, MAP_HASH)
        combined_path = self.root / 'combined.json'
        combined_path.write_text(json.dumps(combined), encoding='utf-8')
        hints = select_seed_video_evidence(
            combined, combined_path, tracks, tracks, matcher, LIBRARY_HASH, MAP_HASH)
        self.assertEqual(hints['0000000000000002']['video_id'], CATALOG_VIDEO)
        self.assertEqual(hints['0000000000000002']['source_relation'], 'album_catalog_candidate')
        self.assertEqual(hints['0000000000000002']['source'],
                         'saved_album_catalog_metadata_candidate_unverified')

    def test_album_catalog_hint_accepts_separately_typed_saved_search_provenance(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture()
        query = 'Album Artist'
        search_response = [{'browseId': ALBUM_ID, 'title': 'Album',
                            'artists': [{'name': 'Artist'}]}]
        response_path = self.root / 'saved-search.json'
        response_path.write_text(json.dumps({
            'endpoint': 'search',
            'arguments': {'query': query, 'filter': 'albums', 'limit': 5},
            'observed_at': '2026-10-09T00:00:00+00:00',
            'response': search_response}), encoding='utf-8')
        response_sha = hashlib.sha256(response_path.read_bytes()).hexdigest()
        credits = sorted([list(credit) for credit in matcher.artist_credits('Artist')])
        source = {'source_kind': 'search_album', 'evidence_path': 'search-evidence.json',
            'evidence_sha256': '0' * 64, 'response_path': str(response_path),
            'response_sha256': response_sha, 'source_key': 'response[0].browseId',
            'arguments': {'query': query, 'filter': 'albums', 'limit': 5},
            'result_position': 0}
        evidence = {'schema_version': 1,
            'producer': 'opengenius-unindexed-album-search-evidence-v1',
            'library_sha256': LIBRARY_HASH, 'identity_map_sha256': MAP_HASH,
            'search_album_candidates': [{
                'source_kind': 'search_album', 'browse_id': ALBUM_ID,
                'album_title': 'Album', 'canonical_artists': credits,
                'candidate_pids': ['0000000000000002'],
                'source': {'response_sha256': response_sha, 'result_position': 0,
                           'arguments': source['arguments']}}]}
        evidence_path = self.root / 'search-evidence.json'
        evidence_path.write_text(json.dumps(evidence), encoding='utf-8')
        source['evidence_sha256'] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        catalog['rows'][0]['source_album_id_inputs'] = [source]
        catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
        validated = validate_album_catalog_evidence(
            catalog, catalog_path, tracks, matcher, LIBRARY_HASH, MAP_HASH)
        self.assertEqual(validated[0]['persistent_id'], '0000000000000002')

    def test_album_catalog_rejects_search_result_browse_id_mismatch(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture()
        response_path = self.root / 'saved-search-mismatch.json'
        response_path.write_text(json.dumps({
            'endpoint': 'search',
            'arguments': {'query': 'Album Artist', 'filter': 'albums', 'limit': 5},
            'observed_at': '2026-10-09T00:00:00+00:00',
            'response': [{'browseId': 'MPREanotheralbum', 'title': 'Album',
                          'artists': [{'name': 'Artist'}]}]}), encoding='utf-8')
        response_sha = hashlib.sha256(response_path.read_bytes()).hexdigest()
        evidence = {'schema_version': 1,
            'producer': 'opengenius-unindexed-album-search-evidence-v1',
            'library_sha256': LIBRARY_HASH, 'identity_map_sha256': MAP_HASH,
            'search_album_candidates': []}
        evidence_path = self.root / 'search-evidence-mismatch.json'
        evidence_path.write_text(json.dumps(evidence), encoding='utf-8')
        catalog['rows'][0]['source_album_id_inputs'] = [{
            'source_kind': 'search_album', 'evidence_path': str(evidence_path),
            'evidence_sha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            'response_path': str(response_path), 'response_sha256': response_sha,
            'source_key': 'response[0].browseId',
            'arguments': {'query': 'Album Artist', 'filter': 'albums', 'limit': 5},
            'result_position': 0}]
        catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'browse ID'):
            validate_album_catalog_evidence(
                catalog, catalog_path, tracks, matcher, LIBRARY_HASH, MAP_HASH)

    def test_album_catalog_loader_rejects_duration_and_saved_album_source_mismatches(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture(duration=104)
        with self.assertRaisesRegex(ValueError, 'complete-credit duration'):
            validate_album_catalog_evidence(catalog, catalog_path, tracks, matcher,
                                            LIBRARY_HASH, MAP_HASH)
        tracks, matcher, catalog, catalog_path = self.catalog_fixture(relation='related')
        with self.assertRaisesRegex(ValueError, 'not read from the saved radio'):
            validate_album_catalog_evidence(catalog, catalog_path, tracks, matcher,
                                            LIBRARY_HASH, MAP_HASH)

    def test_album_catalog_loader_rejects_unsanitized_response_fields(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture()
        row = catalog['rows'][0]
        response_path = Path(row['input_path'])
        saved = json.loads(response_path.read_text())
        saved['response']['feedbackTokens'] = {'private': 'value'}
        response_path.write_text(json.dumps(saved), encoding='utf-8')
        row['input_sha256'] = hashlib.sha256(response_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'outside its safe metadata schema'):
            validate_album_catalog_evidence(catalog, catalog_path, tracks, matcher,
                                            LIBRARY_HASH, MAP_HASH)

    def test_combiner_rejects_conflicting_pid_video_hints(self):
        tracks, matcher, catalog, catalog_path = self.catalog_fixture(
            persistent_id=PID, video='catalog0002')
        radio_doc = build_seed_video_evidence(
            [self.source_path], matcher, LIBRARY_HASH, MAP_HASH)
        with self.assertRaisesRegex(ValueError, 'conflict for one PID'):
            combine_seed_video_evidence(radio_doc, self.sidecar_path,
                catalog, catalog_path, tracks, matcher, LIBRARY_HASH, MAP_HASH)


if __name__ == '__main__':
    unittest.main()
