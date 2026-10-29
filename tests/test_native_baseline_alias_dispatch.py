"""Dedicated static dispatch for the source-verified native alias child."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import merge_rebased_radio_growth as merger
from rebind_native_baseline_alias_chain import _assert_exact_child_receipt


LIBRARY = 'a' * 64
IDENTITY = 'b' * 64
PRODUCER = 'opengenius-typed-native-genius-baseline-alias-child-v1'
GRAPH_PRODUCER = 'verified-identity-map-rebound-state-graph-export'


def digest(data):
    return hashlib.sha256(data).hexdigest()


class NativeBaselineAliasDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_path = self.root / 'state.json'
        self.state_path.write_text(json.dumps({'state_sha256': 'state-seal'}))
        self.report_path = self.root / 'report.json'
        self.graph_path = self.root / 'graph.json'
        audit = {
            'all_original_observation_hashes_verified': True,
            'all_retained_edges_same_pid_and_fresh_metadata_match': True,
            'output_directed_edge_count': 0,
            'dropped_old_directed_edge_count': 0,
            'new_matched_edge_count': 0,
            'source_observation_count': 0,
            'raw_input_provenance': [],
            'accepted_input_manifest': {'anchored_observation_inputs': []},
        }
        report = {
            'schema_version': 1,
            'producer': PRODUCER,
            'next_state_sha256': 'state-seal',
            'zero_id_library_sha256': LIBRARY,
            'identity_map_sha256': IDENTITY,
            'relation_revalidation': audit,
        }
        report_bytes = json.dumps(report).encode()
        self.report_path.write_bytes(report_bytes)
        graph = {
            'schema_version': 1,
            'producer': GRAPH_PRODUCER,
            'source_state_path': str(self.state_path.resolve()),
            'source_state_sha256': digest(self.state_path.read_bytes()),
            'source_identity_map_rebinding_report_sha256': digest(report_bytes),
            'accepted_roots': [],
        }
        self.graph_path.write_text(json.dumps(graph))

    def tearDown(self):
        self.temp.cleanup()

    def test_native_child_uses_only_its_dedicated_verifier(self):
        proof = [{'path': '/verified/source.json', 'sha256': 'c' * 64}]
        typed_proof = {
            'producer': PRODUCER, 'verified': True,
            'library_sha256': LIBRARY, 'identity_map_sha256': IDENTITY,
            'track_count': 3004, 'assignment_count': 515,
            'directed_edge_count': 3341,
            'recording_identity_verified': False, 'recording_edges_added': 0,
            'proof_input_receipts': proof,
        }
        with patch('rebind_native_baseline_alias_chain.verify_native_alias_child_report',
                   return_value=typed_proof) as verifier:
            graph, report, state, provenance = merger._verify_rebase_receipt(
                self.graph_path, self.report_path, self.state_path, LIBRARY, IDENTITY)
        verifier.assert_called_once_with(
            graph_doc=graph, report=report, state=state,
            library_hash=LIBRARY, identity_hash=IDENTITY,
            graph_path=self.graph_path.resolve(),
            report_path=self.report_path.resolve(),
            state_path=self.state_path.resolve(),
            repo_root=Path(merger.__file__).resolve().parents[1])
        self.assertEqual(provenance['proof_input_receipts'], proof)

    def test_native_dispatch_rejects_malformed_verifier_summary(self):
        with patch('rebind_native_baseline_alias_chain.verify_native_alias_child_report',
                   return_value={'verified': True, 'proof_input_receipts': []}):
            with self.assertRaisesRegex(ValueError, 'invalid proof'):
                merger._verify_rebase_receipt(
                    self.graph_path, self.report_path, self.state_path,
                    LIBRARY, IDENTITY)

    def test_unregistered_typed_report_is_rejected(self):
        report = json.loads(self.report_path.read_bytes())
        report['producer'] = 'opengenius-typed-unrecognized-native-child-v1'
        self.report_path.write_text(json.dumps(report))
        graph = json.loads(self.graph_path.read_bytes())
        graph['source_identity_map_rebinding_report_sha256'] = digest(
            self.report_path.read_bytes())
        self.graph_path.write_text(json.dumps(graph))
        with self.assertRaisesRegex(ValueError, 'Unsupported typed identity-map'):
            merger._verify_rebase_receipt(
                self.graph_path, self.report_path, self.state_path, LIBRARY, IDENTITY)

    def test_native_report_does_not_expand_graph_producer_allowlist(self):
        graph = json.loads(self.graph_path.read_bytes())
        graph['producer'] = PRODUCER
        graph['source_identity_map_rebinding_report_sha256'] = digest(
            self.report_path.read_bytes())
        self.graph_path.write_text(json.dumps(graph))
        with self.assertRaisesRegex(ValueError, 'Rebase graph export'):
            merger._verify_rebase_receipt(
                self.graph_path, self.report_path, self.state_path, LIBRARY, IDENTITY)

    def test_child_receipt_must_match_all_fields_and_flags(self):
        receipt = {
            'schema_version': 1,
            'producer': PRODUCER,
            'preserved_state_assignment_count': 515,
            'recording_identity_verified': False,
            'recording_edges_added': 0,
            'network_requests': 0,
        }
        _assert_exact_child_receipt(receipt, dict(receipt))
        altered = dict(receipt, network_requests=1)
        with self.assertRaisesRegex(ValueError, 'complete recomputed receipt'):
            _assert_exact_child_receipt(altered, receipt)
        altered = dict(receipt, unexpected='same hashes do not excuse extra fields')
        with self.assertRaisesRegex(ValueError, 'complete recomputed receipt'):
            _assert_exact_child_receipt(altered, receipt)


if __name__ == '__main__':
    unittest.main()
