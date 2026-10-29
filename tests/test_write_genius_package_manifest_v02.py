import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.write_genius_package_manifest_v02 import write_manifest


def put(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def digest(path):
    path = Path(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    rows = [[child.relative_to(path).as_posix(), hashlib.sha256(child.read_bytes()).hexdigest()]
            for child in sorted(path.rglob('*')) if child.is_file()]
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()


class PackageManifestTests(unittest.TestCase):
    def fixture(self, root):
        package = root / 'package'
        bundle = package / 'Music Library.musiclibrary'
        bundle.mkdir(parents=True)
        for name, doc in (('report.json', {}), ('plan.json', {}), ('state.json', {})):
            put(package / name, doc)
        put(bundle / 'Library.musicdb', {'copy': True})
        growth = root / 'growth'
        for name in ('accepted-graphs.json', 'accepted-evaluation.json', 'state.json', 'merge-audit.json'):
            put(growth / name, {'name': name})
        progress = put(root / 'progress.json', {'status': 'target_reached'})
        graph_path = growth / 'accepted-graphs.json'
        package_state = package / 'state.json'
        topology = put(root / 'graph-topology.json', {
            'producer': 'opengenius-copied-genius-graph-topology-audit-v1',
            'status': 'passed', 'active_graph_roots': 1000, 'ordered_edges': 5000,
            'accepted_pid_vectors_match_output_state_and_sql': True,
            'state_only_assignments_are_empty': True,
            'inputs_sha256': {str(graph_path.resolve()): digest(graph_path),
                              str(package_state.resolve()): digest(package_state),
                              str(bundle.resolve()): digest(bundle)},
        })
        hashes = {
            'accepted_graphs': digest(growth / 'accepted-graphs.json'),
            'accepted_evaluation': digest(growth / 'accepted-evaluation.json'),
            'state': digest(growth / 'state.json'),
            'merge_audit': digest(growth / 'merge-audit.json'),
            'progress': digest(progress),
        }
        audit = {'producer': 'opengenius-independent-ytmusic-source-audit-v1', 'status': 'passed',
                 'independent_full_chain_replay': {'status': 'passed'},
                 'source_root_origin': {'goal_metric': 'total-genuine',
                     'counts': {'total_active': 1000, 'native_only': 159,
                                'ytmusic_source_backed': 841, 'native_ytmusic_overlap': 0,
                                'unknown': 0}}, 'final_files_sha256': hashes, 'source_closure_sha256': {}}
        audit_path = put(root / 'audit.json', audit)
        receipt = put(root / 'audit.receipt.json', {'source_audit_path': str(audit_path.resolve()),
            'source_audit_sha256': digest(audit_path), 'checkpoint_path': str(growth.resolve())})
        preflight = put(root / 'preflight.json', {'status': 'ready_for_separate_copy_packaging',
            'goal_metric': 'total-genuine', 'qualifying_roots': 1000, 'unknown_root_origins': 0})
        index = put(root / 'index.json', {'sqlite_integrity': {'baseline': 'ok', 'output': 'ok'},
            'baseline_library_track_count': 3004, 'output_library_track_count': 3004,
            'existing_pid_count': 515, 'existing_pid_gid_and_metadata_preserved': True,
            'sync_revision_mismatch_count': 0, 'music_core_sample': [{}, {}, {}, {}, {}]})
        misc = [put(root / name, {'p': name}) for name in
                ('projection.json', 'baseline-state.json', 'map.json', 'snapshot.json', 'music.bin', 'tool.py')]
        original = root / 'original.musiclibrary'
        put(original / 'Library.musicdb', {'original': True})
        return dict(package_dir=package, growth_dir=growth, progress=progress, source_audit=audit_path,
                    source_audit_receipt=receipt, source_preflight=preflight, index_audit=index,
                    graph_topology_audit=topology,
                    baseline_projection=misc[0], baseline_state=misc[1], original_bundle=original,
                    identity_map=misc[2], track_snapshot=misc[3], music_executable=misc[4],
                    code_paths=[misc[5]], output=package / 'final-hash-manifest.json')

    def test_seals_a_passed_target_and_all_package_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            result = write_manifest(**args)
            manifest = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
        self.assertEqual(result['status'], 'sealed')
        self.assertEqual(manifest['source_audit']['active_roots'], 1000)
        self.assertTrue(any('Music Library.musiclibrary' in item['path'] for item in manifest['package_outputs_sha256']))

    def test_rejects_sync_revision_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            index = json.loads(args['index_audit'].read_text())
            index['sync_revision_mismatch_count'] = 1
            args['index_audit'].write_text(json.dumps(index))
            with self.assertRaisesRegex(ValueError, 'SQL/revision/MusicCore audit did not pass'):
                write_manifest(**args)

    def test_rejects_sql_topology_report_not_bound_to_accepted_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            topology = json.loads(args['graph_topology_audit'].read_text())
            topology['accepted_pid_vectors_match_output_state_and_sql'] = False
            args['graph_topology_audit'].write_text(json.dumps(topology))
            with self.assertRaisesRegex(ValueError, 'copied SQL topology'):
                write_manifest(**args)

    def test_rejects_wrong_graph_hash_in_topology_report(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            topology = json.loads(args['graph_topology_audit'].read_text())
            graph_path = Path(args['growth_dir']) / 'accepted-graphs.json'
            topology['inputs_sha256'][str(graph_path.resolve())] = '0' * 64
            args['graph_topology_audit'].write_text(json.dumps(topology))
            with self.assertRaisesRegex(ValueError, 'copied SQL topology'):
                write_manifest(**args)


if __name__ == '__main__':
    unittest.main()
