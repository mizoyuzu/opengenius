import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import genuine_root_recovery as recovery


LIBRARY = 'a' * 64
IDENTITY = 'b' * 64
CONFIG = 'c' * 64
RUNNER_CONFIG = {}
CONFIG = ''
PID = '0123456789ABCDEF'
VIDEO = 'abcdefghijk'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False).encode()
    path.write_bytes(data)
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(data).hexdigest()}


class GenuineRootRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.batch = self.root / 'batch-0001'
        self.batch.mkdir()
        self.source = self.root / 'tracks.json'
        self.source.write_text('{}', encoding='utf-8')
        self.source_receipt = {'path': str(self.source.resolve()),
                               'sha256': recovery.sha(self.source)}
        self.plan = self.batch / 'seed-plan.json'
        self.plan_receipt = write_json(self.plan, {
            'schema_version': 1, 'source_library_sha256': LIBRARY,
            'seeds': [{'persistent_id': PID, 'series': 'auto', 'sample_kind': 'auto'}],
        })
        self.evidence = self.batch / 'seed-video-evidence.json'
        self.evidence_receipt = write_json(self.evidence, {
            'schema_version': 1, 'producer': 'opengenius-seed-video-evidence-v1',
            'library_sha256': LIBRARY, 'identity_map_sha256': IDENTITY, 'rows': [],
        })
        self.parent_receipt = {'path': str((self.root / 'previous' / 'accepted-graphs.json').resolve()),
                               'accepted_graphs_sha256': 'd' * 64}
        global RUNNER_CONFIG, CONFIG
        (self.root / 'previous').mkdir()
        self.config_source = {'path': self.source_receipt['path'],
                              'sha256': self.source_receipt['sha256']}
        RUNNER_CONFIG = {
            'batch_request_budget': 90, 'deadline': None,
            'global_request_budget': 450,
            'track_snapshot': self.config_source,
            'identity_map': self.config_source,
            'baseline_state': self.config_source,
            'baseline_graph_export': self.config_source,
            'rebase_report': self.config_source,
            'external_seed_video_evidence': None,
            # The CLI accepts a growth directory and hashes its graph file;
            # recovery must normalize that directory to accepted-graphs.json.
            'previous_growth': {'path': str((self.root / 'previous').resolve()),
                                'sha256': self.parent_receipt['accepted_graphs_sha256']},
        }
        CONFIG = recovery.runner_config_sha256(RUNNER_CONFIG)
        self.intent = {
            'schema_version': 1, 'runner_config_sha256': CONFIG,
            'runner_config': RUNNER_CONFIG,
            'library_sha256': LIBRARY, 'identity_map_sha256': IDENTITY,
            'batch': 1, 'request_budget': 90, 'seed_pids': [PID],
            'source_inputs': [self.source_receipt] * 5,
            'seed_plan': self.plan_receipt, 'seed_video_evidence': self.evidence_receipt,
            'previous_growth': self.parent_receipt,
        }
        write_json(self.batch / 'intent.json', self.intent)
        self.collector = self.batch / 'collector'
        self.collector.mkdir()

        self.snapshot = self.collector / (PID + '.json')
        write_json(self.snapshot, {
            'schema_version': 1, 'origin': 'probe_ytmusic_batch',
            'account_authentication_verified': True,
            'library_sha256': LIBRARY, 'identity_map_sha256': IDENTITY,
            'selected_video_id': VIDEO,
            'local_seed': {'persistent_id': PID}, 'related_status': 'collected',
            'requests': [{'endpoint': 'watch_radio'}, {'endpoint': 'watch_seed'},
                         {'endpoint': 'song_related'}],
            'observations': [
                {'source': 'ytmusic', 'relation': 'radio', 'section': None,
                 'position': 1, 'observed_at': '2026-10-09T00:00:00+00:00',
                 'is_seed': False, 'track': {'video_id': 'lmnopqrstuv', 'title': 'Radio',
                                             'artists': [{'name': 'Artist'}]}},
                {'source': 'ytmusic', 'relation': 'related', 'section': None,
                 'position': 1, 'observed_at': '2026-10-09T00:00:00+00:00',
                 'is_seed': False, 'track': {'video_id': 'mnopqrstuvw', 'title': 'Related',
                                             'artists': [{'name': 'Artist'}]}},
                {'source': 'ytmusic', 'relation': 'radio', 'section': None,
                 'position': 1, 'observed_at': '2026-10-09T00:00:00+00:00',
                 'is_seed': True, 'track': {'video_id': VIDEO, 'title': 'Seed',
                                            'artists': [{'name': 'Artist'}]}},
            ],
        })
        write_json(self.collector / 'seeds.json', {
            'library_sha256': LIBRARY, 'identity_map_sha256': IDENTITY,
            'seed_plan_sha256': self.plan_receipt['sha256'],
            'seed_video_evidence_sha256': self.evidence_receipt['sha256'],
            'seeds': [{'persistent_id': PID}],
        })
        self.summary_path = self.collector / 'summary-123.json'
        write_json(self.summary_path, {
            'schema_version': 1, 'status': 'completed',
            'account_authentication_verified': True, 'library_sha256': LIBRARY,
            'http_requests': 3, 'request_budget': 90,
            'results': [{'persistent_id': PID, 'status': 'collected'}],
        })

    def test_dynamic_composite_must_bind_current_radio_intent_receipt(self):
        radio_path = self.root / 'current-radio-evidence.json'
        receipt = write_json(radio_path, {'producer': 'opengenius-seed-video-evidence-v1'})
        intent = {'current_radio_evidence': receipt}
        config = {'dynamic_current_radio_hints': True}
        evidence = {
            'producer': 'opengenius-combined-rebound-seed-evidence-v1',
            'current_radio_source': receipt,
        }
        recovery._verify_dynamic_current_radio_binding(intent, evidence, config)
        with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'does not bind'):
            recovery._verify_dynamic_current_radio_binding(
                intent, {**evidence, 'current_radio_source': {
                    'path': receipt['path'], 'sha256': '0' * 64}}, config)
        with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'changed'):
            recovery._verify_dynamic_current_radio_binding(
                {'current_radio_evidence': {**receipt, 'sha256': '0' * 64}},
                evidence, config)
        with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'unexpectedly'):
            recovery._verify_dynamic_current_radio_binding(
                intent, evidence, {'dynamic_current_radio_hints': False})

    def tearDown(self):
        self.temp.cleanup()

    def recover(self):
        return recovery.recover_orphan_batch(
            self.batch, expected_config_sha256=CONFIG, library_sha256=LIBRARY,
            identity_map_sha256=IDENTITY, expected_previous_growth=self.parent_receipt,
            expected_request_budget=90)

    def make_selector_preflight_failure(self):
        self.snapshot.unlink()
        (self.collector / 'seeds.json').unlink()
        write_json(self.summary_path, {
            'schema_version': 1, 'status': 'stopped',
            'account_authentication_verified': False, 'library_sha256': LIBRARY,
            'http_requests': 0, 'request_budget': 90, 'results': [],
            'error_type': 'ValueError',
            'error_location': {'file': 'merge_rebased_radio_growth.py',
                               'function': '_replay_previous_growth', 'line': 613},
        })

    def test_collector_finished_before_progress_is_recoverable_from_exact_receipts(self):
        recovered = self.recover()
        self.assertEqual(recovered['outcome'], 'ready_to_merge')
        self.assertEqual(recovered['attempted_pids'], [PID])
        self.assertEqual(len(recovered['genuine_snapshot_inputs']), 1)
        self.assertIsNone(recovered['growth_receipt'])

    def test_finished_merge_before_progress_is_bound_to_the_same_collector_and_parent(self):
        summary = self.recover()['collector_summary']
        manifest_path = self.collector / 'seeds.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest_receipt = {'path': str(manifest_path.resolve()),
                            'sha256': recovery.sha(manifest_path), 'seed_count': 1}
        snapshot_receipts = [{'path': str(self.snapshot.resolve()),
                              'sha256': recovery.sha(self.snapshot)}]
        run = {'collector_summary': summary, 'seed_manifest': manifest_receipt,
               'snapshot_inputs': snapshot_receipts}
        growth_dir = self.batch / 'growth'
        write_json(growth_dir / 'accepted-graphs.json', {
            'producer': 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1',
            'previous_growth_receipt': self.parent_receipt,
            'growth_runs': [run],
            'accepted_roots': [{'root_pid': PID, 'ordered_target_pids': ['target']}],
        })
        recovered = self.recover()
        self.assertEqual(recovered['outcome'], 'merged')
        self.assertTrue(recovered['requires_typed_growth_replay'])
        self.assertEqual(recovered['growth_receipt']['active_outgoing_roots'], 1)

    def test_orphan_growth_parent_accepts_directory_or_graph_file_with_same_hash(self):
        summary = self.recover()['collector_summary']
        manifest_path = self.collector / 'seeds.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest_receipt = {'path': str(manifest_path.resolve()),
                            'sha256': recovery.sha(manifest_path), 'seed_count': 1}
        snapshot_receipts = [{'path': str(self.snapshot.resolve()),
                              'sha256': recovery.sha(self.snapshot)}]
        parent_as_directory = {
            'path': str((self.root / 'previous').resolve()),
            'accepted_graphs_sha256': self.parent_receipt['accepted_graphs_sha256'],
        }
        write_json(self.batch / 'growth' / 'accepted-graphs.json', {
            'producer': 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1',
            'previous_growth_receipt': parent_as_directory,
            'growth_runs': [{'collector_summary': summary,
                             'seed_manifest': manifest_receipt,
                             'snapshot_inputs': snapshot_receipts}],
            'accepted_roots': [{'root_pid': PID, 'ordered_target_pids': ['target']}],
        })
        recovered = self.recover()
        self.assertEqual(recovered['outcome'], 'merged')

        graph = json.loads((self.batch / 'growth' / 'accepted-graphs.json').read_text())
        graph['previous_growth_receipt']['accepted_graphs_sha256'] = '0' * 64
        write_json(self.batch / 'growth' / 'accepted-graphs.json', graph)
        with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'different parent checkpoint'):
            self.recover()

    def test_partial_or_auth_failed_collector_is_never_automatically_retried(self):
        (self.collector / self.summary_path.name).unlink()
        with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'summary is absent'):
            self.recover()
        write_json(self.summary_path, {
            'schema_version': 1, 'status': 'stopped',
            'account_authentication_verified': False, 'library_sha256': LIBRARY,
            'http_requests': 1, 'request_budget': 90,
            'results': [], 'error_type': 'HTTPError', 'http_error_status': 429,
        })
        with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'did not finish'):
            self.recover()

    def test_known_zero_request_selector_preflight_is_checkpointed_without_attempts(self):
        self.make_selector_preflight_failure()
        recovered = self.recover()
        self.assertEqual(recovered['outcome'], 'preflight_failed')
        self.assertEqual(recovered['status'], 'preflight_failed')
        self.assertEqual(recovered['http_requests'], 0)
        self.assertEqual(recovered['attempted_pids'], [])
        self.assertIsNone(recovered['seed_manifest'])
        self.assertEqual(recovered['snapshot_inputs'], [])

    def test_known_preflight_signature_rejects_any_request_or_collector_artifact(self):
        self.make_selector_preflight_failure()
        request = self.collector / 'requests' / ('f' * 64 + '.json')
        write_json(request, {'request_started': True})
        with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'output files'):
            self.recover()

        request.unlink()
        request.parent.rmdir()
        write_json(self.snapshot, {'unexpected': True})
        with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'output files'):
            self.recover()

    def test_known_preflight_signature_rejects_counters_results_and_foreign_errors(self):
        self.make_selector_preflight_failure()
        base = json.loads(self.summary_path.read_text(encoding='utf-8'))
        for change in (
                {'http_requests': 1},
                {'results': [{'persistent_id': PID, 'status': 'collected'}]},
                {'partial_seed_persistent_id': PID},
                {'error_location': {'file': 'other.py',
                                    'function': '_replay_previous_growth', 'line': 613}},
        ):
            with self.subTest(change=change):
                write_json(self.summary_path, {**base, **change})
                with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'did not finish'):
                    self.recover()

    def test_changed_intent_inputs_and_unplanned_summary_pids_are_rejected(self):
        self.source.write_text('{"changed":true}', encoding='utf-8')
        with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'source input changed'):
            self.recover()
        self.source.write_text('{}', encoding='utf-8')
        write_json(self.summary_path, {
            'schema_version': 1, 'status': 'completed',
            'account_authentication_verified': True, 'library_sha256': LIBRARY,
            'http_requests': 3, 'request_budget': 90,
            'results': [{'persistent_id': 'FEDCBA9876543210', 'status': 'search_unresolved'}],
        })
        with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'unplanned PIDs'):
            self.recover()

    def test_missing_config_bound_source_receipt_is_rejected(self):
        self.intent['source_inputs'] = self.intent['source_inputs'][:-1]
        write_json(self.batch / 'intent.json', self.intent)
        with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'receipt set differs'):
            self.recover()

    def test_config_parent_must_match_growth_checkpoint(self):
        self.intent['previous_growth'] = None
        write_json(self.batch / 'intent.json', self.intent)
        with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'first checkpoint'):
            recovery.recover_orphan_batch(
                self.batch, expected_config_sha256=CONFIG, library_sha256=LIBRARY,
                identity_map_sha256=IDENTITY, expected_previous_growth=None,
                expected_request_budget=90)

    def test_later_batch_may_bind_its_evolving_checkpoint(self):
        later_parent = {'path': str((self.root / 'later' / 'accepted-graphs.json').resolve()),
                        'accepted_graphs_sha256': 'e' * 64}
        self.intent['batch'] = 2
        self.intent['previous_growth'] = later_parent
        write_json(self.batch / 'intent.json', self.intent)
        recovered = recovery.recover_orphan_batch(
            self.batch, expected_config_sha256=CONFIG, library_sha256=LIBRARY,
            identity_map_sha256=IDENTITY, expected_previous_growth=later_parent,
            expected_request_budget=90)
        self.assertEqual(recovered['outcome'], 'ready_to_merge')

    def test_runner_hash_binds_budget_and_explicit_deadline(self):
        later_deadline = {**RUNNER_CONFIG, 'deadline': '2026-10-09T12:00:00+09:00'}
        self.assertNotEqual(recovery.runner_config_sha256(RUNNER_CONFIG),
                            recovery.runner_config_sha256(later_deadline))
        with self.assertRaisesRegex(recovery.RecoveryUnsafe, 'timezone'):
            recovery.runner_config_sha256({**RUNNER_CONFIG,
                                           'deadline': '2026-10-09T12:00:00'})


if __name__ == '__main__':
    unittest.main()
