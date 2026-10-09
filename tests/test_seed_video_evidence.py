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
                                 select_seed_video_evidence)


LIBRARY_HASH = 'a' * 64
MAP_HASH = 'b' * 64
PID = '0000000000000001'
VIDEO = 'radio000001'


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


if __name__ == '__main__':
    unittest.main()
