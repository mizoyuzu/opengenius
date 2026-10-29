import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import preflight_genius_package_v11 as preflight


class FinalPackagePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.growth = self.root / 'growth'
        self.growth.mkdir()
        self.audit_path = self.root / 'source-audit.json'
        inventory = json.loads(preflight.SOURCE_ORIGIN_INVENTORY.read_text())
        pid_sets = inventory['source_pid_sets']
        self.native_pids = set(pid_sets['original_native_baseline_nonempty_root_pids'])
        self.known_yt_pids = set(pid_sets['current_732_ytmusic_source_backed_root_pids'])
        self.extra_pids = set()
        candidate = 0xF000000000000000
        while len(self.extra_pids) < 268:
            pid = f'{candidate:016X}'
            if pid not in self.native_pids | self.known_yt_pids:
                self.extra_pids.add(pid)
            candidate += 1
        self.final_pids = sorted(self.native_pids | self.known_yt_pids | self.extra_pids)
        snapshots = []
        new_graphs = []
        for pid in sorted(self.extra_pids):
            path = self.root / f'{pid}.json'
            path.write_text(json.dumps({'local_seed': {'persistent_id': pid},
                                        'account_authentication_verified': True}))
            snapshots.append({'path': str(path), 'sha256': preflight.sha(path)})
            new_graphs.append({'root_pid': pid,
                               'ordered_target_pids': [f'{(int(pid, 16) + 1) % 2**64:016X}']})
        self.graph = {
            'producer': 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1',
            'library_sha256': preflight.PINNED['library'],
            'identity_map_sha256': preflight.PINNED['map'],
            'accepted_roots': [
                {'root_pid': pid, 'ordered_target_pids': [f'{(int(pid, 16) + 1) % 2**64:016X}']}
                for pid in self.final_pids
            ],
            'growth_runs': [{'source_policy': 'authenticated-watch-radio-only-v1',
                             'snapshot_inputs': snapshots, 'new_graphs': new_graphs}],
        }
        self.state = {
            'source': {'library_sha256': preflight.PINNED['library'],
                       'identity_map_sha256': preflight.PINNED['map']},
            'rows': [{'persistent_id': f'{i:016X}', 'genius_id': i + 1,
                      'metadata_hex': '00', 'similarities_hex': '00'} for i in range(515)],
            'state_sha256': 'state-seal',
        }
        self.evaluation = {
            'library_sha256': preflight.PINNED['library'],
            'identity_map_sha256': preflight.PINNED['map'],
            'selection_policy': None,
            'relation_rows': self.graph['accepted_roots'],
            'virtual_state_sha256': 'state-seal',
        }
        self.merge = {'next_state_sha256': 'state-seal',
                      'output_active_outgoing_root_count': len(self.final_pids)}
        self._write('accepted-graphs.json', self.graph)
        self._write('accepted-evaluation.json', self.evaluation)
        self._write('state.json', self.state)
        self._write('merge-audit.json', self.merge)
        self.audit = {
            'producer': 'opengenius-independent-ytmusic-source-audit-v1',
            'status': 'passed',
            'runtime_manifest_sha256': preflight.PINNED['runtime_manifest'],
            'trusted_runtime_module_count': 174,
            'trusted_runtime_modules_sha256': {
                row['path']: row['sha256']
                for row in json.loads((preflight.RUNTIME_ROOT / 'runtime-manifest.json').read_text())['modules']
            },
            'independent_full_chain_replay': {
                'status': 'passed', 'graph_receipt_matches': True,
                'checkpoint': str(self.growth.resolve()), 'outgoing_root_count': len(self.final_pids),
                'state_sha256': preflight.sha(self.growth / 'state.json'),
                'replayed_state_sha256': 'state-seal',
                'accepted_graphs_sha256': preflight.sha(self.growth / 'accepted-graphs.json'),
                'progress_growth_receipt_sha256': preflight.sha(self.growth / 'accepted-graphs.json'),
            },
            'source_root_origin': {
                'goal_metric': 'ytmusic-backed', 'qualifying_root_count': 841,
                'known_source_inventory_path': str(preflight.SOURCE_ORIGIN_INVENTORY),
                'known_source_inventory_sha256': preflight.SOURCE_ORIGIN_INVENTORY_SHA256,
                'known_source_inventory_scope': inventory['status'],
                'native_only_pids': sorted(self.native_pids),
                'ytmusic_source_backed_pids': sorted(self.known_yt_pids | self.extra_pids),
                'native_ytmusic_overlap_pids': [], 'unknown_pids': [],
                'counts': {'native_only': 159, 'ytmusic_source_backed': 841,
                           'native_ytmusic_overlap': 0, 'unknown': 0, 'total_active': 1000},
            },
            'source_policy_audit': {
                'historical_retrieval_pilot': {
                    'path': str(preflight.PILOT_PROGRESS),
                    'sha256': preflight.PILOT_PROGRESS_SHA256,
                    'profile_path': str(preflight.PILOT_PROFILE),
                    'profile_sha256': preflight.PILOT_PROFILE_SHA256,
                    'batches': 2, 'http_requests': 18, 'attempted_pid_count': 16,
                    'roots_credited': 0,
                    'collector_summary_sha256': 'd418ce8fa713bcef59867b2c59be188d79fcae1c25f940b3c6bcb7c19edf3b13',
                },
            },
            'source_closure_sha256': {},
        }
        self.required_closure = {
            str(self.growth / 'accepted-graphs.json'): preflight.sha(self.growth / 'accepted-graphs.json'),
            str(self.growth / 'accepted-evaluation.json'): preflight.sha(self.growth / 'accepted-evaluation.json'),
            str(self.growth / 'state.json'): preflight.sha(self.growth / 'state.json'),
            str(self.growth / 'merge-audit.json'): preflight.sha(self.growth / 'merge-audit.json'),
            str(preflight.RUNTIME_MANIFEST): preflight.PINNED['runtime_manifest'],
            str(preflight.HISTORICAL_NATIVE_MANIFEST): preflight.PINNED['historical_native_manifest'],
            str(preflight.NATIVE / 'baseline-state.json'): preflight.PINNED['native_state_file'],
            str(preflight.NATIVE / 'accepted-baseline-graphs.json'): preflight.PINNED['native_graph'],
            str(preflight.NATIVE / 'baseline-rebase-report.json'): preflight.PINNED['native_report'],
            str(preflight.NATIVE / 'native-baseline-alias-child-receipt.json'): preflight.PINNED['native_receipt'],
            '/mnt/data/mizoyuzu/Projects/personal/opengenius/data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json': preflight.PINNED['snapshot'],
            '/tmp/opengenius-album-alias-append-v03/03-cafe-stella-map.json': preflight.PINNED['map'],
            str(Path(preflight.__file__).resolve().with_name('audit_final_ytmusic_source_v11.py')):
                preflight.sha(Path(preflight.__file__).resolve().with_name('audit_final_ytmusic_source_v11.py')),
            str(preflight.PILOT_PROGRESS): preflight.PILOT_PROGRESS_SHA256,
            str(preflight.PILOT_PROFILE): preflight.PILOT_PROFILE_SHA256,
            str(Path(preflight.__file__).resolve()): preflight.sha(Path(preflight.__file__).resolve()),
            str(preflight.UNION_ADAPTER): preflight.sha(preflight.UNION_ADAPTER),
            str(preflight.SOURCE_ORIGIN_INVENTORY): preflight.SOURCE_ORIGIN_INVENTORY_SHA256,
        }
        historical_manifest = json.loads(preflight.HISTORICAL_NATIVE_MANIFEST.read_text())
        self.required_closure.update({
            str(preflight.HISTORICAL_NATIVE_ROOT / row['path']): row['sha256']
            for row in historical_manifest['modules']
        })
        self.required_closure.update(inventory['source_artifact_sha256'])
        self.audit['source_closure_sha256'] = self.required_closure
        self._write_audit_and_receipt()

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, name, doc):
        (self.growth / name).write_text(json.dumps(doc), encoding='utf-8')

    def _write_audit_and_receipt(self):
        self.audit_path.write_text(json.dumps(self.audit), encoding='utf-8')
        receipt = {
            'producer': 'opengenius-independent-ytmusic-source-audit-receipt-v1',
            'source_audit_path': str(self.audit_path.resolve()),
            'source_audit_sha256': preflight.sha(self.audit_path),
            'checkpoint_path': str(self.growth.resolve()),
            'accepted_graphs_sha256': preflight.sha(self.growth / 'accepted-graphs.json'),
            'state_file_sha256': preflight.sha(self.growth / 'state.json'),
            'runtime_manifest_sha256': preflight.PINNED['runtime_manifest'],
        }
        self.audit_path.with_name('source-audit.receipt.json').write_text(
            json.dumps(receipt), encoding='utf-8')

    def test_actual_typed_union_origin_vectors_count_only_nonempty_rows(self):
        source_graph = json.loads(Path(
            '/tmp/opengenius-radio-source-map-rematch-v16-732-actual-child-v01/accepted-graphs.json'
        ).read_text())
        current_graph = json.loads(Path(
            '/mnt/data/mizoyuzu/Projects/personal/opengenius/data/ytmusic/automatic-genuine-roots-1000-20261013-v13-radio-search-fusion-live/batch-0001/growth/accepted-graphs.json'
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
        rows = [{'root_pid': pid, 'ordered_target_pids': source.get(pid, current[pid])}
                for pid in current if pid not in native_nonempty]
        final_active = set(self.native_pids) | {pid for pid, edges in source.items() if edges} | {
            pid for pid, edges in current.items() if edges}
        active = preflight._typed_active_root_pids(rows, final_active)
        self.assertEqual(len(active), 609)
        self.assertTrue(self.known_yt_pids <= active)
        self.assertFalse(self.native_pids & active)
        self.assertEqual(len(self.native_pids | active), 768)

    def _assert_refused(self, message):
        with self.assertRaisesRegex(ValueError, message):
            preflight.check_final(self.growth, self.audit_path)

    def test_strict_ytmusic_metric_rejects_1000_total_with_841_source_roots(self):
        self._refuse_after_rebinding_audit('at least 1000 ytmusic-backed')

    def test_accepts_explicit_total_genuine_metric(self):
        self.audit['source_root_origin']['goal_metric'] = 'total-genuine'
        self.audit['source_root_origin']['qualifying_root_count'] = 1000
        self._write_audit_and_receipt()
        with patch.object(preflight, 'sha', side_effect=self._sha_with_test_closure):
            report = preflight.check_final(self.growth, self.audit_path, 'total-genuine')
        self.assertEqual(report['outgoing_nonempty_roots'], 1000)

    def _sha_with_test_closure(self, path):
        path = str(Path(path))
        if path in self.required_closure and path != str(self.growth / 'accepted-graphs.json'):
            return self.required_closure[path]
        return preflight.sha.__wrapped__(path) if hasattr(preflight.sha, '__wrapped__') else _real_sha(path)

    def test_refuses_below_1000_nonempty_roots(self):
        self.graph['accepted_roots'].pop()
        self._write('accepted-graphs.json', self.graph)
        self.evaluation['relation_rows'] = self.graph['accepted_roots']
        self.merge['output_active_outgoing_root_count'] = 999
        self._write('accepted-evaluation.json', self.evaluation)
        self._write('merge-audit.json', self.merge)
        recomputed = preflight._recompute_source_origin(
            self.graph, {row['root_pid'] for row in self.graph['accepted_roots']})
        self.audit['source_root_origin'].update({
            key: value for key, value in recomputed.items() if key != 'inventory_receipts'})
        self.audit['source_root_origin']['qualifying_root_count'] = recomputed['counts']['ytmusic_source_backed']
        self._refuse_after_rebinding_audit('at least 1000')

    def test_refuses_forged_source_count_without_pid_set_support(self):
        self.audit['source_root_origin']['counts']['ytmusic_source_backed'] = 1000
        self._refuse_after_rebinding_audit('source-origin PID sets differ')

    def test_native_ytmusic_overlap_is_classified_once_in_union(self):
        pid = sorted(self.native_pids)[0]
        path = self.root / f'{pid}-yt-source.json'
        path.write_text(json.dumps({'local_seed': {'persistent_id': pid},
                                    'account_authentication_verified': True}))
        run = self.graph['growth_runs'][0]
        run['snapshot_inputs'].append({'path': str(path), 'sha256': preflight.sha(path)})
        run['new_graphs'].append({'root_pid': pid, 'ordered_target_pids': ['ABCDEF0123456789']})
        origin = preflight._recompute_source_origin(self.graph, set(self.final_pids))
        self.assertEqual(origin['counts']['native_ytmusic_overlap'], 1)
        self.assertEqual(origin['counts']['native_only'], 158)
        self.assertEqual(origin['counts']['ytmusic_source_backed'], 842)
        self.assertEqual(origin['counts']['native_only'] + origin['counts']['ytmusic_source_backed'],
                         origin['counts']['total_active'])

    def test_refuses_unknown_active_roots_for_both_metrics(self):
        pid = 'EEEEEEEEEEEEEEEE'
        self.graph['accepted_roots'].append({'root_pid': pid, 'ordered_target_pids': ['1234567890ABCDEF']})
        self._write('accepted-graphs.json', self.graph)
        self.evaluation['relation_rows'] = self.graph['accepted_roots']
        self.merge['output_active_outgoing_root_count'] = 1001
        self._write('accepted-evaluation.json', self.evaluation)
        self._write('merge-audit.json', self.merge)
        recomputed = preflight._recompute_source_origin(
            self.graph, {row['root_pid'] for row in self.graph['accepted_roots']})
        self.audit['source_root_origin'].update({
            key: value for key, value in recomputed.items() if key != 'inventory_receipts'})
        self.audit['source_root_origin']['goal_metric'] = 'ytmusic-backed'
        self.audit['source_root_origin']['qualifying_root_count'] = recomputed['counts']['ytmusic_source_backed']
        self.audit['independent_full_chain_replay']['outgoing_root_count'] = 1001
        self._refuse_after_rebinding_audit('without verified Native or YTMusic origin')

    def test_refuses_changed_ordered_evaluation_rows(self):
        self.evaluation['relation_rows'] = list(reversed(self.graph['accepted_roots']))
        self._write('accepted-evaluation.json', self.evaluation)
        self._refuse_after_rebinding_audit('evaluation relation order differs')

    def test_refuses_state_seal_mismatch(self):
        self.state['state_sha256'] = 'other-seal'
        self._write('state.json', self.state)
        self._refuse_after_rebinding_audit('seals differ')

    def test_refuses_duplicate_root_pid_even_when_count_is_1000(self):
        self.graph['accepted_roots'][-1]['root_pid'] = self.graph['accepted_roots'][0]['root_pid']
        self._write('accepted-graphs.json', self.graph)
        self._refuse_after_rebinding_audit('malformed or duplicated')

    def test_refuses_source_audit_hash_change_after_receipt(self):
        self.audit['independent_full_chain_replay']['outgoing_root_count'] = 9999
        self.audit_path.write_text(json.dumps(self.audit), encoding='utf-8')
        self._refused_receipt_hash()

    def test_refuses_missing_historical_native_runtime_manifest_from_source_closure(self):
        self.audit['source_root_origin']['goal_metric'] = 'total-genuine'
        self.audit['source_root_origin']['qualifying_root_count'] = 1000
        self.audit['source_closure_sha256'].pop(str(preflight.HISTORICAL_NATIVE_MANIFEST))
        self._write_audit_and_receipt()
        with patch.object(preflight, 'sha', side_effect=self._sha_with_test_closure):
            with self.assertRaisesRegex(ValueError, 'historical Native v06 manifest'):
                preflight.check_final(self.growth, self.audit_path, 'total-genuine')

    def test_refuses_missing_historical_native_module_from_source_closure(self):
        self.audit['source_root_origin']['goal_metric'] = 'total-genuine'
        self.audit['source_root_origin']['qualifying_root_count'] = 1000
        manifest = json.loads(preflight.HISTORICAL_NATIVE_MANIFEST.read_text())
        missing = str(preflight.HISTORICAL_NATIVE_ROOT / manifest['modules'][0]['path'])
        self.audit['source_closure_sha256'].pop(missing)
        self._write_audit_and_receipt()
        with patch.object(preflight, 'sha', side_effect=self._sha_with_test_closure):
            with self.assertRaisesRegex(ValueError, 'historical Native v06 module'):
                preflight.check_final(self.growth, self.audit_path, 'total-genuine')

    def _refuse_after_rebinding_audit(self, expected):
        self.audit['independent_full_chain_replay']['state_sha256'] = preflight.sha(self.growth / 'state.json')
        self.audit['independent_full_chain_replay']['accepted_graphs_sha256'] = preflight.sha(self.growth / 'accepted-graphs.json')
        self.audit['independent_full_chain_replay']['progress_growth_receipt_sha256'] = preflight.sha(self.growth / 'accepted-graphs.json')
        self.audit['independent_full_chain_replay']['replayed_state_sha256'] = self.state['state_sha256']
        self.audit['source_closure_sha256'].update({
            str(self.growth / name): preflight.sha(self.growth / name)
            for name in ('accepted-graphs.json', 'accepted-evaluation.json', 'state.json', 'merge-audit.json')})
        self._write_audit_and_receipt()
        self._assert_refused(expected)

    def _refused_receipt_hash(self):
        with self.assertRaisesRegex(ValueError, 'detached receipt'):
            preflight.check_final(self.growth, self.audit_path)


def _real_sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == '__main__':
    unittest.main()
