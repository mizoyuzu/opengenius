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


if __name__ == '__main__':
    unittest.main()
