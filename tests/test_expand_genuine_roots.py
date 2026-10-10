"""Offline checks for bounded genuine recommendation-root growth."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import expand_genuine_roots as runner


LIBRARY = 'a' * 64


def track(pid, artist='Artist', album=None):
    return {'persistent_id': pid, 'title': 'Song ' + pid[-1], 'artist': artist,
            'album': album or ('Album ' + pid[-1]), 'duration_ms': 100000}


class Matcher:
    def artist_credits(self, artist):
        return (artist,)


class GenuineRootRunnerTests(unittest.TestCase):
    def test_v03_progress_config_without_dynamic_flag_remains_compatible(self):
        config = {'schema_version': 1, 'dynamic_current_radio_hints': False,
                  'deadline': None, 'batch_request_budget': 90,
                  'prior_attempt_ledger': None}
        old = {key: value for key, value in config.items()
               if key not in ('dynamic_current_radio_hints', 'deadline',
                              'batch_request_budget', 'prior_attempt_ledger')}
        self.assertTrue(runner._legacy_progress_compatible(
            {'config': old, 'batches': []}, config))
        self.assertFalse(runner._current_progress_compatible(
            {'config': old, 'batches': []},
            {**config, 'dynamic_current_radio_hints': True}))
        full_config = {**config, 'prior_attempt_ledger': {'path': 'ledger', 'sha256': 'a' * 64},
                       'interval': 3.0, 'authorized_retry_release_pids': ['B' * 16]}
        saved_current = {key: value for key, value in full_config.items()
                         if key != 'dynamic_current_radio_hints'}
        self.assertTrue(runner._current_progress_compatible(
            {'config': saved_current, 'batches': [{'outcome': 'preflight_failed'}]},
            full_config))
        changed_config = {**full_config, 'interval': 4.0}
        self.assertFalse(runner._current_progress_compatible(
            {'config': saved_current, 'batches': []}, changed_config))

    def test_legacy_config_compatibility_skips_only_preflight_manifestless_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'seeds.json'
            manifest.write_text('{}', encoding='utf-8')
            config = {'schema_version': 1, 'dynamic_current_radio_hints': False,
                      'deadline': None, 'batch_request_budget': 90,
                      'prior_attempt_ledger': None}
            old = {key: value for key, value in config.items()
                   if key not in ('dynamic_current_radio_hints', 'deadline',
                                  'batch_request_budget', 'prior_attempt_ledger')}
            progress = {'config': old, 'batches': [
                {'request_budget': 90, 'seed_manifest': {'path': str(manifest)}},
                {'request_budget': 90, 'seed_manifest': None,
                 'status': 'preflight_failed', 'outcome': 'preflight_failed'},
            ]}
            self.assertTrue(runner._legacy_progress_compatible(progress, config))
            progress['batches'][1]['status'] = 'stopped'
            self.assertFalse(runner._legacy_progress_compatible(progress, config))
            self.assertFalse(runner._legacy_progress_compatible(
                {'config': old, 'batches': []},
                {**config, 'dynamic_current_radio_hints': True}))

    def test_orphan_legacy_false_dynamic_flag_is_hash_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'intent.json'
            old = {'schema_version': 1, 'batch_request_budget': 90,
                   'deadline': None, 'previous_growth': None}
            current = {**old, 'dynamic_current_radio_hints': False}
            digest = runner.runner_config_sha256(old)
            path.write_text(json.dumps({'runner_config': old,
                                        'runner_config_sha256': digest}),
                            encoding='utf-8')
            self.assertEqual(runner._orphan_runner_config_sha256(path, current), digest)
            with self.assertRaisesRegex(runner.RecoveryUnsafe, 'intent config differs'):
                runner._orphan_runner_config_sha256(
                    path, {**old, 'dynamic_current_radio_hints': True})
            tampered = {'runner_config': old, 'runner_config_sha256': '0' * 64}
            path.write_text(json.dumps(tampered), encoding='utf-8')
            with self.assertRaisesRegex(runner.RecoveryUnsafe, 'intent config differs'):
                runner._orphan_runner_config_sha256(path, current)

    def test_plan_only_batch_cap_preserves_the_live_runner_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, _ = self._run_fixture(root, max_attempts=10, request_budget=10)
            args.plan_only = True
            args.plan_only_batches = 1
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}):
                progress = runner.run(args)
            self.assertEqual(progress['status'], 'paused_at_checkpoint')
            self.assertNotIn('plan_only_batches', progress['config'])
            self.assertEqual(progress['batches'], [])

    def test_radio_policy_never_releases_same_map_rebound_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, _ = self._run_fixture(
                root, max_attempts=10, request_budget=10, batch_size=1)
            args.plan_only = True
            args.plan_only_batches = 1
            args.source_policy = 'authenticated-watch-radio-only-v1'
            args.prior_attempt_ledger = root / 'ledger.json'
            args.prior_attempt_ledger.write_text('{}', encoding='utf-8')
            pid = '0000000000000001'
            imported = {
                'attempted_pids': {pid}, 'same_map_attempted_pids': set(),
                'http_requests': 1, 'outgoing_roots': 0,
            }
            printed = []
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_authorized_rebound_retry_pids',
                                 return_value={pid}), \
                    patch.object(runner, '_validate_prior_attempt_ledger',
                                 return_value=imported), \
                    patch.object(runner, '_replay_checkpoint',
                                 return_value={'outgoing_root_count': 0}), \
                    patch('builtins.print', side_effect=printed.append):
                progress = runner.run(args)
            self.assertEqual(progress['config']['authorized_retry_release_pids'], [])
            self.assertEqual(progress['attempted_pids'], [pid])
            planned = next(json.loads(line) for line in printed
                           if json.loads(line).get('batch') == 1)
            self.assertNotIn(pid, planned['planned_seeds'])

    def test_dynamic_rebound_hint_frontier_filters_pid_and_video_collisions(self):
        rebound = {'producer': runner.REBOUND_PRODUCER,
                   'rows': [{'persistent_id': 'A' * 16, 'video_id': 'video-a'}]}
        current = {'rows': [
            {'persistent_id': 'A' * 16, 'video_id': 'video-new'},
            {'persistent_id': 'B' * 16, 'video_id': 'video-a'},
            {'persistent_id': 'C' * 16, 'video_id': 'video-c'},
        ]}
        self.assertEqual(runner._dynamic_rebound_hint_pids(current, rebound),
                         {'A' * 16, 'C' * 16})

    @patch.object(runner, '_verify_progress_receipts')
    def test_prior_ledger_replays_nested_request_and_attempt_history(self, _replay):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def save(name, value):
                path = root / name
                raw = json.dumps(value, sort_keys=True).encode()
                path.write_bytes(raw)
                return {'path': str(path.resolve()),
                        'sha256': runner._sha(raw)}

            track_hash, old_map_hash, map_hash = 'a' * 64, 'c' * 64, 'b' * 64
            parent_progress = save('v02-progress.json', {
                'config': {'track_snapshot': {'sha256': track_hash},
                           'identity_map': {'sha256': old_map_hash}},
                'attempted_pids': ['1' * 16, '2' * 16],
                'http_requests': 10, 'successful_outgoing_roots': 3,
                'batches': [],
            })
            parent_ledger = save('v02-ledger.json', {
                'schema_version': 1,
                'producer': 'opengenius-genuine-root-attempt-ledger-v1',
                'prior_progress': parent_progress, 'attempt_only_progress': [],
                'interruption_incidents': [], 'http_requests_total': 10,
                'excluded_persistent_ids': ['1' * 16, '2' * 16],
                'base_outgoing_roots': 3,
            })
            staged_progress = save('v03-progress.json', {
                'config': {'track_snapshot': {'sha256': track_hash},
                           'identity_map': {'sha256': map_hash},
                           'prior_attempt_ledger': parent_ledger,
                           'authorized_retry_release_pids': ['2' * 16]},
                'attempted_pids': ['1' * 16, '2' * 16, '3' * 16],
                'http_requests': 15, 'successful_outgoing_roots': 4,
                'batches': [{'attempted_pids': ['2' * 16, '3' * 16]}],
            })
            ledger_path = root / 'v03-ledger.json'
            ledger = {
                'schema_version': 1,
                'producer': 'opengenius-genuine-root-attempt-ledger-v1',
                'prior_progress': staged_progress, 'attempt_only_progress': [],
                'interruption_incidents': [], 'http_requests_total': 15,
                'excluded_persistent_ids': ['1' * 16, '2' * 16, '3' * 16],
                'base_outgoing_roots': 4,
            }
            ledger_path.write_text(json.dumps(ledger), encoding='utf-8')
            with patch.object(runner, '_verify_progress_receipts') as replay:
                result = runner._validate_prior_attempt_ledger(
                    ledger_path, LIBRARY, track_hash,
                    authorized_rebound_retry_pids={'2' * 16},
                    current_identity_map_sha256=map_hash)
            self.assertEqual(result['http_requests'], 15)
            self.assertEqual(result['attempted_pids'], {'1' * 16, '2' * 16, '3' * 16})
            self.assertEqual(result['same_map_attempted_pids'], {'2' * 16, '3' * 16})
            self.assertEqual({'2' * 16} - result['same_map_attempted_pids'], set())
            self.assertEqual(replay.call_args_list[-1].args[3], {'1' * 16})
            self.assertEqual(replay.call_args_list[-1].args[4], 10)

            def refresh_stage(parent_receipt=None):
                staged = json.loads(Path(staged_progress['path']).read_text())
                if parent_receipt is not None:
                    staged['config']['prior_attempt_ledger'] = parent_receipt
                staged_path = Path(staged_progress['path'])
                staged_raw = json.dumps(staged, sort_keys=True).encode()
                staged_path.write_bytes(staged_raw)
                ledger['prior_progress'] = {
                    'path': str(staged_path.resolve()),
                    'sha256': runner._sha(staged_raw),
                }
                ledger_path.write_text(json.dumps(ledger), encoding='utf-8')

            legacy_staged = json.loads(Path(staged_progress['path']).read_text())
            radio_staged = json.loads(json.dumps(legacy_staged))
            radio_staged['config']['source_policy'] = 'authenticated-watch-radio-only-v1'
            radio_staged['config']['authorized_retry_release_pids'] = []
            radio_staged['batches'][0]['attempted_pids'] = ['3' * 16]
            staged_path = Path(staged_progress['path'])
            staged_raw = json.dumps(radio_staged, sort_keys=True).encode()
            staged_path.write_bytes(staged_raw)
            ledger['prior_progress'] = {
                'path': str(staged_path.resolve()), 'sha256': runner._sha(staged_raw)}
            ledger_path.write_text(json.dumps(ledger), encoding='utf-8')
            with patch.object(runner, '_verify_progress_receipts') as replay:
                radio_result = runner._validate_prior_attempt_ledger(
                    ledger_path, LIBRARY, track_hash,
                    authorized_rebound_retry_pids={'2' * 16},
                    current_identity_map_sha256=map_hash)
            self.assertEqual(radio_result['attempted_pids'],
                             {'1' * 16, '2' * 16, '3' * 16})
            self.assertEqual(replay.call_args_list[-1].args[3],
                             {'1' * 16, '2' * 16})
            bad_radio = json.loads(json.dumps(radio_staged))
            bad_radio['config']['authorized_retry_release_pids'] = ['2' * 16]
            bad_raw = json.dumps(bad_radio, sort_keys=True).encode()
            staged_path.write_bytes(bad_raw)
            ledger['prior_progress'] = {
                'path': str(staged_path.resolve()), 'sha256': runner._sha(bad_raw)}
            ledger_path.write_text(json.dumps(ledger), encoding='utf-8')
            with patch.object(runner, '_verify_progress_receipts'):
                with self.assertRaisesRegex(
                        runner.RecoveryUnsafe, 'retry releases differ from its parent ledger'):
                    runner._validate_prior_attempt_ledger(
                        ledger_path, LIBRARY, track_hash,
                        authorized_rebound_retry_pids={'2' * 16},
                        current_identity_map_sha256=map_hash)
            legacy_raw = json.dumps(legacy_staged, sort_keys=True).encode()
            staged_path.write_bytes(legacy_raw)
            ledger['prior_progress'] = {
                'path': str(staged_path.resolve()), 'sha256': runner._sha(legacy_raw)}
            ledger_path.write_text(json.dumps(ledger), encoding='utf-8')

            staged = json.loads(Path(staged_progress['path']).read_text())
            staged['config']['prior_attempt_ledger']['sha256'] = '0' * 64
            staged_raw = json.dumps(staged, sort_keys=True).encode()
            Path(staged_progress['path']).write_bytes(staged_raw)
            ledger['prior_progress']['sha256'] = runner._sha(staged_raw)
            ledger_path.write_text(json.dumps(ledger), encoding='utf-8')
            with self.assertRaisesRegex(runner.RecoveryUnsafe, 'hash differs'):
                runner._validate_prior_attempt_ledger(
                    ledger_path, LIBRARY, track_hash,
                    authorized_rebound_retry_pids={'2' * 16},
                    current_identity_map_sha256=map_hash)

            parent_doc = json.loads(Path(parent_ledger['path']).read_text())
            parent_doc['http_requests_total'] = 9
            parent_receipt = save('v02-ledger.json', parent_doc)
            refresh_stage(parent_receipt)
            with self.assertRaisesRegex(runner.RecoveryUnsafe, 'request or root total'):
                runner._validate_prior_attempt_ledger(
                    ledger_path, LIBRARY, track_hash,
                    authorized_rebound_retry_pids={'2' * 16},
                    current_identity_map_sha256=map_hash)

            parent_doc['http_requests_total'] = 10
            parent_doc['excluded_persistent_ids'] = ['1' * 16]
            parent_receipt = save('v02-ledger.json', parent_doc)
            refresh_stage(parent_receipt)
            with self.assertRaisesRegex(runner.RecoveryUnsafe, 'PID exclusions'):
                runner._validate_prior_attempt_ledger(
                    ledger_path, LIBRARY, track_hash,
                    authorized_rebound_retry_pids={'2' * 16},
                    current_identity_map_sha256=map_hash)

            parent_doc['excluded_persistent_ids'] = ['1' * 16, '2' * 16]
            parent_receipt = save('v02-ledger.json', parent_doc)
            refresh_stage(parent_receipt)
            staged = json.loads(Path(staged_progress['path']).read_text())
            staged['config']['authorized_retry_release_pids'] = ['3' * 16]
            staged_raw = json.dumps(staged, sort_keys=True).encode()
            Path(staged_progress['path']).write_bytes(staged_raw)
            ledger['prior_progress'] = {
                'path': staged_progress['path'], 'sha256': runner._sha(staged_raw)}
            ledger_path.write_text(json.dumps(ledger), encoding='utf-8')
            with self.assertRaisesRegex(runner.RecoveryUnsafe, 'retry releases'):
                runner._validate_prior_attempt_ledger(
                    ledger_path, LIBRARY, track_hash,
                    authorized_rebound_retry_pids={'2' * 16},
                    current_identity_map_sha256=map_hash)

            with self.assertRaisesRegex(runner.RecoveryUnsafe, 'contains a cycle'):
                runner._validate_prior_attempt_ledger(
                    ledger_path, LIBRARY, track_hash, {ledger_path.resolve()},
                    authorized_rebound_retry_pids={'2' * 16},
                    current_identity_map_sha256=map_hash)

    def test_request_boundary_requires_an_exact_authenticated_request_stop(self):
        valid = {'schema_version': 1, 'account_authentication_verified': True, 'status': 'stopped',
                 'http_requests': 9, 'request_budget': 9, 'error_type': 'RuntimeError',
                 'error_location': {'function': 'request'}}
        self.assertTrue(runner._valid_collector_batch(valid))
        invalid = [
            {**valid, 'http_requests': 8},
            {**valid, 'request_budget': 10},
            {**valid, 'http_requests': None, 'request_budget': None},
            {**valid, 'account_authentication_verified': False},
            {**valid, 'error_type': 'HTTPError'},
            {**valid, 'error_location': {'function': 'parse'}},
            {**valid, 'http_error_status': 429},
        ]
        for summary in invalid:
            with self.subTest(summary=summary):
                self.assertFalse(runner._valid_collector_batch(summary))

    def test_progress_audit_accepts_only_receipted_zero_call_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            growth = root / 'growth'
            growth.mkdir()
            graph = growth / 'accepted-graphs.json'
            graph.write_text('{}', encoding='utf-8')
            graph_hash = runner._sha(graph.read_bytes())
            config = {'batch_request_budget': 90, 'global_request_budget': 6500,
                      'deadline': None,
                      'previous_growth': {'path': str(growth), 'sha256': graph_hash}}
            parent = {'path': str(graph.resolve()),
                      'accepted_graphs_sha256': graph_hash}
            plan = {'path': str((root / 'batch-0003' / 'seed-plan.json').resolve()),
                    'sha256': '1' * 64}
            evidence = {'path': str((root / 'batch-0003' / 'seed-video-evidence.json').resolve()),
                        'sha256': '2' * 64}
            summary = {'path': str((root / 'batch-0003' / 'collector' / 'summary.json').resolve()),
                       'sha256': '3' * 64, 'http_requests': 0, 'request_budget': 90}
            intent = {'path': str((root / 'batch-0003' / 'intent.json').resolve()),
                      'sha256': '4' * 64}
            row = {'batch': 3, 'seed_pids': ['0000000000000001'],
                   'seed_plan': plan, 'seed_manifest': None,
                   'seed_video_evidence': evidence,
                   'collector_summary': summary,
                   'collector_summary_sha256': summary['sha256'],
                   'status': 'preflight_failed', 'outcome': 'preflight_failed',
                   'http_requests': 0, 'request_budget': 90,
                   'attempted_pids': [], 'snapshots': [], 'snapshot_inputs': [],
                   'growth_receipt': {'path': parent['path'], 'sha256': graph_hash}}
            recovered = {'outcome': 'preflight_failed', 'batch': 3,
                         'seed_pids': row['seed_pids'], 'attempted_pids': [],
                         'http_requests': 0, 'collector_summary': summary,
                         'seed_plan': plan, 'seed_video_evidence': evidence,
                         'preflight_intent_receipt': intent}
            progress = {'config': config, 'batches': [row],
                        'attempted_pids': [], 'http_requests': 0}
            with patch.object(runner, 'recover_orphan_batch', return_value=recovered) as recover, \
                    patch.object(runner, '_orphan_runner_config_sha256',
                                 return_value='a' * 64):
                runner._verify_progress_receipts(progress, LIBRARY, 'b' * 64)
            self.assertEqual(row['preflight_intent_receipt'], intent)
            self.assertEqual(recover.call_args.kwargs['expected_previous_growth'], parent)
            self.assertEqual(recover.call_args.kwargs['expected_request_budget'], 90)

            changed = {**progress, 'batches': [{**row, 'attempted_pids': ['0000000000000001']}]}
            with patch.object(runner, 'recover_orphan_batch', return_value=recovered), \
                    patch.object(runner, '_orphan_runner_config_sha256',
                                 return_value='a' * 64):
                with self.assertRaisesRegex(ValueError, 'zero-call recovery receipt'):
                    runner._verify_progress_receipts(changed, LIBRARY, 'b' * 64)

            changed_parent = {**progress, 'batches': [{
                **row, 'growth_receipt': {'path': parent['path'], 'sha256': '5' * 64}}]}
            with patch.object(runner, 'recover_orphan_batch', return_value=recovered), \
                    patch.object(runner, '_orphan_runner_config_sha256',
                                 return_value='a' * 64):
                with self.assertRaisesRegex(ValueError, 'zero-call recovery receipt'):
                    runner._verify_progress_receipts(changed_parent, LIBRARY, 'b' * 64)

    def test_progress_audit_rejects_missing_manifest_for_a_real_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            summary_path = Path(directory) / 'summary.json'
            summary_path.write_text('{}', encoding='utf-8')
            progress = {'config': {}, 'batches': [{
                'outcome': 'merged',
                'collector_summary': {'path': str(summary_path),
                                      'sha256': runner._sha(summary_path.read_bytes())},
                'seed_manifest': None,
                'seed_plan': {'path': '/unused/plan.json', 'sha256': '2' * 64},
                'seed_video_evidence': {'path': '/unused/evidence.json', 'sha256': '3' * 64},
            }]}
            with self.assertRaisesRegex(ValueError, 'lacks a batch file receipt'):
                runner._verify_progress_receipts(progress, LIBRARY, 'b' * 64)

    def test_choose_batch_stops_when_every_candidate_was_already_attempted(self):
        tracks = [track('0000000000000001'), track('0000000000000002')]
        roots = {'0000000000000001': ['0000000000000002']}
        selected = runner.choose_batch(
            tracks, Matcher(), roots,
            {'0000000000000002'}, 2, LIBRARY)
        self.assertEqual(selected, [])

    def test_choose_batch_includes_unattempted_empty_indexed_root_without_incoming_edges(self):
        empty_root = '0000000000000003'
        active_root = '0000000000000001'
        tracks = [track(active_root), track(empty_root)]
        roots = {active_root: [active_root], empty_root: []}
        selected = runner.choose_batch(
            tracks, Matcher(), roots, set(), 1, LIBRARY)
        self.assertEqual(selected, [empty_root])
        self.assertEqual(runner.choose_batch(
            tracks, Matcher(), roots, {empty_root}, 1, LIBRARY), [])

    def test_choose_batch_prefers_known_hints_then_incoming_only_frontier(self):
        pids = ['0000000000000001', '0000000000000002',
                '0000000000000003', '0000000000000004']
        tracks = [track(pid, artist='Artist ' + pid[-1]) for pid in pids]
        roots = {pids[0]: [pids[1]], pids[2]: [pids[0]]}
        selected = runner.choose_batch(
            tracks, Matcher(), roots, set(), 2, LIBRARY, {pids[3]})
        self.assertEqual(selected[0], pids[3])
        self.assertEqual(selected[1], pids[1])
        self.assertNotIn(pids[0], selected)  # Already has outgoing edges.
        self.assertNotIn(pids[2], selected)  # Outgoing root, even without incoming edges.

    def test_rebound_evidence_remains_typed_and_does_not_claim_current_hints(self):
        current = {'producer': 'opengenius-seed-video-evidence-v1', 'rows': [
            {'persistent_id': '0000000000000001', 'video_id': 'abcdefghijk'},
        ]}
        rebound = {'producer': runner.REBOUND_PRODUCER, 'rows': [
            {'persistent_id': '0000000000000002', 'video_id': 'lmnopqrstuv',
             'source_relation': 'rebound_cached_search_candidate'},
        ]}
        document, known = runner._compose_batch_seed_evidence(current, rebound)
        self.assertIs(document, rebound)
        self.assertEqual(known, {'0000000000000002'})
        self.assertNotIn('0000000000000001', known)

    def test_only_map_transition_proven_search_unresolved_rows_release_attempts(self):
        old_map = '2' * 64
        new_map = '3' * 64
        row = {'persistent_id': '0000000000000001', 'source_relation': 'rebound_radio_candidate',
               'additional_source_evidence': [
                   {'source_relation': 'rebound_cached_search_candidate',
                    'historical_result_status': 'search_unresolved',
                    'historical_old_identity_map_sha256': old_map,
                    'revalidated_identity_map_sha256': new_map}
               ]}
        document = {'producer': runner.REBOUND_PRODUCER,
                    'typed_map_transition': {'old_identity_map': {'sha256': old_map},
                                             'new_identity_map': {'sha256': new_map}},
                    'rows': [row]}
        self.assertEqual(runner._authorized_rebound_retry_pids(document, new_map),
                         {'0000000000000001'})
        row['additional_source_evidence'][0]['historical_result_status'] = 'collected_no_graph'
        self.assertEqual(runner._authorized_rebound_retry_pids(document, new_map), set())
        row['additional_source_evidence'][0]['historical_result_status'] = 'search_unresolved'
        self.assertEqual(runner._authorized_rebound_retry_pids(document, old_map), set())

    def _run_fixture(self, root, *, max_attempts=2, request_budget=2, batch_size=1):
        tracks = [track('0000000000000001'), track('0000000000000002')]
        files = {}
        for name, payload in {
                'tracks.json': '{}', 'identity.json': '{}', 'state.json': '{}',
                'graphs.json': json.dumps({'accepted_roots': []}),
                'rebase.json': '{}', 'auth.json': '{}'} .items():
            path = root / name
            path.write_text(payload, encoding='utf-8')
            files[name] = path
        output = root / 'run'
        args = type('Args', (), {
            'track_snapshot': files['tracks.json'], 'identity_map': files['identity.json'],
            'baseline_state': files['state.json'], 'baseline_graph_export': files['graphs.json'],
            'rebase_report': files['rebase.json'], 'output': output,
            'auth': files['auth.json'], 'target_outgoing_roots': 1,
            'global_request_budget': request_budget, 'max_attempts': max_attempts,
            'batch_size': batch_size, 'batch_request_budget': 1, 'interval': 3,
            'deadline': None, 'pause_file': None, 'stop_after_batches': None,
            'plan_only': False})()

        def collect(command):
            collector_dir = Path(command[command.index('--output') + 1])
            plan_path = Path(command[command.index('--seed-plan') + 1])
            evidence_path = Path(command[command.index('--seed-video-evidence') + 1])
            seeds = json.loads(plan_path.read_text(encoding='utf-8'))['seeds']
            collector_dir.mkdir(parents=True, exist_ok=True)
            intent = json.loads((plan_path.parent / 'intent.json').read_text(encoding='utf-8'))
            (collector_dir / 'seeds.json').write_text(json.dumps({
                'library_sha256': LIBRARY,
                'identity_map_sha256': runner._sha(files['identity.json'].read_bytes()),
                'seed_plan_sha256': runner._sha(plan_path.read_bytes()),
                'seed_video_evidence_sha256': runner._sha(evidence_path.read_bytes()),
                'seeds': seeds,
            }), encoding='utf-8')
            (collector_dir / 'summary-001.json').write_text(json.dumps({
                'schema_version': 1, 'status': 'completed',
                'account_authentication_verified': True, 'library_sha256': LIBRARY,
                'http_requests': 1, 'request_budget': intent['request_budget'],
                'results': [{'persistent_id': row['persistent_id'],
                             'status': 'search_unresolved'} for row in seeds],
            }), encoding='utf-8')
            return 0

        return args, tracks, collect

    def test_all_unresolved_batches_consume_attempts_and_terminate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, collect = self._run_fixture(root)
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}), \
                    patch.object(runner, '_invoke', side_effect=collect):
                progress = runner.run(args)
            self.assertEqual(progress['status'], 'budget_or_attempt_limit')
            self.assertEqual(progress['attempted_pids'], ['0000000000000001', '0000000000000002'])
            self.assertEqual(progress['http_requests'], 2)
            self.assertEqual(len(progress['batches']), 2)
            self.assertTrue(all(row['outcome'] == 'authenticated_no_graphs'
                                for row in progress['batches']))

    def test_pause_file_stops_before_any_new_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, _ = self._run_fixture(root)
            args.pause_file = root / 'pause'
            args.pause_file.write_text('pause after current checkpoint', encoding='utf-8')
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}), \
                    patch.object(runner, '_invoke', side_effect=AssertionError('must not collect')):
                progress = runner.run(args)
            self.assertEqual(progress['status'], 'paused_at_checkpoint')
            self.assertEqual(progress['pause_reason'], 'pause_file')
            self.assertEqual(progress['http_requests'], 0)
            self.assertEqual(progress['batches'], [])

    def test_removing_pause_file_resumes_same_config_without_repeating_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, collect = self._run_fixture(
                root, max_attempts=1, request_budget=1, batch_size=1)
            args.pause_file = root / 'pause'
            args.pause_file.write_text('pause', encoding='utf-8')
            patches = [
                patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})),
                patch.object(runner, 'IdentityMap', return_value=Matcher()),
                patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}),
            ]
            with patches[0], patches[1], patches[2], \
                    patch.object(runner, '_invoke', side_effect=AssertionError('must stay paused')):
                paused = runner.run(args)
            self.assertEqual(paused['status'], 'paused_at_checkpoint')
            args.pause_file.unlink()
            with patches[0], patches[1], patches[2], \
                    patch.object(runner, '_invoke', side_effect=collect) as invoke:
                resumed = runner.run(args)
            self.assertEqual(invoke.call_count, 1)
            self.assertEqual(resumed['status'], 'budget_or_attempt_limit')
            self.assertEqual(resumed['http_requests'], 1)
            self.assertEqual(len(resumed['batches']), 1)

    def test_stop_after_batch_pauses_only_after_checkpoint_is_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, collect = self._run_fixture(root, max_attempts=2,
                                                       request_budget=2, batch_size=1)
            args.stop_after_batches = 1
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}), \
                    patch.object(runner, '_invoke', side_effect=collect) as invoke:
                progress = runner.run(args)
            self.assertEqual(invoke.call_count, 1)
            self.assertEqual(progress['status'], 'paused_at_checkpoint')
            self.assertEqual(progress['pause_reason'], 'stop_after_batches')
            self.assertEqual(len(progress['batches']), 1)
            self.assertEqual(progress['http_requests'], 1)

    def test_resume_rejects_changed_source_hashes_before_more_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, collect = self._run_fixture(root, max_attempts=1, request_budget=1)
            patches = [
                patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})),
                patch.object(runner, 'IdentityMap', return_value=Matcher()),
                patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}),
                patch.object(runner, '_invoke', side_effect=collect),
            ]
            with patches[0], patches[1], patches[2], patches[3]:
                runner.run(args)
            args.baseline_state.write_text('{"changed": true}', encoding='utf-8')
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_invoke', side_effect=AssertionError('must not collect')):
                with self.assertRaisesRegex(ValueError, 'Resume inputs or runner limits differ'):
                    runner.run(args)

    def test_resume_rejects_changed_batch_summary_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, collect = self._run_fixture(root, max_attempts=1, request_budget=1)
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}), \
                    patch.object(runner, '_invoke', side_effect=collect):
                runner.run(args)
            summary_path = root / 'run' / 'batch-0001' / 'collector' / 'summary-001.json'
            summary_path.write_text('{"changed": true}', encoding='utf-8')
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_invoke', side_effect=AssertionError('must not collect')):
                with self.assertRaisesRegex(ValueError, 'file bound by saved progress changed'):
                    runner.run(args)

    def test_completed_v01_progress_migrates_only_when_its_recorded_batch_cap_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, collect = self._run_fixture(root, max_attempts=1, request_budget=1)
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}), \
                    patch.object(runner, '_invoke', side_effect=collect):
                runner.run(args)
            progress_path = root / 'run' / 'progress.json'
            progress = json.loads(progress_path.read_text(encoding='utf-8'))
            progress['config'].pop('batch_request_budget')
            progress['config'].pop('deadline')
            progress['config'].pop('prior_attempt_ledger')
            progress['config'].pop('dynamic_current_radio_hints')
            progress_path.write_text(json.dumps(progress), encoding='utf-8')
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}), \
                    patch.object(runner, '_invoke', side_effect=AssertionError('must not collect')):
                resumed = runner.run(args)
            self.assertEqual(resumed['batches'][0]['request_budget'], 1)
            self.assertEqual(resumed['config']['batch_request_budget'], 1)

    def test_request_boundary_does_not_mark_unreturned_planned_seeds_attempted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, _ = self._run_fixture(
                root, max_attempts=2, request_budget=2, batch_size=2)

            def collect(command):
                collector_dir = Path(command[command.index('--output') + 1])
                plan_path = Path(command[command.index('--seed-plan') + 1])
                evidence_path = Path(command[command.index('--seed-video-evidence') + 1])
                plan = json.loads(plan_path.read_text())
                seeds = plan['seeds']
                collector_dir.mkdir(parents=True, exist_ok=True)
                (collector_dir / 'seeds.json').write_text(json.dumps({
                    'library_sha256': LIBRARY,
                    'identity_map_sha256': runner._sha(args.identity_map.read_bytes()),
                    'seed_plan_sha256': runner._sha(plan_path.read_bytes()),
                    'seed_video_evidence_sha256': runner._sha(evidence_path.read_bytes()),
                    'seeds': seeds}), encoding='utf-8')
                if 'batch-0001' in str(collector_dir):
                    status = {'status': 'stopped', 'http_requests': 1,
                              'request_budget': 1, 'error_type': 'RuntimeError',
                              'error_location': {'function': 'request'},
                              'results': [{'persistent_id': seeds[0]['persistent_id'],
                                           'status': 'search_unresolved'}]}
                else:
                    status = {'status': 'completed', 'http_requests': 1,
                              'request_budget': 1,
                              'results': [{'persistent_id': row['persistent_id'],
                                           'status': 'search_unresolved'} for row in seeds]}
                (collector_dir / 'summary-001.json').write_text(json.dumps({
                    'schema_version': 1, 'account_authentication_verified': True, **status,
                    'library_sha256': LIBRARY,
                }), encoding='utf-8')
                return 0

            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}), \
                    patch.object(runner, '_invoke', side_effect=collect):
                progress = runner.run(args)
            self.assertEqual(len(progress['batches']), 2)
            self.assertEqual(len(progress['batches'][0]['seed_pids']), 2)
            self.assertEqual(len(progress['batches'][1]['seed_pids']), 1)
            self.assertEqual(progress['batches'][0]['seed_pids'][0],
                             progress['rejected_or_unresolved_pids'][0])
            self.assertEqual(progress['batches'][1]['seed_pids'],
                             [progress['batches'][0]['seed_pids'][1]])
            self.assertEqual(set(progress['attempted_pids']),
                             {'0000000000000001', '0000000000000002'})

    def test_interruption_after_collector_recovers_without_repeating_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, collect = self._run_fixture(
                root, max_attempts=1, request_budget=2)
            common = [
                patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})),
                patch.object(runner, 'IdentityMap', return_value=Matcher()),
                patch.object(runner, '_replay_checkpoint', return_value={'outgoing_root_count': 0}),
            ]
            with common[0], common[1], common[2], \
                    patch.object(runner, '_invoke', side_effect=collect), \
                    patch.object(runner, '_promote_orphan', side_effect=RuntimeError('simulated crash')):
                with self.assertRaisesRegex(RuntimeError, 'simulated crash'):
                    runner.run(args)
            with common[0], common[1], common[2], \
                    patch.object(runner, '_invoke', side_effect=AssertionError('must not collect again')):
                progress = runner.run(args)
            self.assertEqual(progress['attempted_pids'], ['0000000000000001'])
            self.assertEqual(progress['http_requests'], 1)
            self.assertEqual(len(progress['batches']), 1)
            self.assertEqual(progress['batches'][0]['outcome'], 'authenticated_no_graphs')

    def test_interruption_after_merge_replays_output_before_progress_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, tracks, _ = self._run_fixture(root, max_attempts=1, request_budget=8)
            args.batch_request_budget = 8
            args.global_request_budget = 8
            pid = '0000000000000001'

            def invoke(command):
                if 'probe_ytmusic_batch.py' in command[1]:
                    collector_dir = Path(command[command.index('--output') + 1])
                    plan_path = Path(command[command.index('--seed-plan') + 1])
                    evidence_path = Path(command[command.index('--seed-video-evidence') + 1])
                    seeds = json.loads(plan_path.read_text())['seeds']
                    collector_dir.mkdir(parents=True, exist_ok=True)
                    (collector_dir / 'seeds.json').write_text(json.dumps({
                        'library_sha256': LIBRARY,
                        'identity_map_sha256': runner._sha(args.identity_map.read_bytes()),
                        'seed_plan_sha256': runner._sha(plan_path.read_bytes()),
                        'seed_video_evidence_sha256': runner._sha(evidence_path.read_bytes()),
                        'seeds': seeds}), encoding='utf-8')
                    video = 'abcdefghijk'
                    snapshot = {
                        'schema_version': 1, 'origin': 'probe_ytmusic_batch',
                        'account_authentication_verified': True, 'library_sha256': LIBRARY,
                        'identity_map_sha256': runner._sha(args.identity_map.read_bytes()),
                        'selected_video_id': video, 'local_seed': {'persistent_id': pid},
                        'related_status': 'collected',
                        'requests': [{'endpoint': 'watch_radio'}, {'endpoint': 'watch_seed'},
                                     {'endpoint': 'song_related'}],
                        'observations': [],
                    }
                    for relation, video_id, title, is_seed in (
                            ('radio', 'lmnopqrstuv', 'Radio', False),
                            ('related', 'mnopqrstuvw', 'Related', False),
                            ('radio', video, 'Seed', True)):
                        snapshot['observations'].append({
                            'source': 'ytmusic', 'relation': relation, 'section': None,
                            'position': 1, 'observed_at': '2026-10-09T00:00:00+00:00',
                            'is_seed': is_seed,
                            'track': {'video_id': video_id, 'title': title,
                                      'artists': [{'name': 'Artist'}]},
                        })
                    (collector_dir / (pid + '.json')).write_text(
                        json.dumps(snapshot), encoding='utf-8')
                    (collector_dir / 'summary-001.json').write_text(json.dumps({
                        'schema_version': 1, 'status': 'completed',
                        'account_authentication_verified': True, 'library_sha256': LIBRARY,
                        'http_requests': 3, 'request_budget': 8,
                        'results': [{'persistent_id': pid, 'status': 'collected'}],
                    }), encoding='utf-8')
                    return 0
                output_dir = Path(command[command.index('--output') + 1])
                collector_dir = Path(command[command.index('--seed-manifest') + 1]).parent
                summary_path = Path(command[command.index('--collector-summary') + 1])
                manifest_path = Path(command[command.index('--seed-manifest') + 1])
                snapshot_path = Path(command[command.index('--snapshot') + 1])
                summary = json.loads(summary_path.read_text())
                manifest = json.loads(manifest_path.read_text())
                inputs = [{'path': str(snapshot_path.resolve()),
                           'sha256': runner._sha(snapshot_path.read_bytes())}]
                run = {
                    'collector_summary': {
                        'path': str(summary_path.resolve()),
                        'sha256': runner._sha(summary_path.read_bytes()),
                        'http_requests': summary['http_requests'],
                        'request_budget': summary['request_budget']},
                    'seed_manifest': {
                        'path': str(manifest_path.resolve()),
                        'sha256': runner._sha(manifest_path.read_bytes()),
                        'seed_count': len(manifest['seeds'])},
                    'snapshot_inputs': inputs,
                }
                output_dir.mkdir()
                (output_dir / 'accepted-graphs.json').write_text(json.dumps({
                    'producer': 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1',
                    'growth_runs': [run], 'accepted_roots': [
                        {'root_pid': pid, 'ordered_target_pids': ['0000000000000002']}],
                }), encoding='utf-8')
                return 0

            def replay(_args, previous_growth):
                if previous_growth is None:
                    return {'outgoing_root_count': 0, 'accepted_graphs_sha256': None}
                path = Path(previous_growth) / 'accepted-graphs.json'
                return {'outgoing_root_count': 1,
                        'accepted_graphs_sha256': runner._sha(path.read_bytes())}

            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch('evaluate_ytmusic_batch.collect_graphs', return_value=([{'root_pid': pid}], [], [])), \
                    patch.object(runner, '_replay_checkpoint', side_effect=replay), \
                    patch.object(runner, '_invoke', side_effect=invoke), \
                    patch.object(runner, '_write_atomic', side_effect=RuntimeError('simulated crash')):
                with self.assertRaisesRegex(RuntimeError, 'simulated crash'):
                    runner.run(args)
            growth_path = root / 'run' / 'batch-0001' / 'growth' / 'accepted-graphs.json'
            self.assertTrue(growth_path.exists())
            with patch.object(runner, 'load_library', return_value=(tracks, LIBRARY, {})), \
                    patch.object(runner, 'IdentityMap', return_value=Matcher()), \
                    patch.object(runner, '_replay_checkpoint', side_effect=replay), \
                    patch.object(runner, '_invoke', side_effect=AssertionError('must not collect again')):
                progress = runner.run(args)
            self.assertEqual(progress['successful_outgoing_roots'], 1)
            self.assertEqual(progress['http_requests'], 3)
            self.assertEqual(progress['batches'][0]['outcome'], 'merged')


if __name__ == '__main__':
    unittest.main()
