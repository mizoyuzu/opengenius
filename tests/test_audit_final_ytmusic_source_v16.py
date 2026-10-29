import unittest
import json
from pathlib import Path
import tempfile

from scripts import audit_final_ytmusic_source_v16 as auditor


class SourceAwareAuditGateTests(unittest.TestCase):
    def test_v14_lineage_pins_union_acceptance_and_thirteen_no_credit_requests(self):
        result = auditor._verify_v14_continuation_lineage()
        self.assertEqual(result['prior_http_requests'], 3948)
        self.assertEqual(result['prior_attempted_pids'], 1823)
        self.assertEqual(result['accepted_roots'], 768)
        self.assertEqual(result['diagnostic_http_requests'], 13)
        self.assertEqual(result['diagnostic_credit'], 0)

    def test_v14_lineage_rejects_forged_base_root_counter_even_when_resealed(self):
        from unittest.mock import patch
        doc = json.loads(auditor.LINEAGE_PATH.read_text())
        doc['aggregate_before_v14']['accepted_nonempty_roots'] = 1000
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lineage.json'
            path.write_text(json.dumps(doc), encoding='utf-8')
            digest = auditor.sha(path)
            with patch.object(auditor, 'LINEAGE_PATH', path), \
                    patch.object(auditor, 'LINEAGE_SHA256', digest):
                with self.assertRaisesRegex(ValueError, 'aggregate counters'):
                    auditor._verify_v14_continuation_lineage()

    def test_v14_lineage_rejects_resealed_command_with_changed_collection_limit(self):
        from unittest.mock import patch
        doc = json.loads(auditor.V14_INVOCATION_PLAN.read_text())
        argv = doc['argv'][:]
        index = argv.index('--batch-request-budget') + 1
        argv[index] = '61'
        doc['argv'] = argv
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invocation.json'
            path.write_text(json.dumps(doc), encoding='utf-8')
            digest = auditor.sha(path)
            with patch.object(auditor, 'V14_INVOCATION_PLAN', path), \
                    patch.object(auditor, 'V14_INVOCATION_PLAN_SHA256', digest):
                with self.assertRaisesRegex(ValueError, 'invocation plan differs'):
                    auditor._verify_v14_continuation_lineage()

    def test_authoritative_pilot_receipt_preserves_16_no_root_attempts(self):
        pilot = auditor._verify_historical_pilot()
        self.assertEqual(pilot['batches'], 2)
        self.assertEqual(pilot['http_requests'], 18)
        self.assertEqual(pilot['attempted_pid_count'], 16)
        self.assertEqual(pilot['roots_credited'], 0)

    def test_actual_v16_c10_native_fixture_has_609_new_active_source_rows(self):
        source_graph = json.loads(Path(
            '/tmp/opengenius-radio-source-map-rematch-v16-732-actual-child-v01/accepted-graphs.json'
        ).read_text())
        current_graph = json.loads(Path(
            '/tmp/opengenius-radio-source-union-v06-candidate-v01/accepted-graphs.json'
        ).read_text())
        native_graph = json.loads(Path(
            '/tmp/opengenius-final-campaign-native-v13-child-v04/accepted-baseline-graphs.json'
        ).read_text())
        source = {row['root_pid']: row['ordered_target_pids']
                  for row in source_graph['accepted_roots']}
        current = {row['root_pid']: row['ordered_target_pids']
                   for row in current_graph['accepted_roots']}
        native_nonempty = {row['root_pid'] for row in native_graph['accepted_roots']
                           if row['ordered_target_pids']}
        union_rows = []
        for pid in current:
            edges = source.get(pid, current[pid])
            if pid not in native_nonempty:
                union_rows.append({'root_pid': pid, 'ordered_target_pids': edges})
        inventory = json.loads(auditor.SOURCE_ORIGIN_INVENTORY.read_text())
        native = set(inventory['source_pid_sets']['original_native_baseline_nonempty_root_pids'])
        known_yt = set(inventory['source_pid_sets']['current_732_ytmusic_source_backed_root_pids'])
        final_active = native | {pid for pid, edges in source.items() if edges} | {
            pid for pid, edges in current.items() if edges}
        typed = auditor._typed_active_root_pids(union_rows, final_active)
        self.assertEqual(len(typed), 609)
        self.assertEqual(len(native), 159)
        self.assertTrue(known_yt <= typed)
        self.assertFalse(native & typed)
        self.assertEqual(len(native | typed), 768)
        self.assertEqual(len((native | typed) - (native | known_yt)), 36)

    def test_typed_union_does_not_count_empty_rows_and_rejects_duplicates(self):
        active = {'A000000000000001', 'A000000000000002'}
        rows = [
            {'root_pid': 'A000000000000001', 'ordered_target_pids': ['B000000000000001']},
            {'root_pid': 'A000000000000002', 'ordered_target_pids': []},
        ]
        self.assertEqual(auditor._typed_active_root_pids(rows, active),
                         {'A000000000000001'})
        with self.assertRaisesRegex(ValueError, 'repeat a PID'):
            auditor._typed_active_root_pids(rows + [rows[0]], active)
        with self.assertRaisesRegex(ValueError, 'not active'):
            auditor._typed_active_root_pids(
                [{'root_pid': 'C000000000000001', 'ordered_target_pids': ['B']}], active)

    def test_receipt_closure_resolves_runtime_relative_paths_from_code_root(self):
        manifest = auditor.RUNTIME_MANIFEST
        closure = auditor._receipt_closure([manifest])
        module = auditor.UNION_ADAPTER
        self.assertEqual(closure[str(module.resolve())], auditor.sha(module))
        self.assertEqual(closure[str(manifest.resolve())], auditor.sha(manifest))

    def test_arbitrary_document_code_root_cannot_redirect_relative_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'module.py'
            source.write_text('untrusted selected code root')
            manifest = root / 'runtime-manifest.json'
            manifest.write_text(json.dumps({
                'producer': 'untrusted-producer',
                'code_root': str(root),
                'modules': [{'path': 'module.py', 'sha256': auditor.sha(source)}],
            }))
            with self.assertRaisesRegex(ValueError, 'source closure receipt changed'):
                auditor._receipt_closure([manifest])

    def test_strict_ytmusic_goal_rejects_841_source_roots_in_1000_total(self):
        source = {
            'counts': {'native_only': 159, 'ytmusic_source_backed': 841,
                       'native_ytmusic_overlap': 0, 'unknown': 0, 'total_active': 1000},
        }
        with self.assertRaisesRegex(ValueError, 'fewer than 1000 roots.*ytmusic-backed'):
            auditor._require_goal(source, 'ytmusic-backed')

    def test_explicit_total_genuine_metric_accepts_1000_fully_classified_roots(self):
        source = {
            'counts': {'native_only': 159, 'ytmusic_source_backed': 841,
                       'native_ytmusic_overlap': 0, 'unknown': 0, 'total_active': 1000},
        }
        self.assertEqual(auditor._require_goal(source, 'total-genuine'), 1000)

    def test_native_ytmusic_overlap_is_unioned_once(self):
        source = {
            'counts': {'native_only': 158, 'ytmusic_source_backed': 842,
                       'native_ytmusic_overlap': 1, 'unknown': 0, 'total_active': 1000},
        }
        self.assertEqual(auditor._goal_root_count(source, 'total-genuine'), 1000)
        self.assertEqual(auditor._goal_root_count(source, 'ytmusic-backed'), 842)

    def test_unknown_roots_are_rejected_for_either_metric(self):
        source = {
            'counts': {'native_only': 159, 'ytmusic_source_backed': 841,
                       'native_ytmusic_overlap': 0, 'unknown': 1, 'total_active': 1001},
        }
        for metric in ('ytmusic-backed', 'total-genuine'):
            with self.subTest(metric=metric), self.assertRaisesRegex(ValueError, 'exact final-root partition'):
                auditor._goal_root_count(source, metric)


if __name__ == '__main__':
    unittest.main()
