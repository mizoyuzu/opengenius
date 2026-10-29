import json
import tempfile
import unittest
from pathlib import Path

from scripts.select_final_musiccore_samples_v01 import select


class MusicCoreSampleSelectionTests(unittest.TestCase):
    def test_selects_five_yt_roots_absent_native_assignments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pids = [f'F{i:015X}' for i in range(8)]
            graph = {'accepted_roots': [{'root_pid': pid, 'ordered_target_pids': ['T' + pid[1:]]}
                                        for pid in pids]}
            audit = {'producer': 'opengenius-independent-ytmusic-source-audit-v1', 'status': 'passed',
                     'independent_full_chain_replay': {'status': 'passed'},
                     'final_files_sha256': {},
                     'source_root_origin': {'goal_metric': 'total-genuine',
                         'counts': {'total_active': 1000, 'unknown': 0},
                         'ytmusic_source_backed_pids': pids, 'native_only_pids': [],
                         'native_ytmusic_overlap_pids': [], 'unknown_pids': []}}
            graph_path, audit_path, native_path = (root / n for n in ('graph.json', 'audit.json', 'native.json'))
            graph_path.write_text(json.dumps(graph), encoding='utf-8')
            audit['final_files_sha256']['accepted_graphs'] = __import__('hashlib').sha256(graph_path.read_bytes()).hexdigest()
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            native_path.write_text(json.dumps({'rows': [{'persistent_id': pids[0]}]}), encoding='utf-8')
            result = select(graph_path, audit_path, native_path, root / 'samples.json')
        self.assertEqual(result['sample_pids'], pids[1:6])

    def test_rejects_unknown_roots_or_under_target_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph_path, audit_path, native_path = (root / n for n in ('g.json', 'a.json', 'n.json'))
            graph_path.write_text(json.dumps({'accepted_roots': []}), encoding='utf-8')
            audit_path.write_text(json.dumps({'producer': 'opengenius-independent-ytmusic-source-audit-v1',
                'status': 'passed', 'independent_full_chain_replay': {'status': 'passed'},
                'source_root_origin': {'goal_metric': 'total-genuine',
                    'counts': {'total_active': 1000, 'unknown': 1}}}), encoding='utf-8')
            native_path.write_text(json.dumps({'rows': []}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '1000 total genuine roots and zero unknowns'):
                select(graph_path, audit_path, native_path, root / 'out.json')


if __name__ == '__main__':
    unittest.main()
