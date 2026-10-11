"""Typed negative receipts for authenticated, uncollected search attempts."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import merge_rebased_radio_growth as merger


POLICY = 'authenticated-watch-radio-only-v1'
PID = 'A1B2C3D4E5F60718'
LIBRARY_HASH = 'a' * 64


class UnresolvedSeedDispositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.batch = self.root / 'batch-0001'
        self.collector_dir = self.batch / 'collector'
        self.radio_dir = self.batch / 'radio-only-source'
        self.collector_dir.mkdir(parents=True)
        self.radio_dir.mkdir()
        self.track_path = self.root / 'track-snapshot.json'
        self.map_path = self.root / 'identity-map.json'
        self.baseline_state_path = self.root / 'baseline-state.json'
        self.baseline_graph_path = self.root / 'baseline-graphs.json'
        self.rebase_path = self.root / 'rebase-report.json'
        self.radio_path = self.batch / 'current-radio-evidence.json'
        self.evidence_path = self.batch / 'seed-video-evidence.json'
        self.plan_path = self.batch / 'seed-plan.json'
        self.manifest_path = self.collector_dir / 'seeds.json'
        self.summary_path = self.collector_dir / 'summary-1.json'
        self.intent_path = self.batch / 'intent.json'
        self.track = {'source_sha256': LIBRARY_HASH, 'tracks': []}
        self.identity_map = {'fixture': 'map'}
        self.evidence = {'schema_version': 1, 'rows': []}
        self.plan = {'source_library_sha256': LIBRARY_HASH,
                     'seeds': [{'persistent_id': PID}]}
        self.manifest = {
            'library_sha256': LIBRARY_HASH,
            'identity_map_sha256': None,
            'seed_video_evidence_sha256': None,
            'seed_plan_sha256': None,
            'seeds': [{'persistent_id': PID, 'seed_video_id': None,
                       'seed_video_source': None}],
        }
        self.write_json(self.track_path, self.track)
        self.write_json(self.map_path, self.identity_map)
        self.write_json(self.baseline_state_path, {'fixture': 'state'})
        self.write_json(self.baseline_graph_path, {'fixture': 'graphs'})
        self.write_json(self.rebase_path, {'fixture': 'rebase'})
        self.write_json(self.radio_path, {'fixture': 'radio'})
        self.evidence['current_radio_source'] = {
            'path': str(self.radio_path.resolve()), 'sha256': self.sha(self.radio_path)}
        self.identity_hash = self.sha(self.map_path)
        self.manifest['identity_map_sha256'] = self.identity_hash
        self.write_json(self.plan_path, self.plan)
        self.manifest['seed_plan_sha256'] = self.sha(self.plan_path)
        self.write_json(self.evidence_path, self.evidence)
        self.manifest['seed_video_evidence_sha256'] = self.sha(self.evidence_path)
        self.write_json(self.manifest_path, self.manifest)
        self.summary = {
            'schema_version': 1, 'status': 'completed',
            'account_authentication_verified': True,
            'collector_collection_mode': 'radio-only-v1',
            'library_sha256': LIBRARY_HASH, 'http_requests': 5,
            'request_budget': 90,
            'finished_at': '2026-10-11T16:02:38.256582+00:00',
            'results': [{'persistent_id': PID, 'status': 'search_unresolved',
                         'candidate_video_ids': 0, 'title_fallback_attempted': False}],
        }
        self.write_json(self.summary_path, self.summary)
        self.config = {
            'track_snapshot': {'path': str(self.track_path.resolve()),
                               'sha256': self.sha(self.track_path)},
            'identity_map': {'path': str(self.map_path.resolve()),
                             'sha256': self.identity_hash},
            'baseline_state': {'path': str(self.baseline_state_path.resolve()),
                               'sha256': self.sha(self.baseline_state_path)},
            'baseline_graph_export': {'path': str(self.baseline_graph_path.resolve()),
                                      'sha256': self.sha(self.baseline_graph_path)},
            'rebase_report': {'path': str(self.rebase_path.resolve()),
                              'sha256': self.sha(self.rebase_path)},
            'source_policy': POLICY,
            'collector_collection_mode': 'radio-only-v1',
            'batch_request_budget': 90,
        }
        self.intent = {
            'schema_version': 1, 'runner_config': self.config,
            'runner_config_sha256': self.sha_json(self.config),
            'source_policy': POLICY, 'library_sha256': LIBRARY_HASH,
            'identity_map_sha256': self.identity_hash,
            'batch': 1, 'seed_pids': [PID], 'request_budget': 90,
            'source_inputs': [
                {'path': str(path.resolve()), 'sha256': self.sha(path)}
                for path in (self.track_path, self.map_path,
                             self.baseline_state_path, self.baseline_graph_path,
                             self.rebase_path)],
            'seed_plan': {'path': str(self.plan_path.resolve()),
                          'sha256': self.sha(self.plan_path)},
            'seed_video_evidence': {'path': str(self.evidence_path.resolve()),
                                    'sha256': self.sha(self.evidence_path)},
            'current_radio_evidence': {'path': str(self.radio_path.resolve()),
                                       'sha256': self.sha(self.radio_path)},
        }
        self.write_json(self.intent_path, self.intent)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def sha_bytes(data):
        return hashlib.sha256(data).hexdigest()

    def sha(self, path):
        return self.sha_bytes(Path(path).read_bytes())

    def sha_json(self, value):
        data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')
        return self.sha_bytes(data)

    @staticmethod
    def write_json(path, value):
        Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def disposition(self, **overrides):
        args = {
            'seed': self.manifest['seeds'][0], 'selected_hints': {},
            'source_policy': POLICY, 'collector_summary': self.summary,
            'collector_summary_path': self.summary_path,
            'seed_manifest': self.manifest,
            'seed_manifest_path': self.manifest_path,
            'seed_video_evidence_path': self.evidence_path,
            'snapshot_paths': [], 'library_hash': LIBRARY_HASH,
            'identity_hash': self.identity_hash,
            'expected_source_paths': {'track_snapshot': self.track_path,
                                      'identity_map': self.map_path},
        }
        args.update(overrides)
        return merger._unresolved_seed_disposition(**args)

    def test_authenticated_unresolved_seed_gets_receipt_and_absence_seals(self):
        receipt = self.disposition()
        self.assertEqual(receipt['producer'],
                         'opengenius-authenticated-search-unresolved-seed-v1')
        self.assertEqual(receipt['persistent_id'], PID)
        self.assertEqual(receipt['status'], 'search_unresolved')
        self.assertEqual(set(receipt), {
            'schema_version', 'producer', 'persistent_id', 'status',
            'candidate_video_ids', 'title_fallback_attempted', 'source_policy',
            'collector_summary', 'batch_intent', 'seed_manifest', 'seed_plan',
            'seed_video_evidence', 'current_radio_evidence',
            'absent_snapshot_paths'})
        self.assertEqual(len(receipt['absent_snapshot_paths']), 3)
        self.assertTrue(all(row['exists'] is False
                            for row in receipt['absent_snapshot_paths']))

    def test_collected_without_hint_does_not_get_unresolved_disposition(self):
        self.summary['results'][0]['status'] = 'collected'
        self.assertIsNone(self.disposition())
        with self.assertRaisesRegex(ValueError, 'complete current collector source receipts'):
            merger._validate_collector_seed_video_binding(
                self.manifest['seeds'][0], {}, [], POLICY, LIBRARY_HASH,
                self.identity_hash, {})

    def test_unauthenticated_or_nonzero_unresolved_candidate_is_fatal(self):
        self.summary['account_authentication_verified'] = False
        with self.assertRaisesRegex(ValueError, 'authenticated current collector summary'):
            self.disposition()
        self.summary['account_authentication_verified'] = True
        self.summary['results'][0]['candidate_video_ids'] = 1
        with self.assertRaisesRegex(ValueError, 'current Library search context'):
            self.disposition()

    def test_nonzero_unresolved_candidate_replays_exact_batch_local_search(self):
        seed = self.manifest['seeds'][0]
        seed.update({'title': 'Seed Song', 'artist': 'Seed Artist', 'album': 'Seed Album'})
        self.write_json(self.manifest_path, self.manifest)
        self.summary['results'][0]['candidate_video_ids'] = 2
        self.write_json(self.summary_path, self.summary)
        arguments = {'query': 'Seed Song Seed Artist', 'filter': 'songs', 'limit': 5}
        key = json.dumps(['search', arguments], sort_keys=True, ensure_ascii=False).encode('utf-8')
        search_path = self.collector_dir / 'requests' / (self.sha_bytes(key) + '.json')
        search_path.parent.mkdir(parents=True, exist_ok=True)
        self.write_json(search_path, {
            'endpoint': 'search', 'arguments': arguments,
            'observed_at': '2026-10-11T16:02:01.986492+00:00',
            'response': [
                {'videoId': 'abcdefghijk', 'title': 'Seed Song', 'album': {'name': 'Seed Album'}},
                {'videoId': 'lmnopqrstuv', 'title': 'Seed Song', 'album': {'name': 'Seed Album'}},
            ],
        })
        class Matcher:
            @staticmethod
            def match(row):
                return [{'persistent_id': PID}]
        receipt = self.disposition(tracks=[seed], matcher=Matcher())
        self.assertEqual(receipt['candidate_video_ids'], 2)
        self.assertEqual(len(receipt['search_request_receipts']), 1)
        self.assertEqual(receipt['search_request_receipts'][0]['path'], str(search_path.resolve()))
        self.assertEqual(receipt['search_request_receipts'][0]['sha256'], self.sha(search_path))

    def test_nonzero_unresolved_rejects_title_fallback_mode(self):
        seed = self.manifest['seeds'][0]
        seed.update({'title': 'Seed Song', 'artist': 'Seed Artist', 'album': 'Seed Album'})
        self.manifest['seed_query_fallback'] = True
        self.write_json(self.manifest_path, self.manifest)
        result = self.summary['results'][0]
        result.update({'candidate_video_ids': 2, 'title_fallback_attempted': True,
                       'title_fallback_candidate_video_ids': 0})
        self.write_json(self.summary_path, self.summary)
        with self.assertRaisesRegex(ValueError, 'fallback'):
            self.disposition(tracks=[seed], matcher=object())

    def test_nonzero_unresolved_rejects_malformed_fallback_flags(self):
        seed = self.manifest['seeds'][0]
        seed.update({'title': 'Seed Song', 'artist': 'Seed Artist', 'album': 'Seed Album'})
        self.manifest['seed_query_fallback'] = 'false'
        self.write_json(self.manifest_path, self.manifest)
        self.summary['results'][0]['candidate_video_ids'] = 2
        self.write_json(self.summary_path, self.summary)
        with self.assertRaisesRegex(ValueError, 'fallback'):
            self.disposition(tracks=[seed], matcher=object())

    def test_nonzero_unresolved_candidate_rejects_tampered_search_receipt(self):
        seed = self.manifest['seeds'][0]
        seed.update({'title': 'Seed Song', 'artist': 'Seed Artist', 'album': 'Seed Album'})
        self.write_json(self.manifest_path, self.manifest)
        self.summary['results'][0]['candidate_video_ids'] = 2
        self.write_json(self.summary_path, self.summary)
        arguments = {'query': 'Seed Song Seed Artist', 'filter': 'songs', 'limit': 5}
        key = json.dumps(['search', arguments], sort_keys=True, ensure_ascii=False).encode('utf-8')
        search_path = self.collector_dir / 'requests' / (self.sha_bytes(key) + '.json')
        search_path.parent.mkdir(parents=True, exist_ok=True)
        self.write_json(search_path, {
            'endpoint': 'search', 'arguments': {'query': 'wrong query', 'filter': 'songs', 'limit': 5},
            'observed_at': '2026-10-11T16:02:01.986492+00:00',
            'response': [{'videoId': 'abcdefghijk', 'title': 'Seed Song'},
                         {'videoId': 'lmnopqrstuv', 'title': 'Seed Song'}],
        })
        class Matcher:
            @staticmethod
            def match(row):
                return [{'persistent_id': PID}]
        with self.assertRaisesRegex(ValueError, 'exact request'):
            self.disposition(tracks=[seed], matcher=Matcher())

    def test_nonzero_unresolved_search_timestamp_must_be_inside_run(self):
        seed = self.manifest['seeds'][0]
        seed.update({'title': 'Seed Song', 'artist': 'Seed Artist', 'album': 'Seed Album'})
        self.write_json(self.manifest_path, self.manifest)
        self.summary['results'][0]['candidate_video_ids'] = 2
        self.write_json(self.summary_path, self.summary)
        arguments = {'query': 'Seed Song Seed Artist', 'filter': 'songs', 'limit': 5}
        key = json.dumps(['search', arguments], sort_keys=True, ensure_ascii=False).encode('utf-8')
        path = self.collector_dir / 'requests' / (self.sha_bytes(key) + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        self.write_json(path, {'endpoint': 'search', 'arguments': arguments,
                               'observed_at': '2026-10-11T16:02:39+00:00',
                               'response': [{'videoId': 'abcdefghijk', 'title': 'Seed Song'},
                                            {'videoId': 'lmnopqrstuv', 'title': 'Seed Song'}]})
        class Matcher:
            @staticmethod
            def match(row):
                return [{'persistent_id': PID}]
        with self.assertRaisesRegex(ValueError, 'outside the collector run window'):
            self.disposition(tracks=[seed], matcher=Matcher())

    def test_nonzero_unresolved_candidate_rejects_a_unique_resolver_result(self):
        seed = self.manifest['seeds'][0]
        seed.update({'title': 'Seed Song', 'artist': 'Seed Artist', 'album': 'Seed Album'})
        self.write_json(self.manifest_path, self.manifest)
        self.summary['results'][0]['candidate_video_ids'] = 2
        self.write_json(self.summary_path, self.summary)
        arguments = {'query': 'Seed Song Seed Artist', 'filter': 'songs', 'limit': 5}
        key = json.dumps(['search', arguments], sort_keys=True, ensure_ascii=False).encode('utf-8')
        search_path = self.collector_dir / 'requests' / (self.sha_bytes(key) + '.json')
        search_path.parent.mkdir(parents=True, exist_ok=True)
        self.write_json(search_path, {
            'endpoint': 'search', 'arguments': arguments,
            'observed_at': '2026-10-11T16:02:01.986492+00:00',
            'response': [{'videoId': 'abcdefghijk', 'title': 'Seed Song'}],
        })
        class Matcher:
            @staticmethod
            def match(row):
                return [{'persistent_id': PID}]
        self.summary['results'][0]['candidate_video_ids'] = 1
        self.write_json(self.summary_path, self.summary)
        with self.assertRaisesRegex(ValueError, 'does not replay'):
            self.disposition(tracks=[seed], matcher=Matcher())

    def test_unresolved_pid_must_match_intent_manifest_and_summary_order(self):
        self.intent['seed_pids'] = ['0000000000000001']
        self.intent['runner_config_sha256'] = self.sha_json(self.config)
        self.write_json(self.intent_path, self.intent)
        with self.assertRaisesRegex(ValueError, 'intent does not bind'):
            self.disposition()
        self.intent['seed_pids'] = [PID]
        self.write_json(self.intent_path, self.intent)
        self.summary['results'][0]['persistent_id'] = '0000000000000001'
        self.write_json(self.summary_path, self.summary)
        self.assertIsNone(self.disposition())

    def test_any_original_or_derived_snapshot_blocks_negative_receipt(self):
        self.write_json(self.collector_dir / (PID + '.json'), {'local_seed': {'persistent_id': PID}})
        with self.assertRaisesRegex(ValueError, 'snapshot or proof source'):
            self.disposition()
        (self.collector_dir / (PID + '.json')).unlink()
        self.write_json(self.radio_dir / (PID + '.proof.json'), {'untrusted': True})
        with self.assertRaisesRegex(ValueError, 'snapshot or proof source'):
            self.disposition()

    def test_source_receipt_mutation_is_rejected(self):
        self.config['identity_map']['sha256'] = '0' * 64
        self.intent['runner_config_sha256'] = self.sha_json(self.config)
        self.write_json(self.intent_path, self.intent)
        with self.assertRaisesRegex(ValueError, 'source hash is invalid'):
            self.disposition()

    def test_disposition_replay_rejects_changed_summary_bytes(self):
        original = self.disposition()
        self.summary['http_requests'] = 6
        self.write_json(self.summary_path, self.summary)
        saved_summary = self.summary
        self.summary = {**saved_summary, 'http_requests': 5}
        with self.assertRaisesRegex(ValueError, 'summary/manifest bytes differ'):
            self.disposition()
        self.assertEqual(original['persistent_id'], PID)

    def test_current_radio_source_must_match_combined_selector_sidecar(self):
        alternate = self.root / 'same-content-radio.json'
        alternate.write_bytes(self.radio_path.read_bytes())
        self.evidence['current_radio_source']['path'] = str(alternate.resolve())
        self.write_json(self.evidence_path, self.evidence)
        self.manifest['seed_video_evidence_sha256'] = self.sha(self.evidence_path)
        self.write_json(self.manifest_path, self.manifest)
        self.intent['seed_video_evidence']['sha256'] = self.sha(self.evidence_path)
        self.write_json(self.intent_path, self.intent)
        with self.assertRaisesRegex(ValueError, 'differs from the seed selector sidecar'):
            self.disposition()

    def test_only_radio_policy_can_record_unresolved_search(self):
        self.assertIsNone(self.disposition(source_policy=None))

    def test_result_schema_and_batch_location_are_bound(self):
        self.summary['results'][0]['observations'] = 0
        with self.assertRaisesRegex(ValueError, 'inconsistent search disposition'):
            self.disposition()
        del self.summary['results'][0]['observations']
        self.intent['batch'] = 2
        self.write_json(self.intent_path, self.intent)
        with self.assertRaisesRegex(ValueError, 'batch number'):
            self.disposition()

    def test_existing_nonstandard_supplied_snapshot_for_pid_is_fatal(self):
        snapshot = self.root / 'somewhere-else.json'
        self.write_json(snapshot, {'local_seed': {'persistent_id': PID}})
        with self.assertRaisesRegex(ValueError, 'supplied radio snapshot'):
            self.disposition(snapshot_paths=[snapshot])


if __name__ == '__main__':
    unittest.main()
