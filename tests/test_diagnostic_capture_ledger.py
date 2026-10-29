"""Tamper-focused tests for zero-credit standalone capture accounting."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import expand_genuine_roots as runner


def write_receipt(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True).encode('utf-8')
    path.write_bytes(raw)
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest()}


class DiagnosticCaptureLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.library = 'a' * 64
        self.context = {'language': 'ja', 'location': 'JP', 'search_filter': None,
                        'search_result_type': 'song',
                        'request_client_context': {'hl': 'ja', 'gl': 'JP'}}
        self.pids = [f'{index:016X}' for index in range(1, 21)]
        prior_pid = 'F000000000000001'
        track = write_receipt(self.root / 'track.json', {'snapshot': 1})
        identity = write_receipt(self.root / 'identity.json', {'map': 1})
        auth = write_receipt(self.root / 'auth.json', {'opaque': True})
        patch_receipt = write_receipt(self.root / 'collector.patch', {'patch': 1})
        source = write_receipt(self.root / 'collector.py', {'source': 1})
        graph = write_receipt(self.root / 'growth' / 'accepted-graphs.json', {
            'accepted_roots': [{'root_pid': prior_pid, 'ordered_target_pids': ['x']}],
        })
        self.prior = write_receipt(self.root / 'progress.json', {
            'config': {'track_snapshot': track, 'identity_map': identity,
                       'previous_growth': graph},
            'batches': [{'attempted_pids': [prior_pid], 'growth_receipt': graph}],
            'attempted_pids': [prior_pid], 'http_requests': 3,
            'successful_outgoing_roots': 1,
        })
        self.track_hash, self.map_hash = track['sha256'], identity['sha256']
        collector = self.root / 'trial' / 'collector'
        requests = collector / 'requests'
        rows = [{'persistent_id': pid, 'title': f'Title {i}', 'artist': f'Artist {i}',
                 'album': f'Album {i}', 'duration_ms': 100000}
                for i, pid in enumerate(self.pids)]
        seed_plan = write_receipt(self.root / 'trial' / 'seed-plan.json', {'seeds': rows})
        plan = {
            'schema_version': 1, 'producer': 'opengenius-ja-locale-standalone-trial-plan-v2',
            'trial_id': 'fixture', 'status': 'prepared_after_reviewed_bootstrap_fix',
            'diagnostic_only': True, 'root_or_edge_credit_allowed': False,
            'output_dir': str(collector.resolve()), 'track_snapshot': track,
            'identity_map': identity, 'auth_receipt': auth,
            'parent_campaign_progress': self.prior, 'parent_ledger': None,
            'cumulative_limits': {'requests_already_charged': 3,
                                  'attempts_already_charged': 1,
                                  'outgoing_roots_before_trial': 1,
                                  'max_trial_request_charge': 450,
                                  'max_trial_attempt_charge': 20},
            'seed_plan': {**seed_plan, 'seeds': 20},
            'collector_code': {'patch_path': patch_receipt['path'],
                               'patch_sha256': patch_receipt['sha256'],
                               'source_files': [source]},
            'collection': {'mode': 'radio-only-ja-locale-v1',
                     'source_policy': 'authenticated-watch-radio-only-ja-locale-v1',
                     'client_context': {'hl': 'ja', 'gl': 'JP'},
                     'search_filter': None, 'seed_query_fallback': False,
                     'interval_seconds': 3, 'seed_count_cap': 20,
                     'request_budget_cap': 450,
                     'auth_receipt': 'reuse the existing explicitly authorized Firefox auth input; do not record secret bytes or path in this plan'},
        }
        plan_receipt = write_receipt(self.root / 'trial' / 'plan.json', plan)
        config_receipt = write_receipt(self.root / 'trial' / 'trial-config.json', {
            'schema_version': 1,
            'producer': 'opengenius-ja-locale-standalone-trial-config-v2',
            'trial_id': 'fixture', 'plan': plan_receipt,
            'output_dir': str(collector.resolve()),
            'auth_receipt': auth, 'mode': plan['collection'],
            'request_budget': 450, 'interval_seconds': 3, 'candidate_limit': 100,
            'seed_count': 20,
            'diagnostic_only': True, 'root_or_edge_credit_allowed': False,
        })

        cache_receipts, search_docs, radio_docs = [], {}, {}
        for i, row in enumerate(rows):
            query = f"{row['title']} {row['artist']}"
            doc = {'endpoint': 'search',
                   'arguments': {'query': query, 'filter': None, 'limit': 5},
                   'observed_at': f'2026-10-01T00:00:{i:02d}Z', 'response': {'items': []},
                   'client_context': self.context,
                   'request_client_contexts': [{'hl': 'ja', 'gl': 'JP'}]}
            receipt = write_receipt(requests / f'search-{i}.json', doc)
            cache_receipts.append({'name': 'request_cache', **receipt})
            search_docs[row['persistent_id']] = doc
        snapshots = []
        for i in range(2):
            video = f'video-{i}'
            doc = {'endpoint': 'get_watch_playlist',
                   'arguments': {'videoId': video, 'radio': True, 'limit': 100},
                   'observed_at': f'2026-10-01T00:01:0{i}Z', 'response': {'items': []},
                   'client_context': self.context,
                   'request_client_contexts': [{'hl': 'ja', 'gl': 'JP'}]}
            receipt = write_receipt(requests / f'radio-{i}.json', doc)
            cache_receipts.append({'name': 'request_cache', **receipt})
            radio_docs[video] = doc
            evidence = []
            for item in (search_docs[self.pids[i]], doc):
                evidence.append({key: item[key] for key in
                                 ('endpoint', 'arguments', 'observed_at', 'client_context',
                                  'request_client_contexts')} | {'cache_hit': False})
            snap = {'local_seed': rows[i], 'selected_video_id': video,
                    'seed_identity_status': 'unique_metadata_candidate_unverified',
                    'account_authentication_verified': True, 'observations': [],
                    'collector_collection_mode': 'radio-only-ja-locale-v1',
                    'source_policy': 'authenticated-watch-radio-only-ja-locale-v1',
                    'client_context': self.context, 'identity_map_sha256': self.map_hash,
                    'request_evidence': evidence}
            snapshots.append({'name': 'raw_snapshot', **write_receipt(
                collector / f'{self.pids[i]}.json', snap)})
        self.report_receipts = snapshots + cache_receipts
        self.rows = [{'persistent_id': pid,
                      'status': 'collected' if i < 2 else 'search_unresolved'}
                     for i, pid in enumerate(self.pids)]
        self.manifest = write_receipt(collector / 'seeds.json', {
            'library_sha256': self.library,
            'library_input': {'snapshot_sha256': self.track_hash},
            'identity_map_sha256': self.map_hash, 'seeds': rows,
            'seed_plan_sha256': seed_plan['sha256'],
            'source_policy': 'authenticated-watch-radio-only-ja-locale-v1',
            'collector_collection_mode': 'radio-only-ja-locale-v1',
            'client_context': self.context,
        })
        summary_doc = {
            'status': 'completed', 'account_authentication_verified': True,
            'identity_status': 'unverified',
            'source_policy': 'authenticated-watch-radio-only-ja-locale-v1',
            'collector_collection_mode': 'radio-only-ja-locale-v1',
            'client_context': self.context, 'library_sha256': self.library,
            'cache_hits': 0, 'http_requests': 28,
            'http_request_categories': {'account_preflight': 1, 'radio_continuation': 4,
                                        'radio_page': 2, 'search_page': 20,
                                        'visitor_bootstrap': 1},
            'http_request_categories_match_total': True, 'results': self.rows,
        }
        self.summary = write_receipt(collector / 'summary.json', summary_doc)
        summary_named = {'name': 'collector_summary', **self.summary}
        manifest_named = {'name': 'seed_manifest', **self.manifest}
        delta = {'http_requests': 28, 'attempted_pids': 20,
                 'attempted_pid_count': 20, 'accepted_roots': 0, 'accepted_edges': 0}
        after = {'http_requests': 31, 'attempted_pids': 21,
                 'outgoing_roots': 1, 'accepted_edges': 1}
        result = {'status': 'completed', 'account_authentication_verified': True,
                  'http_requests': 28,
                  'request_categories': summary_doc['http_request_categories'],
                  'result_count': 20, 'result_status_counts':
                  {'search_unresolved': 18, 'collected': 2}, 'raw_snapshot_count': 2,
                  'request_cache_count': 22,
                  'collection_mode': 'radio-only-ja-locale-v1',
                  'source_policy': 'authenticated-watch-radio-only-ja-locale-v1',
                  'client_context': self.context}
        previous_journal = write_receipt(self.root / 'prior-journal.json', {
            'head_event_sha256': 'f' * 64})
        events, previous = [], 'f' * 64
        def event(body):
            nonlocal previous
            item = {'sequence': len(events), **body, 'previous_event_sha256': previous}
            item['event_sha256'] = hashlib.sha256(
                json.dumps(item, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            previous = item['event_sha256']; events.append(item)
        event({'event': 'prepared'})
        event({'event': 'diagnostic_trial_v02_launch_authorized', 'plan': plan_receipt,
               'config': config_receipt, 'collector_patch_sha256': patch_receipt['sha256'],
               'collector_sources': [source],
               'collector_invocation': {'track_snapshot': track,
                                        'identity_map': identity,
                                        'seed_plan': plan['seed_plan'], 'seeds': 20,
                                        'request_budget': 450, 'interval_seconds': 3,
                                        'candidate_limit': 100, 'language': 'ja',
                                        'source_policy':
                                        'authenticated-watch-radio-only-ja-locale-v1'},
               'accounting_before': {'http_requests': 3, 'attempted_pids': 1, 'roots': 1}})
        event({'event': 'diagnostic_trial_v02_completed', 'output_dir': str(collector.resolve()),
               'accounting_delta': delta,
               'receipts': [summary_named, manifest_named] + self.report_receipts,
               'cumulative_after': after, 'collector_result': result})
        journal_path = self.root / 'trial' / 'journal.json'
        journal = {'schema_version': 1,
                   'producer': 'opengenius-ja-locale-diagnostic-journal-v2',
                   'hash_algorithm': 'sha256 over canonical UTF-8 JSON event excluding event_sha256; chained to prior journal head',
                   'prior_journal': {**previous_journal, 'head_event_sha256': 'f' * 64},
                   'event_count': len(events), 'events': events,
                   'head_event_sha256': previous}
        journal_receipt = write_receipt(journal_path, journal)
        report = {'schema_version': 1, 'producer': 'opengenius-ja-locale-diagnostic-result-v1',
                  'trial_plan': plan_receipt, 'trial_config': config_receipt,
                  'prior_journal': previous_journal,
                  'journal': {**journal_receipt, 'head_event_sha256': previous},
                  'summary': summary_named, 'manifest': manifest_named,
                  'receipts': self.report_receipts, 'collector_result': result,
                  'accounting_delta': delta, 'cumulative_after': after,
                  'no_graph_or_root_credit': True}
        report_receipt = write_receipt(self.root / 'trial' / 'result-report.json', report)
        self.ledger_path = self.root / 'ledger.json'
        self.ledger = {'schema_version': 4,
                       'producer': 'opengenius-genuine-root-attempt-ledger-v4',
                       'prior_progress': self.prior, 'attempt_only_progress': [],
                       'interruption_incidents': [], 'request_only_failure_incidents': [],
                       'diagnostic_capture_receipts': [{
                           'trial_plan': plan_receipt, 'trial_config': config_receipt,
                           'journal': journal_receipt, 'result_report': report_receipt}],
                       'http_requests_total': 31,
                       'excluded_persistent_ids': sorted([prior_pid] + self.pids),
                       'base_outgoing_roots': 1}
        write_receipt(self.ledger_path, self.ledger)

    def tearDown(self):
        self.tmp.cleanup()

    def validate(self):
        with patch.object(runner, '_verify_progress_receipts'):
            return runner._validate_prior_attempt_ledger(
                self.ledger_path, self.library, self.track_hash,
                authorized_rebound_retry_pids=[], current_identity_map_sha256=self.map_hash)

    def test_capture_charges_requests_and_attempts_without_graph_credit(self):
        result = self.validate()
        self.assertEqual((result['http_requests'], len(result['attempted_pids']),
                          result['outgoing_roots']), (31, 21, 1))
        self.assertEqual(result['diagnostic_http_requests'], 28)
        self.assertEqual(result['diagnostic_attempted_pids'], set(self.pids))

    def test_modified_cache_receipt_fails_closed(self):
        cache = next((self.root / 'trial' / 'collector' / 'requests').glob('search-*.json'))
        cache.write_bytes(cache.read_bytes() + b' ')
        with self.assertRaisesRegex(runner.RecoveryUnsafe, 'hash differs'):
            self.validate()

    def test_unaccounted_http_delta_fails_closed(self):
        self.ledger['http_requests_total'] -= 1
        write_receipt(self.ledger_path, self.ledger)
        with self.assertRaisesRegex(runner.RecoveryUnsafe, 'request or root total'):
            self.validate()

    def test_diagnostic_attempts_must_not_overlap_historical_or_incident_sets(self):
        historical = {'F000000000000002'}
        incidents = {'F000000000000003'}
        self.assertFalse(runner._diagnostic_attempts_disjoint(
            ['F000000000000002'], set(), historical, incidents, set()))
        self.assertFalse(runner._diagnostic_attempts_disjoint(
            ['F000000000000003'], set(), set(), incidents, set()))
        self.assertTrue(runner._diagnostic_attempts_disjoint(
            ['0000000000000001'], set(), historical, incidents, set()))

    def test_summary_and_manifest_must_bind_current_library(self):
        summary = {'library_sha256': self.library}
        manifest = {'library_sha256': self.library}
        self.assertTrue(runner._diagnostic_library_matches(
            summary, manifest, self.library))
        self.assertFalse(runner._diagnostic_library_matches(
            summary, {'library_sha256': 'b' * 64}, self.library))

    def test_launch_and_completion_must_be_adjacent_and_ordered(self):
        launch = {'event': 'launch'}
        completion = {'event': 'completion'}
        self.assertTrue(runner._diagnostic_start_completion_order(
            [{'event': 'prepared'}, launch, completion], launch, completion))
        self.assertFalse(runner._diagnostic_start_completion_order(
            [completion, launch], launch, completion))
        self.assertFalse(runner._diagnostic_start_completion_order(
            [launch, {'event': 'unexpected'}, completion], launch, completion))

    def test_result_report_must_bind_outer_journal_hash(self):
        receipt = self.ledger['diagnostic_capture_receipts'][0]
        report_path = Path(receipt['result_report']['path'])
        report = json.loads(report_path.read_text())
        report['journal']['sha256'] = '0' * 64
        report_receipt = write_receipt(report_path, report)
        receipt['result_report'] = report_receipt
        write_receipt(self.ledger_path, self.ledger)
        with self.assertRaisesRegex(runner.RecoveryUnsafe,
                                    'journal hash differs'):
            self.validate()
