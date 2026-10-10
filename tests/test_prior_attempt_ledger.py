"""Offline tests for cross-stage request and attempted-seed receipts."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import expand_genuine_roots as runner


LIBRARY = 'a' * 64
TRACKS = 'b' * 64
OLD_MAP = 'c' * 64
PID_PRIOR = '0123456789ABCDEF'
PID_INTERRUPTED = 'FEDCBA9876543210'
PID_TAIL = 'AAAAAAAAAAAAAAAA'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True).encode('utf-8')
    path.write_bytes(raw)
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest()}


class PriorAttemptLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.checkpoint = self.root / 'prior' / 'batch-0001' / 'growth'
        self.graph = self.checkpoint / 'accepted-graphs.json'
        self.graph_receipt = write_json(self.graph, {
            'accepted_roots': [{'root_pid': PID_PRIOR, 'ordered_target_pids': ['target']}],
        })
        self.prior_progress_path = self.root / 'prior' / 'progress.json'
        self.prior_progress = {
            'schema_version': 1,
            'config': {'track_snapshot': {'sha256': TRACKS},
                       'identity_map': {'sha256': OLD_MAP},
                       'previous_growth': {'path': str(self.checkpoint.resolve()),
                                           'sha256': self.graph_receipt['sha256']}},
            'batches': [{'growth_output': str(self.checkpoint.resolve()),
                         'growth_receipt': self.graph_receipt}],
            'attempted_pids': [PID_PRIOR], 'http_requests': 4,
            'successful_outgoing_roots': 1,
        }
        self.progress_receipt = write_json(self.prior_progress_path, self.prior_progress)

        self.batch = self.root / 'prior' / 'batch-0002'
        self.plan_path = self.batch / 'seed-plan.json'
        self.plan_receipt = write_json(self.plan_path, {'seeds': [
            {'persistent_id': PID_INTERRUPTED}, {'persistent_id': PID_TAIL},
        ]})
        self.summary_path = self.batch / 'collector' / 'summary-123.json'
        self.summary_receipt = write_json(self.summary_path, {
            'schema_version': 1, 'status': 'running',
            'account_authentication_verified': True, 'library_sha256': LIBRARY,
            'http_requests': 3, 'request_budget': 90, 'results': [],
        })
        self.intent_path = self.batch / 'intent.json'
        self.intent_receipt = write_json(self.intent_path, {
            'library_sha256': LIBRARY, 'identity_map_sha256': OLD_MAP,
            'seed_pids': [PID_INTERRUPTED, PID_TAIL],
            'previous_growth': {'path': self.graph_receipt['path'],
                                'accepted_graphs_sha256': self.graph_receipt['sha256']},
        })
        self.incident_path = self.root / 'incident.json'
        self.incident_receipt = write_json(self.incident_path, {
            'schema_version': 1, 'type': 'operator_interrupted_request',
            'status': 'uncheckpointed_no_retry',
            'seed_persistent_id_to_exclude_from_future_attempts': PID_INTERRUPTED,
            'request_starts_counted_by_collector': 3,
            'summary_receipt': self.summary_receipt,
            'intent_receipt': self.intent_receipt,
            'seed_plan_receipt': self.plan_receipt,
            'last_typed_checkpoint': str(self.checkpoint.resolve()),
            'last_typed_checkpoint_graph_sha256': self.graph_receipt['sha256'],
            'verified_outgoing_root_count': 1,
        })
        self.ledger_path = self.root / 'attempt-ledger.json'
        self.ledger = {
            'schema_version': 1,
            'producer': 'opengenius-genuine-root-attempt-ledger-v1',
            'prior_progress': self.progress_receipt,
            'attempt_only_progress': [],
            'interruption_incidents': [self.incident_receipt],
            'http_requests_total': 7,
            'excluded_persistent_ids': sorted([PID_PRIOR, PID_INTERRUPTED]),
            'base_outgoing_roots': 1,
        }
        write_json(self.ledger_path, self.ledger)

    def tearDown(self):
        self.temp.cleanup()

    def validate(self):
        with patch.object(runner, '_verify_progress_receipts') as verify:
            result = runner._validate_prior_attempt_ledger(
                self.ledger_path, LIBRARY, TRACKS)
        self.assertEqual(verify.call_args_list[0].args,
                         (self.prior_progress, LIBRARY, OLD_MAP, set(), 0))
        return result

    def test_reproduces_budget_exclusions_and_checkpoint_from_receipts(self):
        result = self.validate()
        self.assertEqual(result['http_requests'], 7)
        self.assertEqual(result['attempted_pids'], {PID_PRIOR, PID_INTERRUPTED})
        self.assertEqual(result['outgoing_roots'], 1)

    def test_attempt_only_history_excludes_pids_without_charging_old_budget(self):
        historical_receipt = write_json(self.root / 'older' / 'progress.json', {
            'schema_version': 1,
            'config': {'track_snapshot': {'sha256': TRACKS},
                       'identity_map': {'sha256': OLD_MAP}},
            'batches': [{'growth_output': str(self.checkpoint.resolve()),
                         'growth_receipt': self.graph_receipt}],
            'attempted_pids': [PID_TAIL], 'http_requests': 450,
            'successful_outgoing_roots': 1,
        })
        self.ledger['attempt_only_progress'] = [historical_receipt]
        self.ledger['excluded_persistent_ids'] = sorted(
            [PID_PRIOR, PID_INTERRUPTED, PID_TAIL])
        # This earlier campaign has its own request cap and doesn't debit the
        # current campaign's 7 requests (4 completed plus 3 interrupted).
        write_json(self.ledger_path, self.ledger)
        result = self.validate()
        self.assertEqual(result['http_requests'], 7)
        self.assertEqual(result['attempted_pids'], {PID_PRIOR, PID_INTERRUPTED, PID_TAIL})

    def test_false_global_request_total_is_rejected(self):
        self.ledger['http_requests_total'] = 6
        write_json(self.ledger_path, self.ledger)
        with self.assertRaisesRegex(runner.RecoveryUnsafe, 'request or root total'):
            self.validate()

    def test_suppressed_interrupted_seed_is_rejected(self):
        self.ledger['excluded_persistent_ids'] = [PID_PRIOR]
        write_json(self.ledger_path, self.ledger)
        with self.assertRaisesRegex(runner.RecoveryUnsafe, 'PID exclusions'):
            self.validate()

    def _make_promotion_ledger(self):
        sources = {}
        for key in ('track_snapshot', 'identity_map', 'baseline_state',
                    'baseline_graph_export', 'rebase_report'):
            path = self.root / 'promotion-inputs' / (key + '.json')
            receipt = write_json(path, {'input': key})
            sources[key] = receipt
            self.prior_progress['config'][key] = receipt
        batch_dir = self.root / 'prior' / 'batch-0001'
        collector_dir = batch_dir / 'collector'
        summary_receipt = write_json(collector_dir / 'summary.json', {
            'schema_version': 1, 'status': 'completed',
            'account_authentication_verified': True, 'library_sha256': LIBRARY,
            'http_requests': 4, 'request_budget': 10,
            'results': [{'persistent_id': PID_PRIOR, 'status': 'collected'}],
        })
        evidence_receipt = write_json(batch_dir / 'seed-video-evidence.json', {
            'schema_version': 1, 'rows': [],
        })
        plan_receipt = write_json(batch_dir / 'seed-plan.json', {
            'seeds': [{'persistent_id': PID_PRIOR}],
        })
        manifest_receipt = write_json(collector_dir / 'seeds.json', {
            'seeds': [{'persistent_id': PID_PRIOR}],
        })
        original_snapshot = write_json(collector_dir / (PID_PRIOR + '.json'), {
            'local_seed': {'persistent_id': PID_PRIOR},
        })
        intent_path = batch_dir / 'intent.json'
        intent_receipt = write_json(intent_path, {'fixture_intent': True})
        snapshot_inputs = [original_snapshot]
        self.prior_progress['batches'][0].update({
            'batch': 1, 'status': 'completed', 'outcome': 'merged',
            'seed_pids': [PID_PRIOR], 'seed_plan': plan_receipt,
            'seed_manifest': manifest_receipt,
            'seed_video_evidence': evidence_receipt,
            'collector_summary': {**summary_receipt,
                                  'http_requests': 4, 'request_budget': 10},
            'collector_summary_sha256': summary_receipt['sha256'],
            'request_budget': 10, 'http_requests': 4,
            'snapshot_inputs': snapshot_inputs,
            'attempted_pids': [PID_PRIOR],
        })
        self.progress_receipt = write_json(self.prior_progress_path, self.prior_progress)
        promoted_dir = self.root / 'radio-promotion' / 'growth'
        source_output = self.root / 'radio-promotion' / 'radio-inputs'
        proof_path = source_output / (PID_PRIOR + '.proof.json')
        proof_receipt = write_json(proof_path, {
            'collector_snapshot': original_snapshot,
        })
        sanitized = source_output / (PID_PRIOR + '.snapshot.json')
        sanitized_receipt = write_json(sanitized, {
            'radio_only_source_proof': proof_receipt,
        })
        summary = json.loads(Path(summary_receipt['path']).read_text())
        manifest = json.loads(Path(manifest_receipt['path']).read_text())
        transition = {
            'schema_version': 1, 'producer': 'opengenius-radio-source-policy-transition-v1',
            'from_policy': 'legacy-related-required-v1',
            'to_policy': 'authenticated-watch-radio-only-v1',
            'legacy_parent_growth': {'path': str(self.graph.resolve()),
                                     'sha256': self.graph_receipt['sha256']},
            'legacy_run_count': 0, 'promotion_http_requests': 0,
            'promotion_attempt_delta': 0,
        }
        promoted_run = {
            'source_policy': 'authenticated-watch-radio-only-v1',
            'source_policy_transition': transition,
            'snapshot_inputs': [{**sanitized_receipt,
                                 'radio_only_source_proof': proof_receipt}],
            'collector_summary': {
                'path': summary_receipt['path'], 'sha256': summary_receipt['sha256'],
                'http_requests': summary['http_requests'],
                'request_budget': summary['request_budget'],
            },
            'seed_manifest': {
                'path': manifest_receipt['path'], 'sha256': manifest_receipt['sha256'],
                'seed_count': len(manifest['seeds']),
                'seed_video_evidence': evidence_receipt,
            },
        }
        promoted_graph = write_json(promoted_dir / 'accepted-graphs.json', {
            'growth_runs': [promoted_run],
            'accepted_roots': [
                {'root_pid': PID_PRIOR, 'ordered_target_pids': ['target']},
                {'root_pid': PID_TAIL, 'ordered_target_pids': ['target']},
            ],
        })
        source_report = {
            'batch': 1,
            'collector_summary': {'path': summary_receipt['path'],
                                  'sha256': summary_receipt['sha256']},
            'seed_manifest': {'path': manifest_receipt['path'],
                              'sha256': manifest_receipt['sha256']},
            'batch_intent': intent_receipt,
            'seed_video_evidence': evidence_receipt,
            'collector_snapshots': snapshot_inputs,
            'radio_source_output_dir': str(source_output.resolve()),
            'proofs': [proof_receipt],
            'snapshot_inputs': [{**sanitized_receipt,
                                 'radio_only_source_proof': proof_receipt}],
            'ineligible': [],
        }
        promotion_path = self.root / 'radio-promotion' / 'promotion.json'
        promotion = {
            'schema_version': 1,
            'producer': 'opengenius-authenticated-radio-growth-promotion-v1',
            'source_policy': 'authenticated-watch-radio-only-v1',
            'source_progress': self.progress_receipt,
            **sources,
            'parent_growth': self.graph_receipt,
            'promoted_growth': {
                'path': str(promoted_dir.resolve()),
                'accepted_graphs_sha256': promoted_graph['sha256'],
            },
            'http_requests_before': 4,
            'http_requests_after': 4,
            'attempted_pids_before': [PID_PRIOR],
            'attempted_pids_after': [PID_PRIOR],
            'http_request_delta': 0,
            'attempt_delta': 0,
            'promoted_outgoing_roots': 2,
            'promotion_batches': [source_report],
        }
        promotion_receipt = write_json(promotion_path, promotion)
        self.ledger = {
            **self.ledger,
            'schema_version': 2,
            'producer': 'opengenius-genuine-root-attempt-ledger-v2',
            'prior_progress': self.progress_receipt,
            'interruption_incidents': [],
            'http_requests_total': 4,
            'excluded_persistent_ids': [PID_PRIOR],
            'promotion_receipt': promotion_receipt,
            'base_outgoing_roots': 2,
        }
        write_json(self.ledger_path, self.ledger)
        self.promotion = promotion
        self.promotion_path = promotion_path
        self.promoted_graph = promoted_dir / 'accepted-graphs.json'
        self.track_sha = sources['track_snapshot']['sha256']
        self.radio_source_result = {
            'source_policy': 'authenticated-watch-radio-only-v1',
            'proofs': [proof_receipt], 'snapshot_inputs': [sanitized_receipt],
            'ineligible': [], 'output_dir': str(source_output.resolve()),
        }

    def test_v2_promotion_replays_graph_without_charging_requests_or_attempts(self):
        self._make_promotion_ledger()
        with patch.object(runner, '_verify_progress_receipts'), \
                patch('radio_source_integration.prepare_radio_only_snapshots',
                      return_value=self.radio_source_result), \
                patch.object(runner, 'replay_growth_checkpoint', return_value={
                    'accepted_graphs_sha256': hashlib.sha256(
                        self.promoted_graph.read_bytes()).hexdigest(),
                    'outgoing_root_count': 2,
                }):
            result = runner._validate_prior_attempt_ledger(
                self.ledger_path, LIBRARY, self.track_sha)
        self.assertEqual(result['http_requests'], 4)
        self.assertEqual(result['attempted_pids'], {PID_PRIOR})
        self.assertEqual(result['outgoing_roots'], 2)

    def test_v2_promotion_rejects_request_or_attempt_delta(self):
        self._make_promotion_ledger()
        self.promotion['http_requests_after'] = 5
        receipt = write_json(self.promotion_path, self.promotion)
        self.ledger['promotion_receipt'] = receipt
        write_json(self.ledger_path, self.ledger)
        with patch.object(runner, '_verify_progress_receipts'):
            with self.assertRaisesRegex(runner.RecoveryUnsafe, 'preserve frozen request/PID'):
                runner._validate_prior_attempt_ledger(
                    self.ledger_path, LIBRARY, self.track_sha)

    def test_v2_promotion_rejects_changed_snapshot_proof_pointer(self):
        self._make_promotion_ledger()
        pointer = self.promotion['promotion_batches'][0]['snapshot_inputs'][0][
            'radio_only_source_proof']
        pointer['sha256'] = 'f' * 64
        receipt = write_json(self.promotion_path, self.promotion)
        self.ledger['promotion_receipt'] = receipt
        write_json(self.ledger_path, self.ledger)
        with patch.object(runner, '_verify_progress_receipts'), \
                patch('radio_source_integration.prepare_radio_only_snapshots',
                      return_value=self.radio_source_result):
            with self.assertRaisesRegex(
                    runner.RecoveryUnsafe,
                    'Radio promotion proofs and per-batch receipts do not fully replay'):
                runner._validate_prior_attempt_ledger(
                    self.ledger_path, LIBRARY, self.track_sha)

    def test_repeated_interruption_cannot_inflate_budget(self):
        duplicate = self.root / 'incident-copy.json'
        copy_receipt = write_json(duplicate, json.loads(self.incident_path.read_text()))
        self.ledger['interruption_incidents'].append(copy_receipt)
        self.ledger['http_requests_total'] = 10
        write_json(self.ledger_path, self.ledger)
        with self.assertRaisesRegex(runner.RecoveryUnsafe, 'duplicates an attempted PID'):
            self.validate()

    def test_changed_interruption_summary_is_rejected_before_new_requests(self):
        write_json(self.summary_path, {'status': 'running', 'http_requests': 2})
        with self.assertRaisesRegex(runner.RecoveryUnsafe, 'hash differs'):
            self.validate()

    def test_prior_stage_must_use_same_track_snapshot(self):
        with self.assertRaisesRegex(runner.RecoveryUnsafe, 'different Library snapshot'):
            with patch.object(runner, '_verify_progress_receipts'):
                runner._validate_prior_attempt_ledger(
                    self.ledger_path, LIBRARY, 'd' * 64)

    def test_runner_initializes_global_counter_and_exclusions_before_collection(self):
        current = self.root / 'current'
        tracks = current / 'tracks.json'
        identity = current / 'identity.json'
        state = current / 'state.json'
        graphs = current / 'graphs.json'
        rebase = current / 'rebase.json'
        auth = current / 'auth.json'
        current.mkdir()
        for path in (tracks, identity, state, rebase, auth):
            path.write_text('{}', encoding='utf-8')
        write_json(graphs, {'accepted_roots': [
            {'root_pid': PID_PRIOR, 'ordered_target_pids': ['target']},
        ]})
        track_sha = hashlib.sha256(tracks.read_bytes()).hexdigest()
        self.prior_progress['config']['track_snapshot']['sha256'] = track_sha
        self.progress_receipt = write_json(self.prior_progress_path, self.prior_progress)
        self.ledger['prior_progress'] = self.progress_receipt
        write_json(self.ledger_path, self.ledger)

        pause = current / 'pause'
        pause.write_text('pause before HTTP', encoding='utf-8')
        args = type('Args', (), {
            'track_snapshot': tracks, 'identity_map': identity, 'baseline_state': state,
            'baseline_graph_export': graphs, 'rebase_report': rebase,
            'previous_growth': None, 'seed_video_evidence': None,
            'prior_attempt_ledger': self.ledger_path, 'output': current / 'run',
            'auth': auth, 'target_outgoing_roots': 5, 'global_request_budget': 6500,
            'max_attempts': 100, 'batch_size': 20, 'batch_request_budget': 90,
            'candidate_limit': 100, 'interval': 3, 'deadline': None,
            'plan_only': False, 'pause_file': pause, 'stop_after_batches': None,
        })()
        with patch.object(runner, 'load_library', return_value=([], LIBRARY, {})), \
                patch.object(runner, 'IdentityMap', return_value=object()), \
                patch.object(runner, '_verify_progress_receipts'), \
                patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 1}), \
                patch.object(runner, '_invoke', side_effect=AssertionError('must pause before HTTP')):
            progress = runner.run(args)
        self.assertEqual(progress['http_requests'], 7)
        self.assertEqual(set(progress['attempted_pids']), {PID_PRIOR, PID_INTERRUPTED})
        self.assertEqual(progress['successful_outgoing_roots'], 1)
        self.assertEqual(progress['config']['global_request_budget'], 6500)


class PreauthTransportLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.library_hash = '1' * 64
        sources = self.root / 'sources'
        self.source_receipts = [
            write_json(sources / 'tracks.json', {'kind': 'tracks'}),
            write_json(sources / 'map.json', {'kind': 'map'}),
            write_json(sources / 'state.json', {'kind': 'state'}),
            write_json(sources / 'baseline-graphs.json', {'kind': 'baseline'}),
            write_json(sources / 'rebase.json', {'kind': 'rebase'}),
            write_json(sources / 'external-evidence.json', {'kind': 'external'}),
        ]
        self.track_hash = self.source_receipts[0]['sha256']
        self.map_hash = self.source_receipts[1]['sha256']
        self.pid = 'ABCDEF0123456789'
        self.graph_path = self.root / 'parent' / 'accepted-graphs.json'
        self.graph_receipt = write_json(self.graph_path, {'accepted_roots': [
            {'root_pid': PID_PRIOR, 'ordered_target_pids': ['target']},
        ]})
        self.progress_path = self.root / 'run' / 'progress.json'
        config = {
            'track_snapshot': self.source_receipts[0],
            'identity_map': self.source_receipts[1],
            'baseline_state': self.source_receipts[2],
            'baseline_graph_export': self.source_receipts[3],
            'rebase_report': self.source_receipts[4],
            'external_seed_video_evidence': self.source_receipts[5],
            'previous_growth': {'path': str(self.graph_path),
                                'sha256': self.graph_receipt['sha256']},
            'source_policy': 'authenticated-watch-radio-only-v1',
            'batch_size': 20, 'batch_request_budget': 90,
            'global_request_budget': 6500, 'deadline': None,
        }
        self.progress_receipt = write_json(self.progress_path, {
            'config': config, 'batches': [], 'attempted_pids': [],
            'http_requests': 0, 'successful_outgoing_roots': 1,
        })
        batch = self.root / 'run' / 'batch-0001'
        self.plan_receipt = write_json(batch / 'seed-plan.json', {
            'source_library_sha256': self.library_hash,
            'seeds': [{'persistent_id': self.pid}],
        })
        video_receipt = write_json(batch / 'seed-video-evidence.json', {'rows': []})
        radio_receipt = write_json(batch / 'current-radio-evidence.json', {'rows': []})
        self.manifest_receipt = write_json(batch / 'collector' / 'seeds.json', {
            'library_sha256': self.library_hash, 'identity_map_sha256': self.map_hash,
            'seed_plan_sha256': self.plan_receipt['sha256'],
            'seed_video_evidence_sha256': video_receipt['sha256'],
            'seeds': [{'persistent_id': self.pid, 'seed_video_id': 'video'}],
        })
        (batch / 'collector' / 'requests').mkdir()
        self.summary_receipt = write_json(batch / 'collector' / 'summary.json', {
            'schema_version': 1, 'status': 'stopped',
            'account_authentication_verified': False, 'identity_status': 'unverified',
            'results': [], 'http_requests': 1, 'request_budget': 90,
            'partial_seed_persistent_id': None, 'library_sha256': self.library_hash,
            'error_type': 'ConnectionError',
            'error_location': {'file': 'adapters.py', 'function': 'send', 'line': 700},
        })
        # The runner config itself is exactly the progress config; batch-only
        # seed receipts are separate intent fields.
        intent = {
            'batch': 1, 'source_policy': config['source_policy'],
            'runner_config': config,
            'runner_config_sha256': runner.runner_config_sha256(config),
            'request_budget': 90,
            'seed_pids': [self.pid], 'library_sha256': self.library_hash,
            'identity_map_sha256': self.map_hash,
            'source_inputs': self.source_receipts,
            'seed_plan': self.plan_receipt, 'seed_video_evidence': video_receipt,
            'current_radio_evidence': radio_receipt,
            'previous_growth': {'path': str(self.graph_path),
                                'accepted_graphs_sha256': self.graph_receipt['sha256']},
        }
        self.intent_receipt = write_json(batch / 'intent.json', intent)
        self.incident_path = self.root / 'preauth-incident.json'
        self.incident_receipt = write_json(self.incident_path, {
            'schema_version': 1, 'type': 'preauth_transport_failure',
            'status': 'account_preflight_request_failed',
            'request_starts_counted_by_collector': 1,
            'runner_progress_receipt': self.progress_receipt,
            'intent_receipt': self.intent_receipt,
            'seed_plan_receipt': self.plan_receipt,
            'seed_manifest_receipt': self.manifest_receipt,
            'summary_receipt': self.summary_receipt,
            'parent_growth_receipt': {'path': config['previous_growth']['path'],
                                      'sha256': config['previous_growth']['sha256']},
        })
        self.ledger_path = self.root / 'ledger-v3.json'
        self.ledger = {
            'schema_version': 3,
            'producer': 'opengenius-genuine-root-attempt-ledger-v3',
            'prior_progress': self.progress_receipt,
            'attempt_only_progress': [], 'interruption_incidents': [],
            'request_only_failure_incidents': [self.incident_receipt],
            'http_requests_total': 1, 'excluded_persistent_ids': [],
            'base_outgoing_roots': 1,
        }
        write_json(self.ledger_path, self.ledger)

    def tearDown(self):
        self.temp.cleanup()

    def validate(self):
        return runner._validate_prior_attempt_ledger(
            self.ledger_path, self.library_hash, self.track_hash,
            current_identity_map_sha256=self.map_hash)

    def reseal_intent(self, mutate):
        intent_path = Path(self.intent_receipt['path'])
        intent = json.loads(intent_path.read_text())
        mutate(intent)
        self.intent_receipt = write_json(intent_path, intent)
        incident = json.loads(self.incident_path.read_text())
        incident['intent_receipt'] = self.intent_receipt
        self.incident_receipt = write_json(self.incident_path, incident)
        self.ledger['request_only_failure_incidents'] = [self.incident_receipt]
        write_json(self.ledger_path, self.ledger)

    def test_preauth_transport_failure_charges_request_without_consuming_pid(self):
        result = self.validate()
        self.assertEqual(result['http_requests'], 1)
        self.assertEqual(result['attempted_pids'], set())
        self.assertEqual(result['outgoing_roots'], 1)

    def test_preauth_receipt_rejects_auth_success_result_or_snapshot(self):
        summary = json.loads(Path(self.summary_receipt['path']).read_text())
        summary['account_authentication_verified'] = True
        self.summary_receipt = write_json(Path(self.summary_receipt['path']), summary)
        # Rebind the mutated summary through the incident and ledger so this
        # checks semantic validation rather than only a stale hash failure.
        incident = json.loads(self.incident_path.read_text())
        incident['summary_receipt'] = self.summary_receipt
        self.incident_receipt = write_json(self.incident_path, incident)
        self.ledger['request_only_failure_incidents'] = [self.incident_receipt]
        write_json(self.ledger_path, self.ledger)
        with self.assertRaisesRegex(runner.RecoveryUnsafe,
                                    'one request-only failure'):
            self.validate()

    def test_preauth_incident_rejects_omitted_resealed_config_source(self):
        self.reseal_intent(lambda intent: intent['source_inputs'].pop())
        with self.assertRaisesRegex(runner.RecoveryUnsafe,
                                    'intent, plan, or manifest receipts disagree'):
            self.validate()

    def test_preauth_incident_rejects_wrong_resealed_large_request_budget(self):
        def wrong_budget(intent):
            intent['request_budget'] = 89
            summary_path = Path(self.summary_receipt['path'])
            summary = json.loads(summary_path.read_text())
            summary['request_budget'] = 89
            self.summary_receipt = write_json(summary_path, summary)
        self.reseal_intent(wrong_budget)
        incident = json.loads(self.incident_path.read_text())
        incident['summary_receipt'] = self.summary_receipt
        self.incident_receipt = write_json(self.incident_path, incident)
        self.ledger['request_only_failure_incidents'] = [self.incident_receipt]
        write_json(self.ledger_path, self.ledger)
        with self.assertRaisesRegex(runner.RecoveryUnsafe,
                                    'intent, plan, or manifest receipts disagree'):
            self.validate()


if __name__ == '__main__':
    unittest.main()
