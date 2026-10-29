"""Structural-only tests for the typed radio-baseline policy boundary.

The fixture uses the actual accepted title-child documents. It does not call
the expensive raw-source verifier and does not confer source-credit status.
The complete campaign structural harness must still substitute only its
previously completed baseline-verifier result.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v13-typed-baseline-cache-v01')
SCRIPTS = RUNTIME / 'scripts'
os.environ.setdefault('OPENGENIUS_TRUSTED_DATA_ROOT', str(ROOT))
sys.path.insert(0, str(SCRIPTS))

import merge_rebased_radio_growth as merger  # noqa: E402

AUDITOR_SPEC = importlib.util.spec_from_file_location(
    '_final_ytmusic_source_audit_v20', ROOT / 'scripts/audit_final_ytmusic_source_v20.py')
auditor = importlib.util.module_from_spec(AUDITOR_SPEC)
AUDITOR_SPEC.loader.exec_module(auditor)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class VerifiedRadioBaselinePolicyTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline_dir = ROOT / 'data/ytmusic/shiny-ost-title-alias-growth-child-20261014-v01'
        cls.graph_path = cls.baseline_dir / 'accepted-baseline-graphs.json'
        cls.report_path = cls.baseline_dir / 'identity-map-rebinding-report.json'
        cls.state_path = cls.baseline_dir / 'state.json'
        cls.graph_bytes = cls.graph_path.read_bytes()
        cls.report_bytes = cls.report_path.read_bytes()
        cls.state_bytes = cls.state_path.read_bytes()
        cls.graph = json.loads(cls.graph_bytes)
        cls.report = json.loads(cls.report_bytes)
        cls.state = json.loads(cls.state_bytes)
        cls.library_hash = cls.report['zero_id_library_sha256']
        cls.identity_hash = cls.report['identity_map_sha256']
        cls.roots = cls.graph['accepted_roots']
        cls.provenance = {
            'graph_export_path': str(cls.graph_path.resolve()),
            'graph_export_sha256': digest(cls.graph_bytes),
            'rebase_report_path': str(cls.report_path.resolve()),
            'rebase_report_sha256': digest(cls.report_bytes),
            'state_path': str(cls.state_path.resolve()),
            'state_file_sha256': digest(cls.state_bytes),
            # Structural fixture only; a production call receives this from
            # the successful _verify_rebase_receipt return value.
            'proof_input_receipts': cls.report['raw_input_provenance'],
            'raw_observation_receipt_count': len(cls.report['raw_input_provenance']),
            'anchored_observation_count': len(
                cls.report['relation_revalidation']['accepted_input_manifest']
                ['anchored_observation_inputs']),
        }
        cls.transition = merger._verified_radio_baseline_policy_transition(
            graph_doc=cls.graph, report=cls.report, state=cls.state,
            roots=cls.roots, provenance=cls.provenance,
            library_hash=cls.library_hash, identity_hash=cls.identity_hash)

    def test_actual_baseline_transition_counts_active_roots_not_empty_rows(self):
        graph_ref = self.transition['baseline_graph_export']
        self.assertEqual(graph_ref['accepted_pid_row_count'], 943)
        self.assertEqual(graph_ref['nonempty_root_count'], 782)
        self.assertEqual(graph_ref['directed_edge_count'], 18271)
        self.assertEqual(self.transition['baseline_state']['assignment_count'], 972)
        self.assertEqual(self.transition['first_radio_run_index'], 0)
        self.assertEqual(self.transition['inheritance_http_requests'], 0)
        self.assertEqual(self.transition['inheritance_attempt_delta'], 0)

    def test_first_radio_run_is_bound_once_and_only_at_index_zero(self):
        runs = [
            {'source_policy': 'authenticated-watch-radio-only-v1',
             'source_policy_transition': self.transition},
            {'source_policy': 'authenticated-watch-radio-only-v1'},
        ]
        self.assertEqual(merger._validate_source_policy_history(
            'authenticated-watch-radio-only-v1', self.transition, runs), 0)
        with self.assertRaises(ValueError):
            merger._validate_source_policy_history(
                'authenticated-watch-radio-only-v1', self.transition,
                [{'source_policy': 'authenticated-watch-radio-only-v1'}, runs[0]])
        with self.assertRaises(ValueError):
            merger._validate_source_policy_history(
                'authenticated-watch-radio-only-v1', self.transition,
                [runs[0], {**runs[1], 'source_policy_transition': self.transition}])

    def test_transition_replays_exact_verified_baseline_receipts(self):
        merger._validate_verified_radio_baseline_transition(
            self.transition, self.state, self.roots, self.provenance,
            matcher=None, library_hash=self.library_hash,
            identity_hash=self.identity_hash)
        bad = json.loads(json.dumps(self.transition))
        bad['baseline_graph_export']['directed_edge_count'] += 1
        with self.assertRaises(ValueError):
            merger._validate_verified_radio_baseline_transition(
                bad, self.state, self.roots, self.provenance, matcher=None,
                library_hash=self.library_hash, identity_hash=self.identity_hash)

    def test_boolean_counters_cannot_masquerade_as_integers(self):
        for path in (
            ('inheritance_http_requests',),
            ('inheritance_attempt_delta',),
            ('baseline_graph_export', 'accepted_pid_row_count'),
            ('baseline_graph_export', 'nonempty_root_count'),
            ('baseline_graph_export', 'directed_edge_count'),
            ('baseline_state', 'assignment_count'),
        ):
            bad = json.loads(json.dumps(self.transition))
            node = bad
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = True
            with self.assertRaises(ValueError, msg=str(path)):
                merger._validate_verified_radio_baseline_transition(
                    bad, self.state, self.roots, self.provenance, matcher=None,
                    library_hash=self.library_hash, identity_hash=self.identity_hash)

    def test_policy_or_artifact_receipt_tampering_fails(self):
        for field, value in (
            ('source_policy', 'legacy-related-required-v1'),
            ('library_sha256', '0' * 64),
            ('identity_map_sha256', '0' * 64),
            ('proof_input_receipts_sha256', '0' * 64),
        ):
            bad = json.loads(json.dumps(self.transition))
            bad[field] = value
            with self.assertRaises(ValueError, msg=field):
                merger._validate_verified_radio_baseline_transition(
                    bad, self.state, self.roots, self.provenance, matcher=None,
                    library_hash=self.library_hash, identity_hash=self.identity_hash)
        bad_provenance = dict(self.provenance)
        bad_provenance['graph_export_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            merger._validate_verified_radio_baseline_transition(
                self.transition, self.state, self.roots, bad_provenance,
                matcher=None, library_hash=self.library_hash,
                identity_hash=self.identity_hash)

    def test_untyped_baseline_and_nonzero_transition_counters_reject(self):
        for field, value in (
            ('producer', 'arbitrary-radio-baseline'),
            ('first_radio_run_index', 1),
            ('inheritance_http_requests', 1),
            ('inheritance_attempt_delta', 1),
        ):
            bad = json.loads(json.dumps(self.transition))
            bad[field] = value
            with self.assertRaises(ValueError, msg=field):
                merger._validate_source_policy_history(
                    'authenticated-watch-radio-only-v1', bad,
                    [{'source_policy': 'authenticated-watch-radio-only-v1',
                      'source_policy_transition': bad}])

    def test_final_auditor_requires_the_same_exact_baseline_transition(self):
        proof_receipts = [
            {'path': row['path'], 'sha256': row['sha256']}
            for row in self.report['raw_input_provenance']]
        provenance = dict(self.provenance, proof_input_receipts=proof_receipts)
        transition = merger._verified_radio_baseline_policy_transition(
            graph_doc=self.graph, report=self.report, state=self.state,
            roots=self.roots, provenance=provenance,
            library_hash=self.library_hash, identity_hash=self.identity_hash)
        final_graph = {
            'baseline_state_sha256': self.state['state_sha256'],
            'baseline_accepted_roots': self.roots,
            'baseline_rebase_provenance': provenance,
            'source_policy_transition': transition,
            'growth_runs': [
                {'source_policy': 'authenticated-watch-radio-only-v1',
                 'source_policy_transition': transition},
                {'source_policy': 'authenticated-watch-radio-only-v1'},
            ],
        }
        result = auditor._validate_verified_radio_baseline_policy_transition(final_graph)
        self.assertEqual(result['baseline_nonempty_roots'], 782)
        self.assertEqual(result['source_credit_added_by_transition'], 0)
        bad = json.loads(json.dumps(final_graph))
        bad['growth_runs'][0]['source_policy_transition']['inheritance_http_requests'] = True
        with self.assertRaises(ValueError):
            auditor._validate_verified_radio_baseline_policy_transition(bad)


if __name__ == '__main__':
    unittest.main()
