"""Authenticated radio-only source proof rejects unsupported relations and tampering."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from music_identity_map import IdentityMap, build_map
from probe_ytmusic import observe
from probe_ytmusic_batch import summary as collector_result_summary
from evaluate_ytmusic_batch import collect_graphs
from radio_snapshot_proof import (create_radio_snapshot_proof,
                                  RadioSnapshotIneligible,
                                  radio_only_snapshot_from_proof,
                                  verify_radio_snapshot_proof)
from radio_source_integration import prepare_radio_only_snapshots


LIBRARY = 'a' * 64
ROOT, TARGET = '0000000000000001', '0000000000000002'


class RadioSnapshotProofTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tracks = [
            {'persistent_id': ROOT, 'title': 'Seed Song', 'artist': 'Seed Artist',
             'album': 'Seed Album', 'duration_ms': 100000},
            {'persistent_id': TARGET, 'title': 'Target Song', 'artist': 'Target Artist',
             'album': 'Target Album', 'duration_ms': 120000},
        ]
        self.identity = build_map(self.tracks, LIBRARY)
        self.identity_path = self.write('identity.json', self.identity)
        self.track_path = self.write('tracks.json', {'fixture': True})
        self.map_hash = self.digest(self.identity_path)
        self.observed_at = '2026-10-10T12:00:00+00:00'
        self.arguments = {'videoId': 'abcdefghijk', 'radio': True, 'limit': 100}
        self.raw_tracks = [
            {'videoId': 'abcdefghijk', 'title': 'Seed Song', 'length': '1:40',
             'artists': [{'name': 'Seed Artist', 'id': 'UCseed'}],
             'album': {'name': 'Seed Album'}, 'videoType': 'MUSIC_VIDEO_TYPE_ATV'},
            {'videoId': 'lmnopqrstuv', 'title': 'Target Song', 'length': '2:00',
             'artists': [{'name': 'Target Artist', 'id': 'UCtarget'}],
             'album': {'name': 'Target Album'}, 'videoType': 'MUSIC_VIDEO_TYPE_ATV'},
        ]
        self.matcher = IdentityMap(self.identity, self.tracks, LIBRARY)
        self.snapshot_path = self.write('snapshot.json', self.snapshot())
        snapshot = json.loads(self.snapshot_path.read_text())
        self.summary_path = self.write('summary.json', {
            'schema_version': 1, 'account_authentication_verified': True,
            'status': 'completed', 'http_requests': 2, 'request_budget': 10,
            'library_sha256': LIBRARY,
            'results': [{**collector_result_summary(snapshot), 'status': 'collected'}],
        })
        self.cache_path = self.write('radio-cache.json', {
            'endpoint': 'get_watch_playlist', 'arguments': self.arguments,
            'observed_at': self.observed_at, 'response': {'tracks': self.raw_tracks},
        })
        self.seed_plan_path = self.write('seed-plan.json', {
            'source_library_sha256': LIBRARY,
            'seeds': [{'persistent_id': ROOT}],
        })
        self.seed_evidence_path = self.write('seed-video-evidence.json', {
            'schema_version': 1, 'producer': 'fixture',
            'library_sha256': LIBRARY, 'identity_map_sha256': self.map_hash,
            'rows': [{'persistent_id': ROOT, 'video_id': self.arguments['videoId']}],
        })
        self.manifest_path = self.write('seeds.json', {
            'library_sha256': LIBRARY, 'identity_map_sha256': self.map_hash,
            'seed_plan_sha256': self.digest(self.seed_plan_path),
            'seed_video_evidence_sha256': self.digest(self.seed_evidence_path),
            'seeds': [json.loads(self.snapshot_path.read_text())['local_seed']],
        })
        self.intent_path = self.write('intent.json', {
            'library_sha256': LIBRARY, 'identity_map_sha256': self.map_hash,
            'seed_pids': [ROOT], 'source_inputs': [],
            'runner_config': {
                'track_snapshot': {'path': str(self.track_path.resolve()),
                                   'sha256': self.digest(self.track_path)},
                'identity_map': {'path': str(self.identity_path.resolve()),
                                 'sha256': self.map_hash},
                'candidate_limit': 100,
            },
            'seed_plan': {'path': str(self.seed_plan_path.resolve()),
                          'sha256': self.digest(self.seed_plan_path)},
            'seed_video_evidence': {'path': str(self.seed_evidence_path.resolve()),
                                    'sha256': self.digest(self.seed_evidence_path)},
        })

    def tearDown(self):
        self.temporary.cleanup()

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def snapshot(self):
        observations = observe(self.raw_tracks, 'radio', self.observed_at,
                               self.tracks, self.arguments['videoId'],
                               candidate_matcher=self.matcher.match)
        return {
            'schema_version': 1, 'account_authentication_verified': True,
            'library_sha256': LIBRARY, 'identity_map_sha256': self.map_hash,
            'local_seed': {'persistent_id': ROOT, 'title': 'Seed Song',
                           'artist': 'Seed Artist', 'album': 'Seed Album'},
            'selected_video_id': self.arguments['videoId'],
            'request_evidence': [{'endpoint': 'get_watch_playlist',
                                  'arguments': self.arguments,
                                  'observed_at': self.observed_at, 'cache_hit': False}],
            'requests': [{'endpoint': 'watch_radio', 'observed_at': self.observed_at,
                          'returned_track_count': len(self.raw_tracks)},
                         {'endpoint': 'song_related', 'status': 'parser_error'}],
            'related_status': 'parser_error', 'observations': observations,
        }

    def create(self, output):
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            return create_radio_snapshot_proof(
                track_snapshot_path=self.track_path, identity_map_path=self.identity_path,
                snapshot_path=self.snapshot_path, collector_summary_path=self.summary_path,
                collector_seed_manifest_path=self.manifest_path,
                batch_intent_path=self.intent_path,
                raw_radio_cache_path=self.cache_path, output_path=output,
                sealed_at_utc='2026-10-10T12:10:00+00:00')

    def test_radio_only_proof_accepts_parser_error_when_raw_radio_replays(self):
        output = self.root / 'proof.json'
        proof = self.create(output)
        self.assertEqual(proof['policy'], 'authenticated-watch-radio-only-v1')
        self.assertEqual(proof['raw_cache_hash_sealing'],
                         'hash captured when this proof was created; not claimed to be captured at API request time')
        self.assertFalse(proof['related_endpoint_used_for_eligibility'])
        self.assertEqual(proof['outgoing_target_pids'], [TARGET])
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            verified = verify_radio_snapshot_proof(output)
        self.assertTrue(verified['verified'])
        self.assertEqual(verified['persistent_id'], ROOT)
        self.assertEqual(verified['outgoing_edge_count'], 1)
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            sanitized = radio_only_snapshot_from_proof(output)
        self.assertEqual({row['relation'] for row in sanitized['observations']}, {'radio'})

    def test_rejects_tampered_raw_cache_after_first_seal(self):
        output = self.root / 'proof.json'
        self.create(output)
        raw = json.loads(self.cache_path.read_text())
        raw['response']['tracks'][1]['title'] = 'Different Song'
        self.cache_path.write_text(json.dumps(raw), encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            with self.assertRaisesRegex(ValueError, 'does not bind|does not reproduce|differs'):
                verify_radio_snapshot_proof(output)

    def test_rejects_false_radio_argument_and_cached_call(self):
        output = self.root / 'proof.json'
        snapshot = json.loads(self.snapshot_path.read_text())
        snapshot['request_evidence'][0]['arguments']['radio'] = False
        self.snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            with self.assertRaisesRegex(ValueError, 'radio=true watch request'):
                self.create(output)

        snapshot = self.snapshot()
        snapshot['request_evidence'][0]['cache_hit'] = True
        self.snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')
        # Refresh the summary so the failure reaches the request-evidence gate.
        summary = json.loads(self.summary_path.read_text())
        summary['results'][0] = {**collector_result_summary(snapshot), 'status': 'collected'}
        self.summary_path.write_text(json.dumps(summary), encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            with self.assertRaisesRegex(ValueError, 'Radio request'):
                self.create(self.root / 'cached-proof.json')

    def test_rejects_wrong_radio_video_limit_timestamp_and_response_count(self):
        cases = (
            ('videoId', lambda snapshot: snapshot['request_evidence'][0]['arguments'].__setitem__(
                'videoId', 'NOT_THE_SEED_VIDEO')),
            ('limit', lambda snapshot: snapshot['request_evidence'][0]['arguments'].__setitem__(
                'limit', 50)),
            ('extra argument', lambda snapshot: snapshot['request_evidence'][0]['arguments'].__setitem__(
                'visitorData', 'unexpected')),
            ('timestamp', lambda snapshot: snapshot['request_evidence'][0].__setitem__(
                'observed_at', '2026-10-10T12:11:00+00:00')),
            ('response count', lambda snapshot: snapshot['requests'][0].__setitem__(
                'returned_track_count', len(self.raw_tracks) + 1)),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                snapshot = self.snapshot()
                mutate(snapshot)
                self.snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')
                output = self.root / ('invalid-' + label.replace(' ', '-') + '.json')
                with patch('radio_snapshot_proof.load_library',
                           return_value=(self.tracks, LIBRARY, None)), \
                        patch('radio_snapshot_proof.select_seed_video_evidence',
                              return_value={ROOT: {'video_id': self.arguments['videoId']}}):
                    with self.assertRaises(ValueError):
                        create_radio_snapshot_proof(
                            track_snapshot_path=self.track_path,
                            identity_map_path=self.identity_path,
                            snapshot_path=self.snapshot_path,
                            collector_summary_path=self.summary_path,
                            collector_seed_manifest_path=self.manifest_path,
                            batch_intent_path=self.intent_path,
                            raw_radio_cache_path=self.cache_path,
                            output_path=output,
                            sealed_at_utc='2026-10-10T12:10:00+00:00')

    def test_rejects_snapshot_relabel_or_wrong_seed(self):
        output = self.root / 'proof.json'
        snapshot = self.snapshot()
        snapshot['observations'][1]['relation'] = 'related'
        self.snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')
        snapshot = self.snapshot()
        snapshot['observations'][0]['track']['title'] = 'Changed Seed'
        self.snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            with self.assertRaisesRegex(ValueError, 'does not reproduce'):
                self.create(output)

        snapshot = self.snapshot()
        snapshot['local_seed']['persistent_id'] = TARGET
        self.snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')
        summary = json.loads(self.summary_path.read_text())
        summary['results'][0]['persistent_id'] = TARGET
        self.summary_path.write_text(json.dumps(summary), encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={TARGET: {'video_id': self.arguments['videoId']}}):
            with self.assertRaisesRegex(ValueError, 'seed manifest'):
                self.create(output)

    def test_rejects_partial_credit_and_no_strict_unique_targets(self):
        output = self.root / 'proof.json'
        self.raw_tracks[1]['artists'] = [
            {'name': 'Target Artist', 'id': 'UCtarget'},
            {'name': 'Unmapped Collaborator', 'id': 'UCother'},
        ]
        self.cache_path = self.write('partial-radio-cache.json', {
            'endpoint': 'get_watch_playlist', 'arguments': self.arguments,
            'observed_at': self.observed_at, 'response': {'tracks': self.raw_tracks},
        })
        self.snapshot_path = self.write('partial-snapshot.json', self.snapshot())
        summary = json.loads(self.summary_path.read_text())
        summary['results'][0] = {**collector_result_summary(json.loads(self.snapshot_path.read_text())), 'status': 'collected'}
        self.summary_path.write_text(json.dumps(summary), encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            with self.assertRaisesRegex(ValueError, 'no_strict_unique_outgoing_targets'):
                self.create(output)

    def test_rejects_album_version_and_forced_ambiguous_pid_targets(self):
        cases = (
            ('album', lambda rows: rows[1]['album'].__setitem__('name', 'Wrong Album')),
            ('version', lambda rows: rows[1].__setitem__('title', 'Target Song - Live')),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                rows = copy.deepcopy(self.raw_tracks)
                mutate(rows)
                self.cache_path = self.write(label + '-cache.json', {
                    'endpoint': 'get_watch_playlist', 'arguments': self.arguments,
                    'observed_at': self.observed_at, 'response': {'tracks': rows},
                })
                self.raw_tracks = rows
                self.snapshot_path = self.write(label + '-snapshot.json', self.snapshot())
                summary = json.loads(self.summary_path.read_text())
                summary['results'][0] = {
                    **collector_result_summary(json.loads(self.snapshot_path.read_text())),
                    'status': 'collected'}
                self.summary_path.write_text(json.dumps(summary), encoding='utf-8')
                with patch('radio_snapshot_proof.load_library',
                           return_value=(self.tracks, LIBRARY, None)), \
                        patch('radio_snapshot_proof.select_seed_video_evidence',
                              return_value={ROOT: {'video_id': self.arguments['videoId']}}):
                    with self.assertRaisesRegex(ValueError, 'no_strict_unique_outgoing_targets'):
                        self.create(self.root / (label + '-rejected-proof.json'))

        # Two individually strict matches for one video ID must remain
        # ambiguous rather than resolving to whichever local PID appears first.
        third = {'persistent_id': '0000000000000003', 'title': 'Other Song',
                 'artist': 'Other Artist', 'album': 'Other Album', 'duration_ms': 60000}
        self.tracks = [*self.tracks, third]
        self.identity = build_map(self.tracks, LIBRARY)
        self.identity_path = self.write('ambiguous-identity.json', self.identity)
        self.map_hash = self.digest(self.identity_path)
        self.matcher = IdentityMap(self.identity, self.tracks, LIBRARY)
        self.track_path = self.write('ambiguous-track-snapshot.json', {
            'source_sha256': LIBRARY, 'tracks': self.tracks,
        })
        evidence = json.loads(self.seed_evidence_path.read_text())
        evidence['identity_map_sha256'] = self.map_hash
        self.seed_evidence_path.write_text(json.dumps(evidence), encoding='utf-8')
        plan_hash = self.digest(self.seed_plan_path)
        evidence_hash = self.digest(self.seed_evidence_path)
        manifest = json.loads(self.manifest_path.read_text())
        manifest.update(identity_map_sha256=self.map_hash,
                        seed_plan_sha256=plan_hash,
                        seed_video_evidence_sha256=evidence_hash)
        self.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        intent = json.loads(self.intent_path.read_text())
        intent.update(identity_map_sha256=self.map_hash)
        intent['runner_config'].update(
            track_snapshot={'path': str(self.track_path.resolve()),
                            'sha256': self.digest(self.track_path)},
            identity_map={'path': str(self.identity_path.resolve()),
                          'sha256': self.map_hash})
        intent['seed_video_evidence'] = {
            'path': str(self.seed_evidence_path.resolve()), 'sha256': evidence_hash}
        self.intent_path.write_text(json.dumps(intent), encoding='utf-8')
        rows = copy.deepcopy(self.raw_tracks)
        rows[1]['videoId'] = 'sharevid001'
        rows.append({'videoId': 'sharevid001', 'title': 'Other Song', 'length': '1:00',
                     'artists': [{'name': 'Other Artist', 'id': 'UCother'}],
                     'album': {'name': 'Other Album'},
                     'videoType': 'MUSIC_VIDEO_TYPE_ATV'})
        self.arguments['videoId'] = 'abcdefghijk'
        self.raw_tracks = rows
        self.cache_path = self.write('ambiguous-cache.json', {
            'endpoint': 'get_watch_playlist', 'arguments': self.arguments,
            'observed_at': self.observed_at, 'response': {'tracks': rows},
        })
        self.snapshot_path = self.write('ambiguous-snapshot.json', self.snapshot())
        summary = json.loads(self.summary_path.read_text())
        summary['results'][0] = {
            **collector_result_summary(json.loads(self.snapshot_path.read_text())),
            'status': 'collected'}
        self.summary_path.write_text(json.dumps(summary), encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            with self.assertRaisesRegex(ValueError, 'no_strict_unique_outgoing_targets'):
                self.create(self.root / 'ambiguous-pid-proof.json')

    def test_rejects_foreign_identity_map(self):
        snapshot = self.snapshot()
        self.snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')
        foreign = json.loads(self.identity_path.read_text())
        foreign['recording_links'] = [{'persistent_ids': [ROOT, TARGET],
                                       'status': 'confirmed'}]
        self.identity_path.write_text(json.dumps(foreign), encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            with self.assertRaisesRegex(ValueError, 'provenance do not agree'):
                self.create(self.root / 'foreign-map-proof.json')

    def test_seed_album_failure_is_ineligible_without_edge_proof(self):
        rows = copy.deepcopy(self.raw_tracks)
        rows[0]['album']['name'] = 'Wrong Seed Album'
        self.raw_tracks = rows
        self.cache_path = self.write('seed-album-cache.json', {
            'endpoint': 'get_watch_playlist', 'arguments': self.arguments,
            'observed_at': self.observed_at, 'response': {'tracks': rows},
        })
        self.snapshot_path = self.write('seed-album-snapshot.json', self.snapshot())
        summary = json.loads(self.summary_path.read_text())
        summary['results'][0] = {
            **collector_result_summary(json.loads(self.snapshot_path.read_text())),
            'status': 'collected'}
        self.summary_path.write_text(json.dumps(summary), encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            with self.assertRaises(RadioSnapshotIneligible) as caught:
                self.create(self.root / 'seed-album-ineligible-proof.json')
        self.assertEqual(caught.exception.reason, 'seed_not_strictly_matched')
        self.assertEqual(caught.exception.details['selected_video_id'],
                         self.arguments['videoId'])
        self.assertEqual(caught.exception.details['candidate_pids'], [ROOT])
        self.assertFalse(caught.exception.details['candidate_dispositions'][0]['album_match'])

    def test_merge_consumer_requires_and_replays_typed_source_proof(self):
        proof_path = self.root / 'proof.json'
        proof = self.create(proof_path)
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            sanitized = radio_only_snapshot_from_proof(proof_path)
        snapshot_path = self.write('radio-only-snapshot.json', sanitized)
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            graphs, skipped, inputs = collect_graphs(
                [snapshot_path], self.matcher, LIBRARY,
                source_policy='authenticated-watch-radio-only-v1',
                identity_map_sha256=self.map_hash)
        self.assertEqual(skipped, [])
        self.assertEqual(graphs[0]['root_pid'], ROOT)
        self.assertEqual(graphs[0]['ordered_target_pids'], proof['outgoing_target_pids'])
        self.assertEqual(inputs[0]['radio_only_source_proof']['sha256'], self.digest(proof_path))

        tampered = copy.deepcopy(sanitized)
        tampered['observations'] = []
        snapshot_path.write_text(json.dumps(tampered), encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            with self.assertRaisesRegex(ValueError, 'No valid recommendation snapshots'):
                collect_graphs(
                    [snapshot_path], self.matcher, LIBRARY,
                    source_policy='authenticated-watch-radio-only-v1',
                    identity_map_sha256=self.map_hash)

    def test_new_consumer_policy_rejects_an_ordinary_authenticated_envelope(self):
        path = self.write('ordinary-snapshot.json', self.snapshot())
        with self.assertRaisesRegex(ValueError, 'No valid recommendation snapshots'):
            collect_graphs(
                [path], self.matcher, LIBRARY,
                source_policy='authenticated-watch-radio-only-v1',
                identity_map_sha256=self.map_hash)

    def test_materializer_preserves_original_and_replays_proof_sidecars(self):
        batch = self.root / 'materialized-batch'
        collector = batch / 'collector'
        (collector / 'requests').mkdir(parents=True)
        snapshot_path = collector / (ROOT + '.json')
        summary_path = collector / 'summary-1.json'
        manifest_path = collector / 'seeds.json'
        for source, target in (
                (self.snapshot_path, snapshot_path),
                (self.summary_path, summary_path),
                (self.manifest_path, manifest_path),
                (self.intent_path, batch / 'intent.json')):
            shutil.copyfile(source, target)
        cache_key = hashlib.sha256(json.dumps(
            ['get_watch_playlist', self.arguments], sort_keys=True,
            ensure_ascii=False).encode('utf-8')).hexdigest()
        cache_path = collector / 'requests' / (cache_key + '.json')
        shutil.copyfile(self.cache_path, cache_path)
        output_dir = batch / 'radio-only'
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            result = prepare_radio_only_snapshots(
                track_snapshot_path=self.track_path,
                identity_map_path=self.identity_path,
                batch_dir=batch,
                collector_summary_path=summary_path,
                collector_seed_manifest_path=manifest_path,
                snapshot_paths=[snapshot_path], output_dir=output_dir)
            replayed = prepare_radio_only_snapshots(
                track_snapshot_path=self.track_path,
                identity_map_path=self.identity_path,
                batch_dir=batch,
                collector_summary_path=summary_path,
                collector_seed_manifest_path=manifest_path,
                snapshot_paths=[snapshot_path], output_dir=output_dir,
                create_missing=False)
        self.assertEqual(result, replayed)
        self.assertEqual(len(result['proofs']), 1)
        self.assertEqual(json.loads(snapshot_path.read_text()), self.snapshot())
        self.assertEqual(json.loads(Path(result['snapshot_inputs'][0]['path']).read_text())[
            'radio_only_source_proof'], result['proofs'][0])
        proof_path = Path(result['proofs'][0]['path'])
        proof = json.loads(proof_path.read_text())
        proof['sealed_at_utc'] = '2026-10-10T12:01:00+00:00'
        proof_path.write_text(json.dumps(proof), encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            with self.assertRaisesRegex(ValueError, 'Saved radio-only snapshot differs'):
                prepare_radio_only_snapshots(
                    track_snapshot_path=self.track_path,
                    identity_map_path=self.identity_path,
                    batch_dir=batch,
                    collector_summary_path=summary_path,
                    collector_seed_manifest_path=manifest_path,
                    snapshot_paths=[snapshot_path], output_dir=output_dir,
                    create_missing=False)

    def test_missing_proof_replays_typed_ineligible_without_writing_sidecars(self):
        batch = self.root / 'ineligible-replay-batch'
        collector = batch / 'collector'
        (collector / 'requests').mkdir(parents=True)
        snapshot_path = collector / (ROOT + '.json')
        summary_path = collector / 'summary-1.json'
        manifest_path = collector / 'seeds.json'
        for source, target in (
                (self.summary_path, summary_path),
                (self.manifest_path, manifest_path),
                (self.intent_path, batch / 'intent.json')):
            shutil.copyfile(source, target)
        # Preserve all receipts, but make the seed fail its strict album gate.
        raw_tracks = copy.deepcopy(self.raw_tracks)
        raw_tracks[0]['album']['name'] = 'Wrong Seed Album'
        self.raw_tracks = raw_tracks
        cache_key = hashlib.sha256(json.dumps(
            ['get_watch_playlist', self.arguments], sort_keys=True,
            ensure_ascii=False).encode('utf-8')).hexdigest()
        cache_path = collector / 'requests' / (cache_key + '.json')
        cache_path.write_text(json.dumps({
            'endpoint': 'get_watch_playlist', 'arguments': self.arguments,
            'observed_at': self.observed_at,
            'response': {'tracks': raw_tracks},
        }), encoding='utf-8')
        saved_snapshot = self.snapshot()
        snapshot_path.write_text(json.dumps(saved_snapshot), encoding='utf-8')
        summary = json.loads(summary_path.read_text())
        summary['results'][0] = {
            **collector_result_summary(saved_snapshot), 'status': 'collected'}
        summary_path.write_text(json.dumps(summary), encoding='utf-8')
        output_dir = batch / 'radio-only'
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            result = prepare_radio_only_snapshots(
                track_snapshot_path=self.track_path,
                identity_map_path=self.identity_path,
                batch_dir=batch,
                collector_summary_path=summary_path,
                collector_seed_manifest_path=manifest_path,
                snapshot_paths=[snapshot_path], output_dir=output_dir,
                create_missing=False)
        self.assertEqual(result['proofs'], [])
        self.assertEqual(result['snapshot_inputs'], [])
        self.assertEqual(result['ineligible'][0]['persistent_id'], ROOT)
        self.assertEqual(result['ineligible'][0]['reason'], 'seed_not_strictly_matched')
        self.assertEqual(result['ineligible'][0]['raw_radio_cache']['sha256'],
                         hashlib.sha256(cache_path.read_bytes()).hexdigest())
        self.assertFalse((output_dir / (ROOT + '.proof.json')).exists())
        self.assertFalse((output_dir / (ROOT + '.snapshot.json')).exists())

    def test_missing_proof_for_eligible_snapshot_remains_fatal(self):
        batch = self.root / 'eligible-missing-proof-batch'
        collector = batch / 'collector'
        (collector / 'requests').mkdir(parents=True)
        snapshot_path = collector / (ROOT + '.json')
        summary_path = collector / 'summary-1.json'
        manifest_path = collector / 'seeds.json'
        for source, target in (
                (self.snapshot_path, snapshot_path),
                (self.summary_path, summary_path),
                (self.manifest_path, manifest_path),
                (self.intent_path, batch / 'intent.json')):
            shutil.copyfile(source, target)
        cache_key = hashlib.sha256(json.dumps(
            ['get_watch_playlist', self.arguments], sort_keys=True,
            ensure_ascii=False).encode('utf-8')).hexdigest()
        cache_path = collector / 'requests' / (cache_key + '.json')
        shutil.copyfile(self.cache_path, cache_path)
        output_dir = batch / 'radio-only'
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            with self.assertRaisesRegex(ValueError, 'recorded radio proof sidecar is missing'):
                prepare_radio_only_snapshots(
                    track_snapshot_path=self.track_path,
                    identity_map_path=self.identity_path,
                    batch_dir=batch,
                    collector_summary_path=summary_path,
                    collector_seed_manifest_path=manifest_path,
                    snapshot_paths=[snapshot_path], output_dir=output_dir,
                    create_missing=False)
        self.assertFalse((output_dir / (ROOT + '.proof.json')).exists())
        self.assertFalse((output_dir / (ROOT + '.snapshot.json')).exists())

    def test_materializer_loads_real_track_snapshot_from_path(self):
        track_snapshot = self.write('real-track-snapshot.json', {
            'source_sha256': LIBRARY, 'tracks': self.tracks,
        })
        self.track_path = track_snapshot
        intent = json.loads(self.intent_path.read_text())
        intent['runner_config']['track_snapshot'] = {
            'path': str(track_snapshot.resolve()), 'sha256': self.digest(track_snapshot),
        }
        self.intent_path.write_text(json.dumps(intent), encoding='utf-8')
        proof_path = self.root / 'real-track-proof.json'
        proof = self.create(proof_path)
        with patch('radio_snapshot_proof.select_seed_video_evidence',
                   return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            snapshot = radio_only_snapshot_from_proof(proof_path)
        self.assertEqual(snapshot['identity_map_sha256'], proof['identity_map']['sha256'])
        self.assertEqual(snapshot['local_seed']['persistent_id'], ROOT)


if __name__ == '__main__':
    unittest.main()
