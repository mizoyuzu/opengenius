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
                                  radio_snapshot_proof_cache_scope,
                                  verify_radio_snapshot_proof)
import radio_snapshot_proof as radio_proof
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

    def configure_search_derived_seed(self, search_results=None):
        """Replace the ordinary seed hint with a raw exact-search source."""
        snapshot = json.loads(self.snapshot_path.read_text(encoding='utf-8'))
        search_arguments = {'query': 'Seed Song Seed Artist',
                            'filter': 'songs', 'limit': 5}
        search_time = '2026-10-10T11:59:00+00:00'
        snapshot['request_evidence'].insert(0, {
            'endpoint': 'search', 'arguments': search_arguments,
            'observed_at': search_time, 'cache_hit': False,
        })
        self.snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False),
                                      encoding='utf-8')
        summary = json.loads(self.summary_path.read_text(encoding='utf-8'))
        summary['results'] = [{**collector_result_summary(snapshot),
                               'status': 'collected'}]
        self.summary_path.write_text(json.dumps(summary, ensure_ascii=False),
                                     encoding='utf-8')
        evidence = json.loads(self.seed_evidence_path.read_text(encoding='utf-8'))
        evidence['rows'] = []
        self.seed_evidence_path.write_text(json.dumps(evidence, ensure_ascii=False),
                                           encoding='utf-8')
        manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        manifest['seed_video_evidence_sha256'] = self.digest(self.seed_evidence_path)
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False),
                                      encoding='utf-8')
        intent = json.loads(self.intent_path.read_text(encoding='utf-8'))
        intent['seed_video_evidence']['sha256'] = self.digest(self.seed_evidence_path)
        self.intent_path.write_text(json.dumps(intent, ensure_ascii=False),
                                    encoding='utf-8')
        encoded = json.dumps(['search', search_arguments], sort_keys=True,
                             ensure_ascii=False).encode('utf-8')
        cache_dir = self.root / 'requests'
        cache_dir.mkdir(exist_ok=True)
        self.search_cache_path = cache_dir / (hashlib.sha256(encoded).hexdigest() + '.json')
        search_track = {
            'videoId': self.arguments['videoId'], 'title': 'Seed Song',
            'artists': [{'name': 'Seed Artist', 'id': 'UCseed'}],
            'album': {'name': 'Seed Album'}, 'duration_seconds': 100,
            'videoType': 'MUSIC_VIDEO_TYPE_ATV',
        }
        alternate_album = {
            **search_track, 'videoId': 'mnopqrstuvw',
            'album': {'name': 'Another Album'},
        }
        self.search_cache_path.write_text(json.dumps({
            'endpoint': 'search', 'arguments': search_arguments,
            'observed_at': search_time,
            'response': (search_results if search_results is not None
                         else [search_track, alternate_album]),
        }, ensure_ascii=False), encoding='utf-8')

    def verify_with_fixture_patches(self, proof_path):
        return patch('radio_snapshot_proof.load_library',
                     return_value=(self.tracks, LIBRARY, None)), \
               patch('radio_snapshot_proof.select_seed_video_evidence',
                     return_value={ROOT: {'video_id': self.arguments['videoId']}})

    def _install_dynamic_source_fixture(self, *, include_catalog=False):
        collector_dir = self.root / 'cached-run'
        request_dir = collector_dir / 'requests'
        request_dir.mkdir(parents=True)
        summary = self.write('cached-run/summary-1.json', {'results': []})
        manifest = self.write('cached-run/seeds.json', {'seeds': []})
        request = self.write('cached-run/requests/one.json', {'response': []})
        cached_doc = self.write('cached-hints.json', {
            'schema_version': 1,
            'producer': 'opengenius-cached-seed-hint-revalidation-v1',
            'source_runs': [{'collector_dir': str(collector_dir.resolve()),
                             'summary': {'path': str(summary.resolve()),
                                         'sha256': self.digest(summary)},
                             'seed_manifest': {'path': str(manifest.resolve()),
                                               'sha256': self.digest(manifest)}}],
            'rows': [{'input_path': str(request.resolve()),
                      'input_sha256': self.digest(request)}],
        })
        catalog_ref = None
        catalog_source = None
        if include_catalog:
            catalog_source = self.write('catalog-response.json', {'tracks': []})
            catalog_doc = self.write('catalog-evidence.json', {
                'rows': [{'input_path': str(catalog_source.resolve()),
                          'input_sha256': self.digest(catalog_source)}],
            })
            catalog_ref = {'path': str(catalog_doc.resolve()),
                           'sha256': self.digest(catalog_doc)}
        map_ref = {'path': str(self.identity_path.resolve()), 'sha256': self.map_hash}
        transition = {key: dict(map_ref) for key in (
            'old_identity_map', 'new_identity_map', 'graph_export',
            'rebase_report', 'state')}
        rebound = self.write('rebound-hints.json', {
            'schema_version': 1,
            'producer': 'opengenius-map-transition-rebound-seed-evidence-v1',
            'track_snapshot': {'path': str(self.track_path.resolve()),
                               'sha256': self.digest(self.track_path)},
            'typed_map_transition': transition,
            'catalog_evidence': catalog_ref,
            'cached_hint_evidence': {'path': str(cached_doc.resolve()),
                                     'sha256': self.digest(cached_doc)},
        })
        current = self.write('current-radio.json', {'rows': []})
        combined = self.write('seed-video-evidence.json', {
            'schema_version': 1,
            'producer': 'opengenius-combined-rebound-seed-evidence-v1',
            'rebound_evidence': {'path': str(rebound.resolve()),
                                 'sha256': self.digest(rebound)},
            'current_radio_source': {'path': str(current.resolve()),
                                     'sha256': self.digest(current)},
            'current_radio_evidence': {'rows': []}, 'rows': [],
            'audit': {'rebound_hint_count': 0, 'current_radio_hint_count': 0,
                      'recommendation_edges_created': 0,
                      'recommendation_observations_created': 0,
                      'outgoing_roots_created': 0},
        })
        manifest_doc = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        manifest_doc['seed_video_evidence_sha256'] = self.digest(combined)
        self.manifest_path.write_text(json.dumps(manifest_doc), encoding='utf-8')
        intent_doc = json.loads(self.intent_path.read_text(encoding='utf-8'))
        intent_doc['seed_video_evidence'] = {
            'path': str(combined.resolve()), 'sha256': self.digest(combined)}
        self.intent_path.write_text(json.dumps(intent_doc), encoding='utf-8')
        radio_proof._seed_video_evidence._TYPED_TRANSITION_PROOF_CACHE.clear()
        radio_proof._seed_video_evidence._TYPED_TRANSITION_PROOF_CACHE[
            (self.map_hash, self.map_hash, LIBRARY, ())] = {'hash_inputs': []}
        return collector_dir, summary, manifest, request, catalog_source

    def test_scoped_cache_reuses_verified_result_and_returns_a_copy(self):
        output = self.root / 'proof-cache.json'
        self.create(output)
        load_patch, selector_patch = self.verify_with_fixture_patches(output)
        with load_patch, selector_patch, patch(
                'radio_snapshot_proof._proof_from_inputs',
                wraps=__import__('radio_snapshot_proof')._proof_from_inputs) as replay:
            with radio_snapshot_proof_cache_scope():
                first = verify_radio_snapshot_proof(output)
                first['verified'] = False
                second = verify_radio_snapshot_proof(output)
        self.assertEqual(replay.call_count, 1)
        self.assertTrue(second['verified'])
        self.assertEqual(second['persistent_id'], ROOT)

    def _assert_cached_source_change_rejects(self, source_name, *, remove=False,
                                             add_transitive_source=False):
        if add_transitive_source:
            nested = self.write('source-extra.json', {'stable': True})
            intent = json.loads(self.intent_path.read_text(encoding='utf-8'))
            intent['source_inputs'] = [{'path': str(nested.resolve()),
                                        'sha256': self.digest(nested)}]
            self.write('intent.json', intent)
        output = self.root / ('proof-change-' + source_name + '.json')
        self.create(output)
        proof = json.loads(output.read_text(encoding='utf-8'))
        if source_name == 'map':
            source_path = Path(proof['identity_map']['path'])
        elif source_name == 'snapshot':
            source_path = Path(proof['collector_snapshot']['path'])
        elif source_name == 'raw':
            source_path = Path(proof['raw_radio_cache']['path'])
        elif source_name == 'intent':
            source_path = Path(proof['batch_intent']['path'])
        else:
            source_path = self.root / 'source-extra.json'
        load_patch, selector_patch = self.verify_with_fixture_patches(output)
        with load_patch, selector_patch, radio_snapshot_proof_cache_scope():
            self.assertTrue(verify_radio_snapshot_proof(output)['verified'])
            if remove:
                source_path.unlink()
            else:
                source_path.write_text('{"tampered":true}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'cached radio-proof source'):
                verify_radio_snapshot_proof(output)

    def test_cache_hit_rehashes_transitive_intent_source(self):
        self._assert_cached_source_change_rejects('transitive', add_transitive_source=True)

    def test_cache_hit_rejects_changed_map(self):
        self._assert_cached_source_change_rejects('map')

    def test_cache_hit_rejects_changed_snapshot(self):
        self._assert_cached_source_change_rejects('snapshot')

    def test_cache_hit_rejects_changed_raw_cache(self):
        self._assert_cached_source_change_rejects('raw')

    def test_cache_hit_rejects_changed_intent(self):
        self._assert_cached_source_change_rejects('intent')

    def test_cache_hit_rejects_missing_transitive_source(self):
        self._assert_cached_source_change_rejects('transitive-missing',
                                                  remove=True,
                                                  add_transitive_source=True)

    def test_cache_hit_rejects_cached_search_tree_edit_delete_and_add(self):
        collector_dir, summary, manifest, request, _ = self._install_dynamic_source_fixture()
        output = self.root / 'proof-dynamic-cache.json'
        self.create(output)
        load_patch, selector_patch = self.verify_with_fixture_patches(output)
        for mutation, path in (('edit', summary), ('delete', request),
                               ('add', collector_dir / 'summary-new.json')):
            # Reset a new scope and proof for each distinct first-seal state.
            if mutation == 'delete':
                request.write_text('{"response":[]}', encoding='utf-8')
            if mutation == 'add':
                request.write_text('{"response":[]}', encoding='utf-8')
            with load_patch, selector_patch, radio_snapshot_proof_cache_scope():
                self.assertTrue(verify_radio_snapshot_proof(output)['verified'])
                if mutation == 'edit':
                    path.write_text('{"changed":true}', encoding='utf-8')
                elif mutation == 'delete':
                    path.unlink()
                else:
                    path.write_text('{"results":[]}', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'cached radio-proof source'):
                    verify_radio_snapshot_proof(output)
            if mutation == 'delete':
                path.write_text('{"response":[]}', encoding='utf-8')
            if mutation == 'add':
                path.unlink()

    def test_cache_hit_rejects_catalog_source_mutation(self):
        _, _, _, _, catalog_source = self._install_dynamic_source_fixture(
            include_catalog=True)
        output = self.root / 'proof-catalog-cache.json'
        self.create(output)
        load_patch, selector_patch = self.verify_with_fixture_patches(output)
        with load_patch, selector_patch, radio_snapshot_proof_cache_scope():
            self.assertTrue(verify_radio_snapshot_proof(output)['verified'])
            catalog_source.write_text('{"tracks":["tampered"]}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'cached radio-proof source'):
                verify_radio_snapshot_proof(output)

    def test_source_run_tree_change_during_replay_does_not_seal_cache(self):
        collector_dir, _, _, _, _ = self._install_dynamic_source_fixture()
        output = self.root / 'proof-replay-race.json'
        self.create(output)
        load_patch, selector_patch = self.verify_with_fixture_patches(output)
        original = radio_proof._proof_from_inputs
        def replay_then_add(*args, **kwargs):
            result = original(*args, **kwargs)
            (collector_dir / 'summary-late.json').write_text('{}', encoding='utf-8')
            return result
        with load_patch, selector_patch, radio_snapshot_proof_cache_scope(), \
                patch('radio_snapshot_proof._proof_from_inputs', side_effect=replay_then_add):
            with self.assertRaisesRegex(ValueError, 'changed during radio proof replay'):
                verify_radio_snapshot_proof(output)

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

    def test_radio_only_mode_is_declared_and_rejects_auxiliary_endpoints(self):
        snapshot = self.snapshot()
        snapshot['collector_collection_mode'] = 'radio-only-v1'
        snapshot['related_status'] = 'not_requested'
        snapshot['requests'] = [snapshot['requests'][0]]
        self.snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')
        summary = json.loads(self.summary_path.read_text())
        summary['collector_collection_mode'] = 'radio-only-v1'
        summary['results'][0] = {**collector_result_summary(snapshot), 'status': 'collected'}
        self.summary_path.write_text(json.dumps(summary), encoding='utf-8')
        manifest = json.loads(self.manifest_path.read_text())
        manifest['collector_collection_mode'] = 'radio-only-v1'
        self.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        intent = json.loads(self.intent_path.read_text())
        intent['runner_config']['collector_collection_mode'] = 'radio-only-v1'
        self.intent_path.write_text(json.dumps(intent), encoding='utf-8')
        output = self.root / 'radio-only-proof.json'
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            proof = self.create(output)
        self.assertEqual(proof['outgoing_target_pids'], [TARGET])

        snapshot['request_evidence'].append({
            'endpoint': 'get_watch_playlist',
            'arguments': {'videoId': self.arguments['videoId'], 'radio': False, 'limit': 25},
            'observed_at': self.observed_at, 'cache_hit': False})
        snapshot['requests'].append({'endpoint': 'watch_seed', 'related_available': True})
        self.snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')
        summary['results'][0] = {**collector_result_summary(snapshot), 'status': 'collected'}
        self.summary_path.write_text(json.dumps(summary), encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={ROOT: {'video_id': self.arguments['videoId']}}):
            with self.assertRaisesRegex(ValueError, 'auxiliary watch/related'):
                self.create(self.root / 'radio-only-aux-proof.json')

    def test_search_derived_seed_binding_requires_exact_strict_unique_search(self):
        self.configure_search_derived_seed()
        output = self.root / 'search-derived-proof.json'
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={}):
            proof = create_radio_snapshot_proof(
                track_snapshot_path=self.track_path,
                identity_map_path=self.identity_path,
                snapshot_path=self.snapshot_path,
                collector_summary_path=self.summary_path,
                collector_seed_manifest_path=self.manifest_path,
                batch_intent_path=self.intent_path,
                raw_radio_cache_path=self.cache_path,
                output_path=output,
                sealed_at_utc='2026-10-10T12:10:00+00:00')
            binding = proof['seed_video_search_binding']
            self.assertEqual(binding['kind'],
                             'authenticated-search-strict-unique-seed-v1')
            self.assertEqual(binding['strict_unique_video_ids'],
                             [self.arguments['videoId']])
            self.assertEqual(binding['persistent_id'], ROOT)
            self.assertEqual(binding['resolver']['candidate_video_count'], 2)
            self.assertEqual(binding['resolver']['status'],
                             'unique_album_metadata_preference_unverified')
            with patch('radio_snapshot_proof.load_library',
                       return_value=(self.tracks, LIBRARY, None)), \
                    patch('radio_snapshot_proof.select_seed_video_evidence',
                          return_value={}):
                self.assertTrue(verify_radio_snapshot_proof(output)['verified'])

    def test_search_derived_seed_binding_rejects_multiple_strict_videos(self):
        duplicate = {
            'videoId': 'zyxwvutsrqp', 'title': 'Seed Song',
            'artists': [{'name': 'Seed Artist', 'id': 'UCseed'}],
            'album': {'name': 'Seed Album'}, 'duration_seconds': 100,
            'videoType': 'MUSIC_VIDEO_TYPE_ATV',
        }
        self.configure_search_derived_seed(search_results=[
            {'videoId': self.arguments['videoId'], 'title': 'Seed Song',
             'artists': [{'name': 'Seed Artist', 'id': 'UCseed'}],
             'album': {'name': 'Seed Album'}, 'duration_seconds': 100,
             'videoType': 'MUSIC_VIDEO_TYPE_ATV'}, duplicate])
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={}):
            with self.assertRaisesRegex(ValueError, 'does not resolve to the radio seed video'):
                create_radio_snapshot_proof(
                    track_snapshot_path=self.track_path,
                    identity_map_path=self.identity_path,
                    snapshot_path=self.snapshot_path,
                    collector_summary_path=self.summary_path,
                    collector_seed_manifest_path=self.manifest_path,
                    batch_intent_path=self.intent_path,
                    raw_radio_cache_path=self.cache_path,
                    output_path=self.root / 'two-strict-videos.json',
                    sealed_at_utc='2026-10-10T12:10:00+00:00')

    def test_search_derived_seed_binding_rejects_wrong_request_receipts(self):
        self.configure_search_derived_seed()
        original_snapshot = json.loads(self.snapshot_path.read_text(encoding='utf-8'))
        original_cache = json.loads(self.search_cache_path.read_text(encoding='utf-8'))
        cases = (
            ('query', lambda row, cache: row['arguments'].__setitem__('query', 'other query')),
            ('filter', lambda row, cache: row['arguments'].__setitem__('filter', 'videos')),
            ('limit', lambda row, cache: row['arguments'].__setitem__('limit', 10)),
            ('cache-hit', lambda row, cache: row.__setitem__('cache_hit', True)),
            ('timestamp', lambda row, cache: row.__setitem__(
                'observed_at', '2026-10-10T12:01:00+00:00')),
            ('different-video', lambda row, cache: cache['response'][0].__setitem__(
                'videoId', 'ZYXWVUTSRQP')),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                snapshot = copy.deepcopy(original_snapshot)
                cache = copy.deepcopy(original_cache)
                search_row = next(row for row in snapshot['request_evidence']
                                  if row['endpoint'] == 'search')
                mutate(search_row, cache)
                self.snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False),
                                              encoding='utf-8')
                self.search_cache_path.write_text(json.dumps(cache, ensure_ascii=False),
                                                  encoding='utf-8')
                summary = json.loads(self.summary_path.read_text(encoding='utf-8'))
                summary['results'] = [{**collector_result_summary(snapshot),
                                       'status': 'collected'}]
                self.summary_path.write_text(json.dumps(summary, ensure_ascii=False),
                                             encoding='utf-8')
                with patch('radio_snapshot_proof.load_library',
                           return_value=(self.tracks, LIBRARY, None)), \
                        patch('radio_snapshot_proof.select_seed_video_evidence',
                              return_value={}):
                    with self.assertRaises(ValueError):
                        create_radio_snapshot_proof(
                            track_snapshot_path=self.track_path,
                            identity_map_path=self.identity_path,
                            snapshot_path=self.snapshot_path,
                            collector_summary_path=self.summary_path,
                            collector_seed_manifest_path=self.manifest_path,
                            batch_intent_path=self.intent_path,
                            raw_radio_cache_path=self.cache_path,
                            output_path=self.root / ('invalid-search-' + label + '.json'),
                            sealed_at_utc='2026-10-10T12:10:00+00:00')

    def test_search_derived_seed_binding_rejects_zero_strict_videos(self):
        wrong_album = {
            'videoId': self.arguments['videoId'], 'title': 'Seed Song',
            'artists': [{'name': 'Seed Artist', 'id': 'UCseed'}],
            'album': {'name': 'Wrong Album'}, 'duration_seconds': 100,
            'videoType': 'MUSIC_VIDEO_TYPE_ATV',
        }
        self.configure_search_derived_seed(search_results=[wrong_album])
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={}):
            with self.assertRaisesRegex(ValueError, 'one strict unique video'):
                create_radio_snapshot_proof(
                    track_snapshot_path=self.track_path,
                    identity_map_path=self.identity_path,
                    snapshot_path=self.snapshot_path,
                    collector_summary_path=self.summary_path,
                    collector_seed_manifest_path=self.manifest_path,
                    batch_intent_path=self.intent_path,
                    raw_radio_cache_path=self.cache_path,
                    output_path=self.root / 'zero-strict-video.json',
                    sealed_at_utc='2026-10-10T12:10:00+00:00')

    def test_search_binding_cannot_hide_a_different_strict_radio_seed_pid(self):
        self.configure_search_derived_seed()
        other = {'persistent_id': '0000000000000003', 'title': 'Seed Song',
                 'artist': 'Seed Artist', 'album': 'Seed Album',
                 'duration_ms': 104000}
        self.tracks.append(other)
        self.identity = build_map(self.tracks, LIBRARY)
        self.identity_path = self.write('radio-ambiguous-identity.json', self.identity)
        self.map_hash = self.digest(self.identity_path)
        self.matcher = IdentityMap(self.identity, self.tracks, LIBRARY)
        # Search is 100s and uniquely supports ROOT (the duplicate track is
        # four seconds away); the radio seed row is 102s and strictly supports
        # both PIDs. Search uniqueness cannot mask radio-row ambiguity.
        self.raw_tracks[0]['length'] = '1:42'
        self.cache_path = self.write('radio-ambiguous-cache.json', {
            'endpoint': 'get_watch_playlist', 'arguments': self.arguments,
            'observed_at': self.observed_at, 'response': {'tracks': self.raw_tracks},
        })
        snapshot = self.snapshot()
        snapshot['request_evidence'].insert(0, {
            'endpoint': 'search',
            'arguments': {'query': 'Seed Song Seed Artist',
                          'filter': 'songs', 'limit': 5},
            'observed_at': '2026-10-10T11:59:00+00:00', 'cache_hit': False,
        })
        snapshot['identity_map_sha256'] = self.map_hash
        self.snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False),
                                      encoding='utf-8')
        summary = json.loads(self.summary_path.read_text(encoding='utf-8'))
        summary['results'] = [{**collector_result_summary(snapshot),
                               'status': 'collected'}]
        self.summary_path.write_text(json.dumps(summary, ensure_ascii=False),
                                     encoding='utf-8')
        evidence = json.loads(self.seed_evidence_path.read_text(encoding='utf-8'))
        evidence.update(identity_map_sha256=self.map_hash, rows=[])
        self.seed_evidence_path.write_text(json.dumps(evidence, ensure_ascii=False),
                                           encoding='utf-8')
        manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        manifest.update(identity_map_sha256=self.map_hash,
                        seed_video_evidence_sha256=self.digest(self.seed_evidence_path))
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False),
                                      encoding='utf-8')
        intent = json.loads(self.intent_path.read_text(encoding='utf-8'))
        intent.update(identity_map_sha256=self.map_hash)
        intent['runner_config']['identity_map'] = {
            'path': str(self.identity_path.resolve()), 'sha256': self.map_hash}
        intent['seed_video_evidence']['sha256'] = self.digest(self.seed_evidence_path)
        self.intent_path.write_text(json.dumps(intent, ensure_ascii=False),
                                    encoding='utf-8')
        output = self.root / 'different-radio-seed-proof.json'
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={}):
            with self.assertRaisesRegex(RadioSnapshotIneligible,
                                        'seed_not_strictly_matched'):
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

    def test_search_derived_seed_binding_rejects_raw_search_cache_tamper(self):
        self.configure_search_derived_seed()
        output = self.root / 'search-cache-tamper-proof.json'
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={}):
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
            with patch('radio_snapshot_proof.load_library',
                       return_value=(self.tracks, LIBRARY, None)), \
                    patch('radio_snapshot_proof.select_seed_video_evidence',
                          return_value={}):
                with radio_snapshot_proof_cache_scope():
                    self.assertTrue(verify_radio_snapshot_proof(output)['verified'])
                    verify_radio_snapshot_proof(output)
                    search_cache = json.loads(self.search_cache_path.read_text(encoding='utf-8'))
                    search_cache['response'][0]['title'] = 'Tampered Search Result'
                    self.search_cache_path.write_text(
                        json.dumps(search_cache, ensure_ascii=False), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'source receipt changed'):
                        verify_radio_snapshot_proof(output)


if __name__ == '__main__':
    unittest.main()
