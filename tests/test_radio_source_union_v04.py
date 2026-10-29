import json
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import project_radio_source_union_v04 as union


def read_json(path):
    return json.loads(Path(path).read_bytes())


def fixture_run():
    source = read_json(union.SOURCE_CHILD / 'accepted-graphs.json')
    report = read_json(union.SOURCE_CHILD / 'identity-map-rebinding-report.json')
    current = read_json(union.CURRENT_GROWTH / 'accepted-graphs.json')
    native = read_json(union.NATIVE_BASELINE / 'accepted-baseline-graphs.json')
    parent = read_json(union.SOURCE_PARENT_732)
    source_rows = union._relation_map(source['accepted_roots'], 'test v16 graph')
    current_rows = union._relation_map(current['accepted_roots'], 'test C10 graph')
    native_rows = union._relation_map(native['accepted_roots'], 'test native graph')
    parent_rows = union._relation_map(parent['accepted_roots'], 'test source parent')
    source_active = {pid: edges for pid, edges in source_rows.items() if edges}
    native_active = {pid for pid, edges in native_rows.items() if edges}
    rematch = {pid: edges for pid, edges in source_active.items()
               if pid not in {pid for pid, edges in parent_rows.items() if edges}}
    current_active = {pid: edges for pid, edges in current_rows.items() if edges}
    origins = {pid: source_active.get(pid, current_active.get(pid))
               for pid in current_rows
               if source_active.get(pid, current_active.get(pid))
               and pid not in native_active}
    source_receipts = {
        'graph_export': union._receipt(union.SOURCE_CHILD / 'accepted-graphs.json'),
        'rebase_report': union._receipt(union.SOURCE_CHILD / 'identity-map-rebinding-report.json'),
        'state': union._receipt(union.SOURCE_CHILD / 'state.json'),
        'independent_consumer': union._receipt(union.SOURCE_CHILD / 'independent-consumer-return.json'),
        'terminal_status': union._receipt(union.SOURCE_RUN / 'terminal-status.json'),
        'archive_manifest': union._receipt(union.ARCHIVE_MANIFEST),
    }
    c10 = current['growth_runs'][1]
    run = dict(c10)
    run.update({
        'producer': 'opengenius-radio-typed-source-union-run-v1',
        'source_policy': union.SOURCE_POLICY,
        'http_requests': 0,
        'attempted_pid_delta': 0,
        'current_c10_growth': {
            'collector_delta_run': c10,
            'new_graphs': c10['new_graphs'],
        },
        'typed_source_origin_graphs': [
            {'root_pid': pid, 'ordered_target_pids': edges}
            for pid, edges in origins.items()
        ],
        'rematch_new_graphs': [
            {'root_pid': pid, 'ordered_target_pids': rematch[pid]}
            for pid in sorted(rematch)
        ],
        'source_rematch_transition': {
            'source_policy': union.SOURCE_POLICY,
            'accepted_typed_radio_source_rematch': source_receipts,
            'new_from_source_parent_roots': 35,
            'new_from_source_parent_edges': 1045,
            'recording_identity_verified': False,
            'recording_edges_added': 0,
        },
        'union_nonempty_roots': 768,
        'union_ordered_edges': 18047,
        'shared_v16_c10_active_roots': 732,
        'c10_only_new_roots': 1,
        'c10_only_new_edges': 26,
    })
    return run


class TypedSourceUnionContractTests(unittest.TestCase):
    def test_actual_v04_v16_c10_documents_satisfy_union_contract(self):
        run = fixture_run()
        self.assertIsNone(union._validate_saved_union_run_contract(run))

    def test_actual_planner_union_preserves_empty_assigned_pids_without_counting_them(self):
        source_graph = read_json(union.SOURCE_CHILD / 'accepted-graphs.json')
        source_state = read_json(union.SOURCE_CHILD / 'state.json')
        current = read_json(union.CURRENT_GROWTH / 'accepted-graphs.json')
        baseline = read_json(union.NATIVE_BASELINE / 'baseline-state.json')
        source_by_pid = {row['root_pid']: row for row in source_graph['accepted_roots']}
        union_roots = []
        for row in current['accepted_roots']:
            authoritative = source_by_pid.get(row['root_pid'])
            union_roots.append(
                row if authoritative is None else
                {**row, 'ordered_target_pids': list(authoritative['ordered_target_pids'])})
        union_relations = union._relation_map(union_roots, 'test union graph')
        source_state_rows = union._rows_by_pid(source_state, 'test v16 state')
        c10_delta = union._relation_map(current['growth_runs'][1]['new_graphs'], 'test C10 delta')

        merger, identity, planner, _ = union._trusted_v10_modules()
        tracks, library_hash, _ = identity.load_library(track_snapshot=union.TRACK_SNAPSHOT)
        identity_hash = union._sha(union.IDENTITY_MAP.read_bytes())
        matcher = identity.IdentityMap(json.loads(union.IDENTITY_MAP.read_bytes()), tracks, library_hash)
        state = planner.plan_genius_update(
            baseline, union_roots, matcher, library_hash, identity_hash)['next_state']
        state_rows = union._rows_by_pid(state, 'test union planner state')
        state_relations = union._relation_map(
            merger._state_graph_rows(state), 'test union planner relations')

        union._validate_planner_union_projection(
            union_relations, state_relations, state_rows, source_state_rows, c10_delta)
        extras = set(state_relations) - set(union_relations)
        self.assertEqual(len(state_rows), 958)
        self.assertEqual(len(union_relations), 929)
        self.assertEqual(sum(bool(edges) for edges in union_relations.values()), 768)
        self.assertEqual(sum(map(len, union_relations.values())), 18047)
        self.assertEqual(len(extras), 29)
        self.assertTrue(all(not state_relations[pid] for pid in extras))

        first_extra = next(iter(extras))
        state_relations[first_extra] = ['TAMPERED-EDGE']
        with self.assertRaises(ValueError):
            union._validate_planner_union_projection(
                union_relations, state_relations, state_rows, source_state_rows, c10_delta)

        state_relations[first_extra] = []
        del state_rows[first_extra]
        with self.assertRaises(ValueError):
            union._validate_planner_union_projection(
                union_relations, state_relations, state_rows, source_state_rows, c10_delta)

    def test_pid_edge_and_order_tampering_are_rejected(self):
        for mutation in ('pid', 'edge', 'order'):
            run = fixture_run()
            rows = run['typed_source_origin_graphs']
            if mutation == 'pid':
                rows[0]['root_pid'] = 'TAMPERED-PID'
            elif mutation == 'edge':
                rows[0]['ordered_target_pids'][0] = 'TAMPERED-EDGE'
            else:
                rows[0]['ordered_target_pids'].reverse()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                union._validate_saved_union_run_contract(run)

    def test_counters_only_forgery_does_not_supply_origin_proof(self):
        run = fixture_run()
        del run['typed_source_origin_graphs']
        del run['rematch_new_graphs']
        with self.assertRaises(ValueError):
            union._validate_saved_union_run_contract(run)

    def test_missing_rematch_source_receipt_is_rejected(self):
        run = fixture_run()
        del run['source_rematch_transition']['accepted_typed_radio_source_rematch']['state']
        with self.assertRaises(ValueError):
            union._validate_saved_union_run_contract(run)

    def test_boolean_values_cannot_forge_integer_zero_or_count_receipts(self):
        mutations = (
            ('http_requests', lambda run: run.__setitem__('http_requests', False)),
            ('attempted_pid_delta', lambda run: run.__setitem__('attempted_pid_delta', False)),
            ('recording_edges_added', lambda run: run['source_rematch_transition'].__setitem__(
                'recording_edges_added', False)),
            ('union_count', lambda run: run.__setitem__('union_nonempty_roots', True)),
            ('rematch_count', lambda run: run['source_rematch_transition'].__setitem__(
                'new_from_source_parent_roots', True)),
        )
        for label, mutate in mutations:
            run = fixture_run()
            mutate(run)
            with self.subTest(label=label), self.assertRaises(ValueError):
                union._validate_saved_union_run_contract(run)

    def test_adapter_path_manifest_and_bytes_are_all_bound(self):
        source = union.V11_UNION_RUNTIME
        manifest = read_json(source / 'runtime-manifest.json')
        helper = source / 'scripts/project_radio_source_union_v04.py'
        receipt = {'path': str(helper.resolve()), 'sha256': union._sha(helper.read_bytes())}
        self.assertEqual(union._verify_adapter_receipt(receipt)[0], helper.resolve())
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            (runtime / 'scripts').mkdir()
            shutil.copy2(helper, runtime / 'scripts/project_radio_source_union_v04.py')
            (runtime / 'runtime-manifest.json').write_text(json.dumps(manifest))
            temp_helper = runtime / 'scripts/project_radio_source_union_v04.py'
            temp_receipt = {'path': str(temp_helper.resolve()),
                            'sha256': union._sha(temp_helper.read_bytes())}
            union._verify_adapter_receipt(temp_receipt, runtime)
            bad_path = {**temp_receipt, 'path': str(helper.resolve())}
            with self.assertRaises(ValueError):
                union._verify_adapter_receipt(bad_path, runtime)
            manifest_path = runtime / 'runtime-manifest.json'
            bad_manifest = json.loads(manifest_path.read_bytes())
            for row in bad_manifest['modules']:
                if row.get('path') == 'scripts/project_radio_source_union_v04.py':
                    row['sha256'] = '0' * 64
            manifest_path.write_text(json.dumps(bad_manifest))
            with self.assertRaises(ValueError):
                union._verify_adapter_receipt(temp_receipt, runtime)
            manifest_path.write_text(json.dumps(manifest))
            altered = runtime / 'scripts/project_radio_source_union_v04.py'
            altered.write_bytes(altered.read_bytes() + b'\n# changed\n')
            with self.assertRaises(ValueError):
                union._verify_adapter_receipt(temp_receipt, runtime)

    def test_pinned_normal_dispatcher_rejects_tampered_payload_before_proof_replay(self):
        runtime = union.V11_UNION_RUNTIME
        merge_path = runtime / 'scripts/merge_rebased_radio_growth.py'
        manifest = read_json(runtime / 'runtime-manifest.json')
        root_text = os.environ.get('OPENGENIUS_TRUSTED_DATA_ROOT')
        os.environ['OPENGENIUS_TRUSTED_DATA_ROOT'] = manifest['trusted_data_root']
        sys.path.insert(0, str(runtime / 'scripts'))
        try:
            spec = importlib.util.spec_from_file_location('_test_v11_union_dispatch', merge_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            with tempfile.TemporaryDirectory() as tmp:
                previous = Path(tmp)
                run = {
                    'producer': 'opengenius-radio-typed-source-union-run-v1',
                    'source_rematch_transition': {
                        'adapter_code': union._receipt(
                            runtime / 'scripts/project_radio_source_union_v04.py'),
                        'trusted_v11_runtime_manifest': union._receipt(
                            runtime / 'runtime-manifest.json'),
                    },
                }
                accepted = {
                    'producer': 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1',
                    'growth_runs': [{}, run],
                }
                (previous / 'accepted-graphs.json').write_text(json.dumps(accepted))
                with self.assertRaisesRegex(ValueError, 'wrong producer, policy'):
                    module._replay_previous_growth(
                        previous, None, None, None, None, None, None)
        finally:
            sys.modules.pop('_test_v11_union_dispatch', None)
            if root_text is None:
                os.environ.pop('OPENGENIUS_TRUSTED_DATA_ROOT', None)
            else:
                os.environ['OPENGENIUS_TRUSTED_DATA_ROOT'] = root_text
            sys.path.remove(str(runtime / 'scripts'))


class LaterRunContractTests(unittest.TestCase):
    class Matcher:
        tracks = []

    class FakeMerger:
        @staticmethod
        def _validate_run_receipt(run, tracks, matcher, library_hash, identity_hash):
            if run.get('valid') is not True:
                raise ValueError('bad receipt')
            return run['new_graphs'], {'source_policy': run.get('source_policy'),
                                       'new_graphs': run['new_graphs']}

        @staticmethod
        def _append_graphs(roots, additions):
            return roots + additions, {}

        @staticmethod
        def plan_genius_update(state, roots, matcher, library_hash, identity_hash):
            return {'next_state': {'state_sha256': 'later-state', 'roots': roots}}

    def test_normal_collector_suffix_replays_and_preserves_source_policy(self):
        roots = [{'root_pid': 'BASE', 'ordered_target_pids': ['TARGET']}]
        run = {'valid': True, 'source_policy': union.SOURCE_POLICY,
               'new_graphs': [{'root_pid': 'NEW', 'ordered_target_pids': ['T2']}],
               'resulting_state_sha256': 'later-state'}
        state, replayed, normalized = union._replay_standard_suffix(
            [run], {'state_sha256': 'before'}, roots, self.Matcher(), 'lib', 'identity',
            self.FakeMerger)
        self.assertEqual(state['state_sha256'], 'later-state')
        self.assertEqual(replayed[-1]['root_pid'], 'NEW')
        self.assertEqual(normalized[0]['source_policy'], union.SOURCE_POLICY)

    def test_later_policy_or_native_projection_substitution_is_rejected(self):
        for run in (
            {'valid': True, 'source_policy': None, 'new_graphs': []},
            {'producer': 'opengenius-radio-native-alias-projection-run-v1'},
        ):
            with self.subTest(run=run), self.assertRaises(ValueError):
                union._replay_standard_suffix(
                    [run], {'state_sha256': 'before'}, [], self.Matcher(), 'lib', 'identity',
                    self.FakeMerger)


if __name__ == '__main__':
    unittest.main()
