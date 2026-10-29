import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_composed_genius_copy_pipeline import (
    GateError, hash_input, sha, validate_index_report, validate_source_audit,
    validate_topology_report, verify_unchanged_inputs,
)


def dump(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ComposedCopyGateTests(unittest.TestCase):
    def fixture(self, root):
        union = root / 'union'
        union.mkdir()
        graph = {'accepted_roots': [
            {'root_pid': f'{n:016X}', 'ordered_target_pids': ['0000000000000002']}
            for n in range(1, 1001)
        ]}
        # Keep the test graph vectors unique; the audit gate only checks active rows.
        graph['accepted_roots'][-1]['root_pid'] = '00000000000003E9'
        graph_path = union / 'candidate-union-graphs.json'
        state_path = union / 'candidate-state.json'
        receipt_path = union / 'composition-receipt.json'
        graph_sha = dump(graph_path, graph)
        state_sha = dump(state_path, {'state_sha256': 'semantic-seal', 'rows': []})
        receipt_sha = dump(receipt_path, {'producer': 'fixture'})
        audit = {
            'producer': 'opengenius-independent-composed-radio-source-audit-v1',
            'status': 'passed', 'goal_target_reached': True,
            'source_root_origin': {'goal_metric': 'total-genuine',
                                   'counts': {'total_active': 1000, 'unknown': 0}},
            'independent_full_chain_replay': {
                'status': 'passed', 'graph_receipt_matches': True,
                'checkpoint': str(union.resolve()),
                'accepted_graphs_sha256': graph_sha,
                'state_sha256': state_sha, 'replayed_state_sha256': 'semantic-seal',
            },
            'final_files_sha256': {'accepted_graphs': graph_sha, 'state': state_sha},
            'source_closure_sha256': {str(receipt_path): receipt_sha},
        }
        audit_path = root / 'audit.json'
        audit_sha = dump(audit_path, audit)
        return union, audit_path, audit_sha

    def test_actual_shape_fixture_passes_only_when_graph_and_audit_are_pinned(self):
        with tempfile.TemporaryDirectory() as temp:
            union, audit, digest = self.fixture(Path(temp))
            result = validate_source_audit(union_dir=union, audit_path=audit,
                                           expected_audit_sha256=digest)
            self.assertEqual(result['active_roots'], 1000)
            self.assertEqual(result['unknown_roots'], 0)
            self.assertEqual(result['audit_sha256'], digest)

    def test_below_target_unknown_origins_and_hash_drift_refuse(self):
        with tempfile.TemporaryDirectory() as temp:
            union, audit, _ = self.fixture(Path(temp))
            doc = json.loads(audit.read_text())
            doc['source_root_origin']['counts']['total_active'] = 999
            digest = dump(audit, doc)
            with self.assertRaises(GateError):
                validate_source_audit(union_dir=union, audit_path=audit,
                                      expected_audit_sha256=digest)
            doc['source_root_origin']['counts'].update(total_active=1000, unknown=1)
            digest = dump(audit, doc)
            with self.assertRaises(GateError):
                validate_source_audit(union_dir=union, audit_path=audit,
                                      expected_audit_sha256=digest)
            with self.assertRaises(GateError):
                validate_source_audit(union_dir=union, audit_path=audit,
                                      expected_audit_sha256='0' * 64)

    def test_candidate_graph_mutation_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            union, audit, digest = self.fixture(Path(temp))
            graph_path = union / 'candidate-union-graphs.json'
            graph = json.loads(graph_path.read_text())
            graph['accepted_roots'][0]['ordered_target_pids'] = []
            dump(graph_path, graph)
            with self.assertRaises(GateError):
                validate_source_audit(union_dir=union, audit_path=audit,
                                      expected_audit_sha256=digest)

    def test_actual_index_and_topology_report_shapes(self):
        # Keys mirror the dictionaries returned by audit_index_expansion.audit_bundles
        # and audit_genius_graph_topology.audit_files, not their CLI summaries.
        validate_index_report({
            'sqlite_integrity': {'baseline': 'ok', 'output': 'ok'},
            'baseline_library_track_count': 3004, 'output_library_track_count': 3004,
            'existing_pid_count': 515, 'existing_pid_gid_and_metadata_preserved': True,
            'sync_revision_mismatch_count': 0,
            'music_core_sample': [{'persistent_id': f'{i:016X}'} for i in range(5)],
        })
        validate_topology_report({
            'producer': 'opengenius-copied-genius-graph-topology-audit-v1',
            'status': 'passed', 'active_graph_roots': 1000,
            'accepted_pid_vectors_match_output_state_and_sql': True,
            'state_only_assignments_are_empty': True,
        }, 1000)
        with self.assertRaises(GateError):
            validate_topology_report({'status': 'passed'}, 1000)

    def test_directory_hash_and_post_stage_input_mutation_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'bundle'
            folder.mkdir()
            member = folder / 'Library.musicdb'
            member.write_bytes(b'original')
            expected = hash_input(folder)
            verify_unchanged_inputs({'bundle': (folder, expected)})
            member.write_bytes(b'changed')
            with self.assertRaises(GateError):
                verify_unchanged_inputs({'bundle': (folder, expected)})


if __name__ == '__main__':
    unittest.main()
