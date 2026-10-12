"""Authenticated radio-only source proof rejects unsupported relations and tampering."""
import copy
from contextlib import contextmanager
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
import merge_rebased_radio_growth as radio_growth_merge
from radio_snapshot_proof import (create_radio_snapshot_proof,
                                  RadioSnapshotIneligible,
                                  radio_only_snapshot_from_proof,
                                  radio_snapshot_proof_cache_scope,
                                  verify_radio_snapshot_proof)
import radio_snapshot_proof as radio_proof
from radio_source_integration import prepare_radio_only_snapshots
import expand_genuine_roots as genuine_runner
from seed_video_evidence import (build_seed_video_evidence,
                                 select_seed_video_evidence)
import seed_video_evidence as seed_evidence_module
import merge_rebased_radio_growth as radio_growth_merge


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
        track_doc = json.loads(self.track_path.read_text(encoding='utf-8'))
        track_doc['source_sha256'] = LIBRARY
        self.track_path.write_text(json.dumps(track_doc), encoding='utf-8')
        intent['runner_config']['track_snapshot'] = {
            'path': str(self.track_path.resolve()), 'sha256': self.digest(self.track_path)}
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

    def test_creation_replay_seals_exact_written_proof_for_normal_verify_path(self):
        output = self.root / 'proof-created-and-verified.json'
        load_patch, selector_patch = self.verify_with_fixture_patches(output)
        with load_patch, selector_patch, patch(
                'radio_snapshot_proof._proof_from_inputs',
                wraps=radio_proof._proof_from_inputs) as replay:
            with radio_snapshot_proof_cache_scope() as stats:
                created = self.create(output)
                self.assertTrue(verify_radio_snapshot_proof(output)['verified'])
                self.assertTrue(verify_radio_snapshot_proof(output)['verified'])
                self.assertEqual(json.loads(output.read_text()), created)
                self.assertEqual(stats['cache_hits'], 2)
                self.assertEqual(stats['cache_misses'], 0)
                self.assertEqual(stats['creation_replays'], 1)
                self.assertGreater(stats['creation_full_source_replay_seconds'], 0.0)
                self.assertGreater(stats['creation_closure_seal_seconds'], 0.0)
            # A separate top-level invocation has no memo and still performs
            # the ordinary full independent replay.
            with radio_snapshot_proof_cache_scope() as fresh_stats:
                independently_verified = verify_radio_snapshot_proof(output)
                self.assertTrue(independently_verified['verified'])
                self.assertEqual(fresh_stats['cache_misses'], 1)
                self.assertEqual(fresh_stats['cache_hits'], 0)
                self.assertEqual(fresh_stats['verified_results'], 1)
                self.assertEqual(fresh_stats['creation_replays'], 0)
        # One creation replay plus one fresh-scope replay. The two verifies in
        # the creation scope take the normal exact-byte/closure cache-hit path.
        self.assertEqual(replay.call_count, 2)

    def test_creation_memo_supports_search_bound_seed_and_rehashes_search_cache(self):
        self.configure_search_derived_seed()
        output = self.root / 'proof-created-search-bound.json'
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={}), \
                patch('radio_snapshot_proof._proof_from_inputs',
                      wraps=radio_proof._proof_from_inputs) as replay:
            with radio_snapshot_proof_cache_scope() as stats:
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
                self.assertEqual(proof['seed_video_search_binding']['persistent_id'], ROOT)
                self.assertTrue(verify_radio_snapshot_proof(output)['verified'])
                self.assertEqual(stats['cache_hits'], 1)
                self.assertEqual(stats['cache_misses'], 0)
            with radio_snapshot_proof_cache_scope() as fresh_stats:
                self.assertTrue(verify_radio_snapshot_proof(output)['verified'])
                self.assertEqual(fresh_stats['cache_misses'], 1)
        self.assertEqual(replay.call_count, 2)

        # A normal memo hit after creation also rehashes the raw search result,
        # not only the original radio cache.
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence',
                      return_value={}):
            with radio_snapshot_proof_cache_scope():
                create_radio_snapshot_proof(
                    track_snapshot_path=self.track_path,
                    identity_map_path=self.identity_path,
                    snapshot_path=self.snapshot_path,
                    collector_summary_path=self.summary_path,
                    collector_seed_manifest_path=self.manifest_path,
                    batch_intent_path=self.intent_path,
                    raw_radio_cache_path=self.cache_path,
                    output_path=self.root / 'proof-created-search-source-tamper.json',
                    sealed_at_utc='2026-10-10T12:10:00+00:00')
                self.search_cache_path.write_text('{"tampered":true}', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'cached radio-proof source receipt changed'):
                    verify_radio_snapshot_proof(
                        self.root / 'proof-created-search-source-tamper.json')

    def test_creation_memo_rejects_source_mutation_before_verify(self):
        output = self.root / 'proof-created-source-mutation.json'
        load_patch, selector_patch = self.verify_with_fixture_patches(output)
        with load_patch, selector_patch, radio_snapshot_proof_cache_scope():
            self.create(output)
            self.cache_path.write_text('{"tampered":true}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'cached radio-proof source receipt changed'):
                verify_radio_snapshot_proof(output)

    def test_creation_memo_rejects_search_tree_membership_change(self):
        collector_dir, _, _, _, _ = self._install_dynamic_source_fixture()
        output = self.root / 'proof-created-tree-mutation.json'
        load_patch, selector_patch = self.verify_with_fixture_patches(output)
        with load_patch, selector_patch, radio_snapshot_proof_cache_scope():
            self.create(output)
            (collector_dir / 'summary-after-create.json').write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'cached radio-proof source directory changed'):
                verify_radio_snapshot_proof(output)

    def test_creation_memo_miss_after_written_proof_bytes_change(self):
        output = self.root / 'proof-created-bytes-change.json'
        load_patch, selector_patch = self.verify_with_fixture_patches(output)
        with load_patch, selector_patch, patch(
                'radio_snapshot_proof._proof_from_inputs',
                wraps=radio_proof._proof_from_inputs) as replay:
            with radio_snapshot_proof_cache_scope():
                self.create(output)
                changed = json.loads(output.read_text())
                changed['outgoing_target_pids'] = []
                output.write_text(json.dumps(changed), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'differs from full raw response'):
                    verify_radio_snapshot_proof(output)
        self.assertEqual(replay.call_count, 2)

    def test_creation_does_not_seal_directory_changed_during_full_replay(self):
        collector_dir, _, _, _, _ = self._install_dynamic_source_fixture()
        output = self.root / 'proof-created-directory-race.json'
        load_patch, selector_patch = self.verify_with_fixture_patches(output)
        original = radio_proof._proof_from_inputs

        def replay_then_add(*args, **kwargs):
            result = original(*args, **kwargs)
            (collector_dir / 'summary-created-race.json').write_text('{}', encoding='utf-8')
            return result

        with load_patch, selector_patch, radio_snapshot_proof_cache_scope(), \
                patch('radio_snapshot_proof._proof_from_inputs', side_effect=replay_then_add):
            with self.assertRaisesRegex(ValueError, 'directory changed during radio proof creation'):
                self.create(output)
            cache = radio_proof._ACTIVE_VERIFICATION_CACHE.get()
            self.assertFalse(any(key[0] == str(output.resolve()) for key in cache))

    def test_creation_does_not_seal_changed_direct_source_after_replay(self):
        output = self.root / 'proof-created-source-race.json'
        load_patch, selector_patch = self.verify_with_fixture_patches(output)
        original = radio_proof._proof_from_inputs

        def replay_then_mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            self.cache_path.write_text('{"changed_after_read":true}', encoding='utf-8')
            return result

        with load_patch, selector_patch, radio_snapshot_proof_cache_scope(), \
                patch('radio_snapshot_proof._proof_from_inputs', side_effect=replay_then_mutate):
            with self.assertRaisesRegex(ValueError, 'verified radio-proof source changed'):
                self.create(output)
            cache = radio_proof._ACTIVE_VERIFICATION_CACHE.get()
            self.assertFalse(any(key[0] == str(output.resolve()) for key in cache))

    def test_chronological_proof_history_fits_capacity_and_scopes_reset(self):
        count = 260
        self.assertGreaterEqual(radio_proof._CACHE_LIMIT, count)
        proofs = []
        marker_sha = self.digest(self.cache_path)
        for index in range(count):
            path = self.root / f"capacity-proof-{index:03d}.json"
            proof = {
                'schema_version': 1, 'producer': radio_proof.PRODUCER,
                'policy': radio_proof.POLICY, 'selected_seed': {'persistent_id': ROOT},
                'outgoing_target_pids': [TARGET], 'outgoing_recommendation_edges': [],
                'raw_radio_cache': {'path': str(self.cache_path.resolve()),
                                    'sha256': marker_sha},
                'collector_snapshot': {'path': str(self.snapshot_path.resolve()),
                                       'sha256': self.digest(self.snapshot_path)},
                'collector_summary': {'path': str(self.summary_path.resolve()),
                                      'sha256': self.digest(self.summary_path)},
                'track_snapshot': {'path': str(path.resolve()), 'sha256': 'a' * 64},
                'identity_map': {'path': str(self.identity_path.resolve()),
                                 'sha256': self.map_hash},
                'collector_seed_manifest': {'path': str(self.manifest_path.resolve()),
                                            'sha256': self.digest(self.manifest_path)},
                'batch_intent': {'path': str(self.intent_path.resolve()),
                                 'sha256': self.digest(self.intent_path)},
                'seed_plan': {'path': str(self.seed_plan_path.resolve()),
                              'sha256': self.digest(self.seed_plan_path)},
                'seed_video_evidence': {'path': str(self.seed_evidence_path.resolve()),
                                        'sha256': self.digest(self.seed_evidence_path)},
                'library_sha256': LIBRARY,
            }
            path.write_text(json.dumps(proof, ensure_ascii=False), encoding='utf-8')
            proofs.append(path)

        def replay_from_proof(**kwargs):
            return json.loads(Path(kwargs['track_snapshot_path']).read_bytes())

        with patch('radio_snapshot_proof._proof_from_inputs', side_effect=replay_from_proof) as replay, \
                patch('radio_snapshot_proof._replay_scanned_directory_state', return_value=()), \
                patch('radio_snapshot_proof._verified_source_closure', return_value=((
                    str(self.cache_path.resolve()), marker_sha),)):
            with radio_snapshot_proof_cache_scope() as stats:
                for path in proofs:
                    self.assertTrue(verify_radio_snapshot_proof(path)['verified'])
                self.assertEqual(stats['cache_misses'], count)
                self.assertEqual(stats['cache_evictions'], 0)
                for path in proofs:
                    self.assertTrue(verify_radio_snapshot_proof(path)['verified'])
                self.assertEqual(stats['cache_hits'], count)
                self.assertEqual(stats['cache_evictions'], 0)
            self.assertEqual(replay.call_count, count)
            with radio_snapshot_proof_cache_scope() as fresh_stats:
                verify_radio_snapshot_proof(proofs[0])
                self.assertEqual(fresh_stats['cache_misses'], 1)
                self.assertEqual(fresh_stats['cache_hits'], 0)
            self.assertEqual(replay.call_count, count + 1)

    def test_proof_cache_still_evicts_at_explicit_temporary_small_bound(self):
        proofs = []
        marker_sha = self.digest(self.cache_path)
        for index in range(3):
            path = self.root / f"eviction-proof-{index}.json"
            proof = {
                'schema_version': 1, 'producer': radio_proof.PRODUCER,
                'policy': radio_proof.POLICY, 'selected_seed': {'persistent_id': ROOT},
                'outgoing_target_pids': [TARGET], 'outgoing_recommendation_edges': [],
                'raw_radio_cache': {'path': str(self.cache_path.resolve()), 'sha256': marker_sha},
                'collector_snapshot': {'path': str(self.snapshot_path.resolve()),
                                       'sha256': self.digest(self.snapshot_path)},
                'collector_summary': {'path': str(self.summary_path.resolve()),
                                      'sha256': self.digest(self.summary_path)},
                'track_snapshot': {'path': str(path.resolve()), 'sha256': 'a' * 64},
                'identity_map': {'path': str(self.identity_path.resolve()), 'sha256': self.map_hash},
                'collector_seed_manifest': {'path': str(self.manifest_path.resolve()),
                                            'sha256': self.digest(self.manifest_path)},
                'batch_intent': {'path': str(self.intent_path.resolve()),
                                 'sha256': self.digest(self.intent_path)},
                'seed_plan': {'path': str(self.seed_plan_path.resolve()),
                              'sha256': self.digest(self.seed_plan_path)},
                'seed_video_evidence': {'path': str(self.seed_evidence_path.resolve()),
                                        'sha256': self.digest(self.seed_evidence_path)},
                'library_sha256': LIBRARY,
            }
            path.write_text(json.dumps(proof), encoding='utf-8')
            proofs.append(path)

        def replay_from_proof(**kwargs):
            return json.loads(Path(kwargs['track_snapshot_path']).read_bytes())

        with patch.object(radio_proof, '_CACHE_LIMIT', 2), \
                patch('radio_snapshot_proof._proof_from_inputs', side_effect=replay_from_proof) as replay, \
                patch('radio_snapshot_proof._replay_scanned_directory_state', return_value=()), \
                patch('radio_snapshot_proof._verified_source_closure', return_value=((
                    str(self.cache_path.resolve()), marker_sha),)):
            with radio_snapshot_proof_cache_scope() as stats:
                for path in proofs:
                    verify_radio_snapshot_proof(path)
                self.assertEqual(stats['cache_evictions'], 1)
                for path in proofs[1:]:
                    verify_radio_snapshot_proof(path)
                self.assertEqual(stats['cache_hits'], 2)
                verify_radio_snapshot_proof(proofs[0])
                self.assertEqual(stats['cache_misses'], 4)
                self.assertEqual(stats['cache_evictions'], 2)
            self.assertEqual(replay.call_count, 4)

    def test_nested_scopes_share_memo_rehash_tampering_and_restore_outer_context(self):
        output = self.root / 'nested-proof-cache.json'
        self.create(output)
        load_patch, selector_patch = self.verify_with_fixture_patches(output)
        with load_patch, selector_patch, patch(
                'radio_snapshot_proof._proof_from_inputs',
                wraps=__import__('radio_snapshot_proof')._proof_from_inputs) as replay:
            with radio_snapshot_proof_cache_scope() as outer_stats:
                self.assertIsNotNone(radio_proof._ACTIVE_VERIFICATION_CACHE.get())
                self.assertTrue(verify_radio_snapshot_proof(output)['verified'])
                with radio_snapshot_proof_cache_scope() as nested_stats:
                    self.assertIs(outer_stats, nested_stats)
                    self.assertTrue(verify_radio_snapshot_proof(output)['verified'])
                    self.assertEqual(nested_stats['cache_hits'], 1)
                self.cache_path.write_text('{"tampered":true}', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'cached radio-proof source receipt changed'):
                    with radio_snapshot_proof_cache_scope():
                        verify_radio_snapshot_proof(output)
                self.assertIsNotNone(radio_proof._ACTIVE_VERIFICATION_CACHE.get())
        self.assertEqual(replay.call_count, 1)
        self.assertEqual(outer_stats['cache_misses'], 1)
        self.assertEqual(outer_stats['cache_hits'], 1)
        self.assertIsNone(radio_proof._ACTIVE_VERIFICATION_CACHE.get())

    def test_top_level_proof_scopes_are_independent_and_exception_safe(self):
        output = self.root / 'independent-proof-scopes.json'
        self.create(output)
        load_patch, selector_patch = self.verify_with_fixture_patches(output)
        with load_patch, selector_patch, patch(
                'radio_snapshot_proof._proof_from_inputs',
                wraps=__import__('radio_snapshot_proof')._proof_from_inputs) as replay:
            for _ in range(2):
                with radio_snapshot_proof_cache_scope() as stats:
                    self.assertTrue(verify_radio_snapshot_proof(output)['verified'])
                    self.assertEqual(stats['cache_misses'], 1)
                    self.assertEqual(stats['cache_hits'], 0)
            with self.assertRaisesRegex(RuntimeError, 'test cleanup'):
                with radio_snapshot_proof_cache_scope():
                    raise RuntimeError('test cleanup')
            self.assertIsNone(radio_proof._ACTIVE_VERIFICATION_CACHE.get())
        self.assertEqual(replay.call_count, 2)

    def test_merge_wrapper_reuses_selector_index_across_distinct_proofs(self):
        evidence = build_seed_video_evidence(
            [self.snapshot_path], self.matcher, LIBRARY, self.map_hash)
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

        proof_paths = [self.root / 'merge-wrapper-proof-one.json',
                       self.root / 'merge-wrapper-proof-two.json']
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)):
            for path, sealed_at in zip(
                    proof_paths, ('2026-10-10T12:10:00+00:00',
                                  '2026-10-10T12:11:00+00:00')):
                create_radio_snapshot_proof(
                    track_snapshot_path=self.track_path,
                    identity_map_path=self.identity_path,
                    snapshot_path=self.snapshot_path,
                    collector_summary_path=self.summary_path,
                    collector_seed_manifest_path=self.manifest_path,
                    batch_intent_path=self.intent_path,
                    raw_radio_cache_path=self.cache_path,
                    output_path=path, sealed_at_utc=sealed_at)

        original_scope = seed_evidence_module.seed_video_selector_cache_scope
        captured_stats = []

        @contextmanager
        def tracked_scope():
            with original_scope() as stats:
                captured_stats.append(stats)
                yield stats

        def validate_two_proofs(*args, **kwargs):
            return [verify_radio_snapshot_proof(path)['verified']
                    for path in proof_paths]

        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch.object(seed_evidence_module,
                             'seed_video_selector_cache_scope', tracked_scope), \
                patch.object(radio_growth_merge,
                             '_merge_radio_growth_with_radio_proof_cache',
                             side_effect=validate_two_proofs):
            self.assertEqual(radio_growth_merge.merge_radio_growth(), [True, True])
        self.assertEqual(len(captured_stats), 1)
        self.assertEqual(captured_stats[0]['full_replays'], 1)
        self.assertEqual(captured_stats[0]['hits'], 1)
        self.assertIsNone(seed_evidence_module._ACTIVE_SELECTOR_INDEX.get())
        self.assertIsNone(radio_proof._ACTIVE_VERIFICATION_CACHE.get())

    def test_runner_rejects_diagnostics_nested_in_campaign_output_before_loading(self):
        output = self.root / 'campaign-output'
        diagnostics_path = output / 'diagnostics.json'
        args = type('RunnerArgs', (), {
            'output': output,
            'proof_cache_diagnostics': diagnostics_path,
        })()
        with patch('expand_genuine_roots.load_library') as load:
            with self.assertRaisesRegex(genuine_runner.RecoveryUnsafe, 'outside campaign output'):
                genuine_runner._run_with_radio_proof_cache(args)
        load.assert_not_called()
        self.assertFalse(output.exists())

    def test_runner_optional_diagnostics_capture_nested_verification_without_paths(self):
        output = self.root / 'runner-diagnostics-proof.json'
        proof = self.create(output)
        diagnostics_path = self.root / 'proof-cache-diagnostics.json'
        selector_document = build_seed_video_evidence(
            [self.snapshot_path], self.matcher, LIBRARY, self.map_hash)
        selector_document_path = self.write('selector-hints.json', selector_document)
        args = type('RunnerArgs', (), {
            'proof_cache_diagnostics': diagnostics_path,
            'source_policy': 'authenticated-watch-radio-only-v1',
        })()
        load_patch, selector_patch = self.verify_with_fixture_patches(output)

        def nested_runner(_args):
            with radio_snapshot_proof_cache_scope():
                self.assertEqual(verify_radio_snapshot_proof(output)['persistent_id'], ROOT)
                with radio_snapshot_proof_cache_scope():
                    self.assertEqual(verify_radio_snapshot_proof(output)['persistent_id'], ROOT)
            self.assertEqual(genuine_runner.select_seed_video_evidence(
                selector_document, selector_document_path, [], self.tracks,
                self.matcher, LIBRARY, self.map_hash), {})
            self.assertEqual(genuine_runner.select_seed_video_evidence(
                selector_document, selector_document_path,
                [{'persistent_id': ROOT}], self.tracks,
                self.matcher, LIBRARY, self.map_hash)[ROOT]['video_id'],
                self.arguments['videoId'])
            return {'status': 'paused_at_checkpoint'}

        with load_patch, selector_patch, patch(
                'expand_genuine_roots._run_with_radio_proof_cache',
                side_effect=nested_runner):
            result = genuine_runner.run(args)
        document = json.loads(diagnostics_path.read_text(encoding='utf-8'))
        self.assertEqual(result['status'], 'paused_at_checkpoint')
        self.assertEqual(document['producer'], 'opengenius-radio-proof-cache-metrics-v1')
        self.assertEqual(document['source_policy'], 'authenticated-watch-radio-only-v1')
        self.assertEqual(document['metrics']['verify_calls'], 2)
        self.assertEqual(document['metrics']['cache_misses'], 1)
        self.assertEqual(document['metrics']['cache_hits'], 1)
        self.assertEqual(document['metrics']['verified_results'], 2)
        self.assertEqual(document['metrics']['verified_outgoing_target_count_total'],
                         2 * len(proof['outgoing_target_pids']))
        self.assertEqual(document['selector_metrics']['full_replays'], 1)
        self.assertEqual(document['selector_metrics']['hits'], 1)
        self.assertEqual(document['selector_metrics']['calls'], 2)
        self.assertNotIn(str(self.root), diagnostics_path.read_text(encoding='utf-8'))
        self.assertNotIn('auth_path', diagnostics_path.read_text(encoding='utf-8').lower())

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
            with self.assertRaisesRegex(ValueError, 'no_strict_unique_outgoing_targets') as caught:
                self.create(output)
        self.assertEqual(caught.exception.details, {})

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

    def test_search_derived_root_binding_reaches_consumer_only_through_proof(self):
        self.configure_search_derived_seed()
        # A loose second metadata candidate shares the seed title/credit/time,
        # but fails the exact album gate. The authenticated search and radio
        # proof uniquely establish ROOT; ordinary rematching alone is ambiguous.
        other = {'persistent_id': '0000000000000003', 'title': 'Seed Song',
                 'artist': 'Seed Artist', 'album': 'Different Album',
                 'duration_ms': 100000}
        self.tracks.append(other)
        self.identity = build_map(self.tracks, LIBRARY)
        self.identity_path = self.write('search-bound-identity.json', self.identity)
        self.map_hash = self.digest(self.identity_path)
        self.matcher = IdentityMap(self.identity, self.tracks, LIBRARY)

        snapshot = json.loads(self.snapshot_path.read_text(encoding='utf-8'))
        snapshot['identity_map_sha256'] = self.map_hash
        snapshot['observations'] = observe(
            self.raw_tracks, 'radio', self.observed_at, self.tracks,
            self.arguments['videoId'], candidate_matcher=self.matcher.match)
        self.snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
        summary = json.loads(self.summary_path.read_text(encoding='utf-8'))
        summary['results'] = [{**collector_result_summary(snapshot), 'status': 'collected'}]
        self.summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding='utf-8')
        evidence = json.loads(self.seed_evidence_path.read_text(encoding='utf-8'))
        evidence['identity_map_sha256'] = self.map_hash
        self.seed_evidence_path.write_text(json.dumps(evidence, ensure_ascii=False), encoding='utf-8')
        manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        manifest['identity_map_sha256'] = self.map_hash
        manifest['seed_video_evidence_sha256'] = self.digest(self.seed_evidence_path)
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        intent = json.loads(self.intent_path.read_text(encoding='utf-8'))
        intent['identity_map_sha256'] = self.map_hash
        intent['runner_config']['identity_map'] = {
            'path': str(self.identity_path.resolve()), 'sha256': self.map_hash}
        intent['seed_video_evidence']['sha256'] = self.digest(self.seed_evidence_path)
        self.intent_path.write_text(json.dumps(intent, ensure_ascii=False), encoding='utf-8')

        proof_path = self.root / 'search-bound-proof.json'
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            proof = create_radio_snapshot_proof(
                track_snapshot_path=self.track_path, identity_map_path=self.identity_path,
                snapshot_path=self.snapshot_path, collector_summary_path=self.summary_path,
                collector_seed_manifest_path=self.manifest_path,
                batch_intent_path=self.intent_path, raw_radio_cache_path=self.cache_path,
                output_path=proof_path, sealed_at_utc='2026-10-10T12:10:00+00:00')
            sanitized = radio_only_snapshot_from_proof(proof_path)
        snapshot_path = self.write('search-bound-radio-only.json', sanitized)
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            graphs, skipped, _ = collect_graphs(
                [snapshot_path], self.matcher, LIBRARY,
                source_policy='authenticated-watch-radio-only-v1',
                identity_map_sha256=self.map_hash)
        self.assertEqual(skipped, [])
        self.assertEqual([graph['root_pid'] for graph in graphs], [ROOT])
        self.assertEqual(graphs[0]['ordered_target_pids'], proof['outgoing_target_pids'])

        seed = json.loads(self.manifest_path.read_text(encoding='utf-8'))['seeds'][0]
        expected_sources = {
            'track_snapshot': self.track_path,
            'identity_map': self.identity_path,
            'collector_snapshot': self.snapshot_path,
            'collector_summary': self.summary_path,
            'collector_seed_manifest': self.manifest_path,
            'batch_intent': self.intent_path,
            'seed_plan': self.seed_plan_path,
            'seed_video_evidence': self.seed_evidence_path,
        }
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            proved_hint = radio_growth_merge._verified_search_derived_seed_hint(
                seed, [snapshot_path], LIBRARY, self.map_hash, expected_sources)
            self.assertEqual(proved_hint, {
                'video_id': self.arguments['videoId'],
                'source': 'authenticated_search_seed_proof'})
            self.assertEqual(
                radio_growth_merge._validate_collector_seed_video_binding(
                    seed, {}, [snapshot_path],
                    'authenticated-watch-radio-only-v1', LIBRARY, self.map_hash,
                    expected_sources),
                proved_hint)
            copied_summary = self.write(
                'search-bound-foreign-summary.json',
                json.loads(self.summary_path.read_text(encoding='utf-8')))
            with self.assertRaisesRegex(ValueError, 'current collector source'):
                radio_growth_merge._verified_search_derived_seed_hint(
                    seed, [snapshot_path], LIBRARY, self.map_hash,
                    {**expected_sources, 'collector_summary': copied_summary})
            with self.assertRaisesRegex(ValueError, 'strict saved-video evidence'):
                radio_growth_merge._validate_collector_seed_video_binding(
                    seed, {}, [snapshot_path], None, LIBRARY, self.map_hash,
                    expected_sources)
            self.assertIsNone(radio_growth_merge._verified_search_derived_seed_hint(
                {**seed, 'seed_video_id': 'wrongvideo1'},
                [snapshot_path], LIBRARY, self.map_hash, expected_sources))
            with self.assertRaisesRegex(ValueError, 'strict saved-video evidence'):
                radio_growth_merge._validate_collector_seed_video_binding(
                    {**seed, 'seed_video_id': 'wrongvideo1'}, {}, [snapshot_path],
                    'authenticated-watch-radio-only-v1', LIBRARY, self.map_hash,
                    expected_sources)
            self.assertIsNone(radio_growth_merge._verified_search_derived_seed_hint(
                {**seed, 'persistent_id': TARGET},
                [snapshot_path], LIBRARY, self.map_hash, expected_sources))
            with self.assertRaisesRegex(ValueError, 'multiple search-derived'):
                radio_growth_merge._verified_search_derived_seed_hint(
                    seed, [snapshot_path, snapshot_path], LIBRARY, self.map_hash,
                    expected_sources)
            changed_snapshot = copy.deepcopy(sanitized)
            changed_snapshot['selected_video_id'] = 'wrongvideo1'
            changed_path = self.write('search-bound-wrong-video.json', changed_snapshot)
            with self.assertRaisesRegex(ValueError, 'does not bind'):
                radio_growth_merge._verified_search_derived_seed_hint(
                    seed, [changed_path], LIBRARY, self.map_hash, expected_sources)
            unproved = copy.deepcopy(sanitized)
            unproved.pop('radio_only_source_proof')
            unproved_path = self.write('search-bound-no-proof.json', unproved)
            self.assertIsNone(radio_growth_merge._verified_search_derived_seed_hint(
                seed, [unproved_path], LIBRARY, self.map_hash, expected_sources))

        unproved = copy.deepcopy(sanitized)
        unproved.pop('radio_only_source_proof')
        forged_path = self.write('search-bound-unproved.json', unproved)
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            with self.assertRaisesRegex(ValueError, 'No valid recommendation snapshots'):
                collect_graphs(
                    [forged_path], self.matcher, LIBRARY,
                    source_policy='authenticated-watch-radio-only-v1',
                    identity_map_sha256=self.map_hash)

    def test_merge_types_only_fully_replayed_search_strict_ineligibility(self):
        self.configure_search_derived_seed()
        snapshot = json.loads(self.snapshot_path.read_text(encoding='utf-8'))
        raw_radio = json.loads(self.cache_path.read_text(encoding='utf-8'))
        raw_radio['response']['tracks'][0]['album']['name'] = 'Different Album'
        self.cache_path.write_text(json.dumps(raw_radio, ensure_ascii=False), encoding='utf-8')
        search_cache = json.loads(self.search_cache_path.read_text(encoding='utf-8'))
        search_cache['response'][0]['album']['name'] = 'Different Album'
        search_cache['response'] = search_cache['response'][:1]
        self.search_cache_path.write_text(json.dumps(search_cache, ensure_ascii=False),
                                          encoding='utf-8')
        snapshot['observations'] = observe(
            raw_radio['response']['tracks'], 'radio', self.observed_at,
            self.tracks, self.arguments['videoId'], candidate_matcher=self.matcher.match)
        self.snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
        summary = json.loads(self.summary_path.read_text(encoding='utf-8'))
        summary['results'] = [{**collector_result_summary(snapshot), 'status': 'collected'}]
        self.summary_path.write_text(json.dumps(summary), encoding='utf-8')
        batch, manifest_path, summary_path, evidence_path, snapshot_paths = \
            self._relocate_search_fixture_to_radio_only_batch(snapshot, summary)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        seed = manifest['seeds'][0]
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            receipt = radio_growth_merge._collected_seed_ineligible_disposition(
                seed, {}, source_policy='authenticated-watch-radio-only-v1',
                collector_summary=json.loads(summary_path.read_text()),
                collector_summary_path=summary_path,
                seed_manifest=manifest, seed_manifest_path=manifest_path,
                seed_video_evidence_path=evidence_path,
                snapshot_paths=snapshot_paths, track_snapshot_path=self.track_path,
                identity_map_path=self.identity_path, library_hash=LIBRARY,
                identity_hash=self.map_hash)
        self.assertEqual(receipt['status'], 'collected_seed_strictly_ineligible')
        self.assertEqual(receipt['reason'], 'seed_not_strictly_matched')
        self.assertEqual(receipt['details']['seed_video_search_binding']['kind'],
                         'authenticated-search-resolver-seed-ineligible-v1')
        self.assertEqual(receipt['accepted_edges'], [])

    def _configure_search_bound_seed_without_targets(self):
        self.configure_search_derived_seed()
        self.raw_tracks = copy.deepcopy(self.raw_tracks)
        self.raw_tracks[1]['album']['name'] = 'Unmatched Album'
        self.cache_path = self.write('no-target-radio-cache.json', {
            'endpoint': 'get_watch_playlist', 'arguments': self.arguments,
            'observed_at': self.observed_at, 'response': {'tracks': self.raw_tracks},
        })
        snapshot = json.loads(self.snapshot_path.read_text(encoding='utf-8'))
        snapshot['observations'] = observe(
            self.raw_tracks, 'radio', self.observed_at, self.tracks,
            self.arguments['videoId'], candidate_matcher=self.matcher.match)
        self.snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False),
                                      encoding='utf-8')
        summary = json.loads(self.summary_path.read_text(encoding='utf-8'))
        summary['results'] = [{**collector_result_summary(snapshot), 'status': 'collected'}]
        self.summary_path.write_text(json.dumps(summary, ensure_ascii=False),
                                     encoding='utf-8')
        shutil.rmtree(self.root / 'typed-radio-only-batch', ignore_errors=True)
        return self._relocate_search_fixture_to_radio_only_batch(snapshot, summary)

    def test_authenticated_strict_seed_with_zero_targets_is_typed_without_edges(self):
        batch, manifest_path, summary_path, evidence_path, snapshot_paths = \
            self._configure_search_bound_seed_without_targets()
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        seed = manifest['seeds'][0]
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            receipt = radio_growth_merge._collected_seed_ineligible_disposition(
                seed, {}, source_policy='authenticated-watch-radio-only-v1',
                collector_summary=json.loads(summary_path.read_text()),
                collector_summary_path=summary_path, seed_manifest=manifest,
                seed_manifest_path=manifest_path, seed_video_evidence_path=evidence_path,
                snapshot_paths=snapshot_paths, track_snapshot_path=self.track_path,
                identity_map_path=self.identity_path, library_hash=LIBRARY,
                identity_hash=self.map_hash)
        self.assertEqual(receipt['status'], 'collected_seed_no_strict_outgoing_targets')
        self.assertEqual(receipt['reason'], 'no_strict_unique_outgoing_targets')
        self.assertEqual(receipt['details']['accepted_edges'], [])
        binding = receipt['details']['seed_video_search_binding']
        self.assertEqual(binding['kind'], 'authenticated-search-strict-unique-seed-v1')
        self.assertEqual(binding['persistent_id'], ROOT)
        self.assertEqual(binding['strict_unique_video_ids'], [self.arguments['videoId']])
        self.assertEqual(receipt['accepted_edges'], [])
        self.assertEqual(sum(row['name'] == 'raw_radio_cache'
                             for row in receipt['source_receipts']), 1)
        self.assertEqual(sum(row['name'] == 'raw_search_cache'
                             for row in receipt['source_receipts']), 1)

    def test_zero_target_disposition_rejects_observation_or_raw_cache_tampering(self):
        for index, tamper in enumerate(('observations', 'raw_cache')):
            with self.subTest(tamper=tamper):
                if index:
                    self.tearDown()
                    self.setUp()
                batch, manifest_path, summary_path, evidence_path, snapshot_paths = \
                    self._configure_search_bound_seed_without_targets()
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                collector_snapshot = manifest_path.parent / (ROOT + '.json')
                snapshot = json.loads(collector_snapshot.read_text(encoding='utf-8'))
                if tamper == 'observations':
                    snapshot['observations'] = []
                    collector_snapshot.write_text(json.dumps(snapshot, ensure_ascii=False),
                                                  encoding='utf-8')
                    summary = json.loads(summary_path.read_text(encoding='utf-8'))
                    summary['results'] = [{**collector_result_summary(snapshot),
                                           'status': 'collected'}]
                    summary_path.write_text(json.dumps(summary, ensure_ascii=False),
                                             encoding='utf-8')
                else:
                    cache_path = radio_proof._request_cache_path(
                        collector_snapshot, 'get_watch_playlist', self.arguments)
                    cache = json.loads(cache_path.read_text(encoding='utf-8'))
                    cache['response']['tracks'][1]['album']['name'] = 'Forged Cache Album'
                    cache_path.write_text(json.dumps(cache, ensure_ascii=False),
                                          encoding='utf-8')
                with patch('radio_snapshot_proof.load_library',
                           return_value=(self.tracks, LIBRARY, None)), \
                        patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
                    with self.assertRaisesRegex(ValueError, 'Raw radio response does not reproduce'):
                        radio_growth_merge._collected_seed_ineligible_disposition(
                            manifest['seeds'][0], {},
                            source_policy='authenticated-watch-radio-only-v1',
                            collector_summary=json.loads(summary_path.read_text()),
                            collector_summary_path=summary_path, seed_manifest=manifest,
                            seed_manifest_path=manifest_path,
                            seed_video_evidence_path=evidence_path,
                            snapshot_paths=snapshot_paths,
                            track_snapshot_path=self.track_path,
                            identity_map_path=self.identity_path, library_hash=LIBRARY,
                            identity_hash=self.map_hash)

    def test_merge_refuses_to_type_a_fully_eligible_seed_as_ineligible(self):
        self.configure_search_derived_seed()
        snapshot = json.loads(self.snapshot_path.read_text(encoding='utf-8'))
        summary = json.loads(self.summary_path.read_text(encoding='utf-8'))
        batch, manifest_path, summary_path, evidence_path, snapshot_paths = \
            self._relocate_search_fixture_to_radio_only_batch(snapshot, summary)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            result = radio_growth_merge._collected_seed_ineligible_disposition(
                manifest['seeds'][0], {}, source_policy='authenticated-watch-radio-only-v1',
                collector_summary=json.loads(summary_path.read_text()),
                collector_summary_path=summary_path, seed_manifest=manifest,
                seed_manifest_path=manifest_path,
                seed_video_evidence_path=evidence_path,
                snapshot_paths=snapshot_paths, track_snapshot_path=self.track_path,
                identity_map_path=self.identity_path, library_hash=LIBRARY,
                identity_hash=self.map_hash)
        self.assertIsNone(result)

    def test_hintless_snapshot_is_deferred_to_strict_proof_validator(self):
        self.configure_search_derived_seed()
        snapshot = json.loads(self.snapshot_path.read_text(encoding='utf-8'))
        summary = json.loads(self.summary_path.read_text(encoding='utf-8'))
        batch, manifest_path, summary_path, evidence_path, _ = \
            self._relocate_search_fixture_to_radio_only_batch(snapshot, summary)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        seed = manifest['seeds'][0]
        forged = self.write('hintless-forged-snapshot.json', {'local_seed': seed})
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            result = radio_growth_merge._collected_seed_ineligible_disposition(
                seed, {}, source_policy='authenticated-watch-radio-only-v1',
                collector_summary=json.loads(summary_path.read_text()),
                collector_summary_path=summary_path, seed_manifest=manifest,
                seed_manifest_path=manifest_path,
                seed_video_evidence_path=evidence_path,
                snapshot_paths=[forged], track_snapshot_path=self.track_path,
                identity_map_path=self.identity_path, library_hash=LIBRARY,
                identity_hash=self.map_hash)
        self.assertIsNone(result)
        with self.assertRaisesRegex(ValueError, 'complete current collector source receipts'):
            radio_growth_merge._validate_collector_seed_video_binding(
                seed, {}, [forged], 'authenticated-watch-radio-only-v1',
                LIBRARY, self.map_hash, {})

    def test_standard_batch_proof_path_is_deferred_but_forgery_and_missing_input_fail(self):
        self.configure_search_derived_seed()
        snapshot = json.loads(self.snapshot_path.read_text(encoding='utf-8'))
        summary = json.loads(self.summary_path.read_text(encoding='utf-8'))
        batch, manifest_path, summary_path, evidence_path, _ = \
            self._relocate_search_fixture_to_radio_only_batch(snapshot, summary)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        seed = manifest['seeds'][0]
        collector_snapshot = manifest_path.parent / (ROOT + '.json')
        intent_path = batch / 'intent.json'
        raw_radio_path = radio_proof._request_cache_path(
            collector_snapshot, 'get_watch_playlist', self.arguments)
        proof_dir = batch / 'radio-only-source'
        proof_path = proof_dir / (ROOT + '.proof.json')
        derived_path = proof_dir / (ROOT + '.snapshot.json')
        proof_dir.mkdir(parents=True)
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            create_radio_snapshot_proof(
                track_snapshot_path=self.track_path, identity_map_path=self.identity_path,
                snapshot_path=collector_snapshot, collector_summary_path=summary_path,
                collector_seed_manifest_path=manifest_path,
                batch_intent_path=intent_path, raw_radio_cache_path=raw_radio_path,
                output_path=proof_path, sealed_at_utc='2026-10-10T12:10:00+00:00')
            sanitized = radio_only_snapshot_from_proof(proof_path)
        derived_path.write_text(json.dumps(sanitized, ensure_ascii=False), encoding='utf-8')
        expected_sources = {
            'track_snapshot': self.track_path,
            'identity_map': self.identity_path,
            'collector_snapshot': collector_snapshot,
            'collector_summary': summary_path,
            'collector_seed_manifest': manifest_path,
            'batch_intent': intent_path,
            'seed_plan': batch / 'seed-plan.json',
            'seed_video_evidence': evidence_path,
        }
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            # Recovery writes proof products under this standard batch path.
            # The ineligible classifier must defer the matching PID to the
            # ordinary proof consumer instead of rejecting the mere files.
            self.assertIsNone(radio_growth_merge._collected_seed_ineligible_disposition(
                seed, {}, source_policy='authenticated-watch-radio-only-v1',
                collector_summary=json.loads(summary_path.read_text()),
                collector_summary_path=summary_path, seed_manifest=manifest,
                seed_manifest_path=manifest_path,
                seed_video_evidence_path=evidence_path,
                snapshot_paths=[derived_path], track_snapshot_path=self.track_path,
                identity_map_path=self.identity_path, library_hash=LIBRARY,
                identity_hash=self.map_hash))
            accepted = radio_growth_merge._validate_collector_seed_video_binding(
                seed, {}, [derived_path], 'authenticated-watch-radio-only-v1',
                LIBRARY, self.map_hash, expected_sources)
            self.assertEqual(accepted['video_id'], self.arguments['videoId'])

            forged = copy.deepcopy(sanitized)
            forged['observations'] = []
            forged_path = self.write('standard-path-forged-snapshot.json', forged)
            self.assertIsNone(radio_growth_merge._collected_seed_ineligible_disposition(
                seed, {}, source_policy='authenticated-watch-radio-only-v1',
                collector_summary=json.loads(summary_path.read_text()),
                collector_summary_path=summary_path, seed_manifest=manifest,
                seed_manifest_path=manifest_path,
                seed_video_evidence_path=evidence_path,
                snapshot_paths=[forged_path], track_snapshot_path=self.track_path,
                identity_map_path=self.identity_path, library_hash=LIBRARY,
                identity_hash=self.map_hash))
            with self.assertRaisesRegex(ValueError, 'differs from its proof'):
                radio_growth_merge._validate_collector_seed_video_binding(
                    seed, {}, [forged_path], 'authenticated-watch-radio-only-v1',
                    LIBRARY, self.map_hash, expected_sources)

            # Unexpected proof files remain fatal when no matching graph
            # snapshot is supplied; they cannot be hidden by an empty list.
            with self.assertRaisesRegex(ValueError, 'unexpectedly has a proof'):
                radio_growth_merge._collected_seed_ineligible_disposition(
                    seed, {}, source_policy='authenticated-watch-radio-only-v1',
                    collector_summary=json.loads(summary_path.read_text()),
                    collector_summary_path=summary_path, seed_manifest=manifest,
                    seed_manifest_path=manifest_path,
                    seed_video_evidence_path=evidence_path,
                    snapshot_paths=[], track_snapshot_path=self.track_path,
                    identity_map_path=self.identity_path, library_hash=LIBRARY,
                    identity_hash=self.map_hash)

    def _relocate_search_fixture_to_radio_only_batch(self, snapshot, summary):
        batch = self.root / 'typed-radio-only-batch' / 'batch-0001'
        collector = batch / 'collector'
        request_dir = collector / 'requests'
        request_dir.mkdir(parents=True)
        snapshot = copy.deepcopy(snapshot)
        snapshot['collector_collection_mode'] = 'radio-only-v1'
        snapshot['related_status'] = 'not_requested'
        snapshot['requests'] = [{
            'endpoint': 'watch_radio', 'observed_at': self.observed_at,
            'returned_track_count': len(self.raw_tracks)}]
        snapshot_path = collector / (ROOT + '.json')
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
        summary = copy.deepcopy(summary)
        summary['collector_collection_mode'] = 'radio-only-v1'
        summary['results'] = [{**collector_result_summary(snapshot), 'status': 'collected'}]
        summary_path = collector / 'summary-1.json'
        summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding='utf-8')
        manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        manifest['collector_collection_mode'] = 'radio-only-v1'
        manifest_path = collector / 'seeds.json'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        intent = json.loads(self.intent_path.read_text(encoding='utf-8'))
        plan_path = batch / 'seed-plan.json'
        evidence_path = batch / 'seed-video-evidence.json'
        shutil.copyfile(self.seed_plan_path, plan_path)
        shutil.copyfile(self.seed_evidence_path, evidence_path)
        intent['seed_plan'] = {'path': str(plan_path.resolve()),
                               'sha256': self.digest(plan_path)}
        intent['seed_video_evidence'] = {'path': str(evidence_path.resolve()),
                                         'sha256': self.digest(evidence_path)}
        intent['runner_config']['collector_collection_mode'] = 'radio-only-v1'
        intent_path = batch / 'intent.json'
        intent_path.write_text(json.dumps(intent, ensure_ascii=False), encoding='utf-8')
        for endpoint, arguments, source in (
                ('get_watch_playlist', self.arguments, self.cache_path),
                ('search', {'query': 'Seed Song Seed Artist', 'filter': 'songs', 'limit': 5},
                 self.search_cache_path)):
            key = json.dumps([endpoint, arguments], sort_keys=True,
                             ensure_ascii=False).encode('utf-8')
            target = request_dir / (hashlib.sha256(key).hexdigest() + '.json')
            shutil.copyfile(source, target)
        return (batch, manifest_path, summary_path, evidence_path, [])

    def test_merge_does_not_type_bad_saved_observations_as_ineligible(self):
        self.configure_search_derived_seed()
        snapshot = json.loads(self.snapshot_path.read_text(encoding='utf-8'))
        snapshot['observations'] = []
        self.snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
        summary = json.loads(self.summary_path.read_text(encoding='utf-8'))
        summary['results'] = [{**collector_result_summary(snapshot), 'status': 'collected'}]
        self.summary_path.write_text(json.dumps(summary), encoding='utf-8')
        batch, manifest_path, summary_path, evidence_path, snapshot_paths = \
            self._relocate_search_fixture_to_radio_only_batch(snapshot, summary)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        seed = manifest['seeds'][0]
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            with self.assertRaisesRegex(ValueError, 'Raw radio response does not reproduce'):
                radio_growth_merge._collected_seed_ineligible_disposition(
                    seed, {}, source_policy='authenticated-watch-radio-only-v1',
                    collector_summary=json.loads(summary_path.read_text()),
                    collector_summary_path=summary_path,
                    seed_manifest=manifest, seed_manifest_path=manifest_path,
                    seed_video_evidence_path=evidence_path,
                    snapshot_paths=snapshot_paths, track_snapshot_path=self.track_path,
                    identity_map_path=self.identity_path, library_hash=LIBRARY,
                    identity_hash=self.map_hash)

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

    def test_search_derived_strict_seed_failure_is_source_bound_ineligible(self):
        self.configure_search_derived_seed()
        # Keep the resolver's exact-album preference, but make the observed
        # seed credit partial in both independent source responses. The full
        # search and radio replays must complete before strict seed rejection.
        snapshot = json.loads(self.snapshot_path.read_text(encoding='utf-8'))
        radio_cache = json.loads(self.cache_path.read_text(encoding='utf-8'))
        radio_cache['response']['tracks'][0]['artists'] = [
            {'name': 'Seed Artist', 'id': 'UCseed'},
            {'name': 'VISUAL ARTS / Key', 'id': 'UCkey'},
        ]
        self.cache_path.write_text(json.dumps(radio_cache, ensure_ascii=False),
                                   encoding='utf-8')
        search_cache = json.loads(self.search_cache_path.read_text(encoding='utf-8'))
        search_cache['response'][0]['artists'] = [
            {'name': 'Seed Artist', 'id': 'UCseed'},
            {'name': 'VISUAL ARTS / Key', 'id': 'UCkey'},
        ]
        self.search_cache_path.write_text(json.dumps(search_cache, ensure_ascii=False),
                                          encoding='utf-8')
        snapshot['observations'] = observe(
            radio_cache['response']['tracks'], 'radio', self.observed_at,
            self.tracks, self.arguments['videoId'], candidate_matcher=self.matcher.match)
        self.snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False),
                                      encoding='utf-8')
        summary = json.loads(self.summary_path.read_text(encoding='utf-8'))
        summary['results'] = [{**collector_result_summary(snapshot), 'status': 'collected'}]
        self.summary_path.write_text(json.dumps(summary, ensure_ascii=False),
                                     encoding='utf-8')

        output = self.root / 'strict-seed-ineligible-proof.json'
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            with self.assertRaises(RadioSnapshotIneligible) as caught:
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
        error = caught.exception
        self.assertEqual(error.persistent_id, ROOT)
        self.assertEqual(error.reason, 'seed_not_strictly_matched')
        self.assertFalse(output.exists())
        binding = error.details['seed_video_search_binding']
        self.assertEqual(binding['kind'],
                         'authenticated-search-resolver-seed-ineligible-v1')
        self.assertEqual(binding['resolver']['video_id'], self.arguments['videoId'])
        self.assertEqual(binding['resolver']['candidate_video_count'], 2)
        self.assertEqual(binding['resolver']['status'],
                         'unique_album_metadata_preference_unverified')
        self.assertFalse(binding['strict_unique'])
        self.assertEqual(binding['raw_search_cache']['sha256'], self.digest(self.search_cache_path))
        self.assertEqual(binding['request']['arguments'], {
            'query': 'Seed Song Seed Artist', 'filter': 'songs', 'limit': 5})
        self.assertEqual(error.details['candidate_dispositions'][0]['artist_credit_match'],
                         'partial')

        snapshot_bytes = self.snapshot_path.read_bytes()
        radio_cache_bytes = self.cache_path.read_bytes()
        changed_snapshot = json.loads(snapshot_bytes)
        changed_snapshot['observations'][0]['track']['title'] = 'Forged observed seed'
        self.snapshot_path.write_text(json.dumps(changed_snapshot, ensure_ascii=False),
                                      encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            with self.assertRaisesRegex(ValueError, 'does not reproduce'):
                create_radio_snapshot_proof(
                    track_snapshot_path=self.track_path,
                    identity_map_path=self.identity_path,
                    snapshot_path=self.snapshot_path,
                    collector_summary_path=self.summary_path,
                    collector_seed_manifest_path=self.manifest_path,
                    batch_intent_path=self.intent_path,
                    raw_radio_cache_path=self.cache_path,
                    output_path=self.root / 'tampered-observation.json',
                    sealed_at_utc='2026-10-10T12:10:00+00:00')
        self.snapshot_path.write_bytes(snapshot_bytes)

        malformed_radio_cache = json.loads(radio_cache_bytes)
        malformed_radio_cache['response']['tracks'] = []
        self.cache_path.write_text(json.dumps(malformed_radio_cache), encoding='utf-8')
        with patch('radio_snapshot_proof.load_library',
                   return_value=(self.tracks, LIBRARY, None)), \
                patch('radio_snapshot_proof.select_seed_video_evidence', return_value={}):
            with self.assertRaisesRegex(ValueError, 'response count differs'):
                create_radio_snapshot_proof(
                    track_snapshot_path=self.track_path,
                    identity_map_path=self.identity_path,
                    snapshot_path=self.snapshot_path,
                    collector_summary_path=self.summary_path,
                    collector_seed_manifest_path=self.manifest_path,
                    batch_intent_path=self.intent_path,
                    raw_radio_cache_path=self.cache_path,
                    output_path=self.root / 'malformed-radio-cache.json',
                    sealed_at_utc='2026-10-10T12:10:00+00:00')

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
