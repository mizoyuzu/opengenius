"""Read-only gate for packaging a source-audited 1,000-root Genius copy."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path('/tmp/opengenius-final-campaign-native-v13-child-v04')
MAP = Path('/tmp/opengenius-album-alias-append-v03/03-cafe-stella-map.json')
SNAPSHOT = ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
ORIGINAL = ROOT / 'data/new-library-rebased-20261009-v03/Music Library.musiclibrary'
MUSIC = Path('/mnt/data/mizoyuzu/Music.app/Contents/MacOS/Music')
RUNTIME_MANIFEST = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v04/runtime-manifest.json')
HISTORICAL_NATIVE_MANIFEST = Path('/tmp/opengenius-final-campaign-runtime-v06/runtime-manifest.json')
RUNTIME_ROOT = RUNTIME_MANIFEST.parent
UNION_ADAPTER = RUNTIME_ROOT / 'scripts/project_radio_source_union_v04.py'
SOURCE_AUDITOR = Path(__file__).resolve().with_name('audit_final_ytmusic_source_v11.py')
SOURCE_AUDITOR_SHA256 = 'dad45c551e4a9c23768717b88603141593beca78f2a789c0b7ea77ab91831512'
PILOT_PROGRESS = ROOT / 'data/ytmusic/retrieval-query-pilot-20261013-v01/progress.json'
PILOT_PROGRESS_SHA256 = '1eea8551bdf70703c556b2273f36f921c2e99e4640f1ef69d33b41ff5c3ce5bf'
PILOT_PROFILE = PILOT_PROGRESS.parent / 'retrieval-profile.json'
PILOT_PROFILE_SHA256 = '56b67fd28d717d22e73db5396cc607b7cd83ad2bdee6de5fe347bf219191a0bb'
RUNTIME_MANIFEST_SHA256 = 'e001a34d8eddb7e702880aca28d665ccd99cf1fb954761956a7fcbf1c79ae46d'
SOURCE_ORIGIN_INVENTORY = ROOT / 'data/ytmusic/root-source-origin-inventory-20261013-v01.json'
SOURCE_ORIGIN_INVENTORY_SHA256 = 'ecc9743c246949b5ddb3eca59491aacf4c6d9fa970ac7970058d75ad24523514'

PINNED = {
    'native_graph': '8d14cf54cc75acb17b7fc15757691b2c7b4528d539dfdf86960475d6068207e3',
    'native_report': 'bdfc0d0789119e58543ff1d2691920e96e7a7ff98b6c00fcfbc06997254578dc',
    'native_receipt': '1bd5c2193d7a9a672704171be87e9b93012abab638ac3f520734f8160aee1809',
    'native_state_file': '0ff818736f942e4e849487dd486fdf7a4b39c993c32369a889c60f6e3e383b95',
    'native_state_seal': '188e671a4d3d6743fd33d025a1684c831d1ddd9f87d72bae08250340eee6bd89',
    'map': '195c9646dc1f579708ebb19b263c2f5c9828bb3e9542e6b6179253aae7fd0d67',
    'snapshot': '53a232dc9818e1039e016858b3c5c85a54e6fe4be899c94fa3cdd8ee44d7587f',
    'library': '1ea4b3a0ee26495ddb5041d2c6683ec85327dfd6aca602a089b884be22c5c5b7',
    'music': 'a8ddce118ebe19132a26d8b4d7efab7020fa38386aadedeb5f9ce629e6981f6a',
    'runtime_manifest': RUNTIME_MANIFEST_SHA256,
    'historical_native_manifest': '2310e0eb13b21da42baebf5f806f71bda37490a3c3f9b228b901bc420acba5f5',
}
HISTORICAL_NATIVE_ROOT = Path('/tmp/opengenius-final-campaign-runtime-v06')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _typed_active_root_pids(rows, active_pids):
    require(isinstance(rows, list), 'typed source-origin graph rows are malformed')
    seen = set()
    active = set()
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get('root_pid'), str)
                and row['root_pid'] and isinstance(row.get('ordered_target_pids'), list),
                'typed source-origin graph row is malformed')
        pid = row['root_pid']
        require(pid not in seen, 'typed source-origin graph rows repeat a PID')
        seen.add(pid)
        edges = row['ordered_target_pids']
        require(all(isinstance(edge, str) and edge for edge in edges),
                'typed source-origin row contains malformed ordered relations')
        if edges:
            active.add(pid)
    require(active <= active_pids,
            'typed source-origin PIDs are not active in the final accepted graph')
    return active


def _recompute_source_origin(graph, active_pids):
    require(SOURCE_ORIGIN_INVENTORY.is_file()
            and sha(SOURCE_ORIGIN_INVENTORY) == SOURCE_ORIGIN_INVENTORY_SHA256,
            'known source-origin inventory is missing or changed')
    inventory = read(SOURCE_ORIGIN_INVENTORY)
    require(inventory.get('producer') == 'opengenius-typed-root-source-origin-inventory-v1'
            and inventory.get('status') ==
            'source_inventory_structural_only_full_whole_source_replay_not_performed',
            'known source-origin inventory has an unexpected producer or scope')
    receipts = inventory.get('source_artifact_sha256')
    require(isinstance(receipts, dict) and len(receipts) >= 600,
            'known source-origin inventory lacks its historical source receipt closure')
    for source, expected_hash in receipts.items():
        path = Path(source)
        require(path.is_file() and isinstance(expected_hash, str) and sha(path) == expected_hash,
                'known historical source input changed: ' + source)
    sets = inventory.get('source_pid_sets', {})
    native = set(sets.get('original_native_baseline_nonempty_root_pids', []))
    yt = set(sets.get('current_732_ytmusic_source_backed_root_pids', []))
    require(len(native) == 159 and len(yt) == 573 and not native & yt
            and not set(sets.get('current_732_unknown_baseline_root_pids', [])),
            'known historical Native/YT source-origin PID sets are inconsistent')

    require(sha(RUNTIME_MANIFEST) == RUNTIME_MANIFEST_SHA256,
            'typed source-union runtime manifest changed')
    runtime_manifest = read(RUNTIME_MANIFEST)
    module_hashes = {row.get('path'): row.get('sha256')
                     for row in runtime_manifest.get('modules', []) if isinstance(row, dict)}
    helper_relative = 'scripts/project_radio_source_union_v04.py'
    require(module_hashes.get(helper_relative) == sha(UNION_ADAPTER),
            'typed source-union adapter is outside the approved runtime closure')

    def validate_typed_union(run, index):
        require(index == 1 and run.get('source_policy') == 'authenticated-watch-radio-only-v1',
                'typed source union is not the authenticated second transition')
        spec = importlib.util.spec_from_file_location('_preflight_source_union_v04', UNION_ADAPTER)
        require(spec is not None and spec.loader is not None,
                'typed source-union consumer cannot be loaded from its pinned path')
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        require(Path(adapter.__file__).resolve() == UNION_ADAPTER.resolve(),
                'typed source-union consumer resolved outside its pinned path')
        # The whole-source audit already ran the normal dispatcher. Recheck the
        # normalized typed rows against fixed documents, not counters or empty rows.
        adapter._validate_saved_union_run_contract(run)
        typed_rows = run.get('typed_source_origin_graphs')
        result = _typed_active_root_pids(typed_rows, active_pids)
        require(len(result) == 609,
                'typed source-origin rows do not contain exactly 609 active YT roots')
        return result

    verified_new = set()
    typed_union_seen = False
    runs = graph.get('growth_runs')
    require(isinstance(runs, list), 'final accepted graph omits normalized growth-run receipts')
    for index, run in enumerate(runs):
        if run.get('producer') == 'opengenius-radio-typed-source-union-run-v1':
            require(not typed_union_seen,
                    'final graph repeats the typed source-union transition')
            verified_new.update(validate_typed_union(run, index))
            typed_union_seen = True
        rows = run.get('new_graphs', [])
        if not rows:
            if run.get('producer') == 'opengenius-radio-native-alias-projection-run-v1':
                require(run.get('http_requests') == 0 and run.get('attempted_pid_delta') == 0,
                        'Native alias projection claims source collection activity')
            else:
                require(run.get('source_policy') == 'authenticated-watch-radio-only-v1',
                        f'empty final growth run {index} lacks authenticated radio policy')
            continue
        require(run.get('source_policy') == 'authenticated-watch-radio-only-v1',
                f'final source growth run {index} lacks authenticated radio policy')
        snapshot_rows = run.get('snapshot_inputs')
        require(isinstance(snapshot_rows, list) and snapshot_rows,
                f'final source growth run {index} lacks snapshot receipts')
        snapshot_pids = set()
        for receipt in snapshot_rows:
            require(isinstance(receipt, dict) and set(receipt) == {'path', 'sha256'},
                    'final source snapshot receipt is malformed')
            path = Path(receipt['path']).resolve()
            require(path.is_file() and sha(path) == receipt['sha256'],
                    'final source snapshot changed: ' + str(path))
            snapshot = read(path)
            seed = snapshot.get('local_seed')
            pid = seed.get('persistent_id') if isinstance(seed, dict) else None
            require(isinstance(pid, str) and pid and snapshot.get('account_authentication_verified') is True,
                    'final source snapshot lacks authenticated seed identity')
            require(pid not in snapshot_pids, 'final source run repeats a snapshot PID')
            snapshot_pids.add(pid)
        run_pids = set()
        for row in rows:
            require(isinstance(row, dict) and isinstance(row.get('root_pid'), str)
                    and isinstance(row.get('ordered_target_pids'), list),
                    'final source graph row is malformed')
            if row['ordered_target_pids']:
                run_pids.add(row['root_pid'])
        require(run_pids <= snapshot_pids,
                f'final source run {index} has a nonempty root without a same-PID authenticated snapshot')
        verified_new.update(run_pids & active_pids)

    yt_active = (yt | verified_new) & active_pids
    native_active = native & active_pids
    overlap = native_active & yt_active
    native_only = native_active - yt_active
    unknown = active_pids - (native_only | yt_active)
    return {
        'native_only_pids': sorted(native_only),
        'ytmusic_source_backed_pids': sorted(yt_active),
        'native_ytmusic_overlap_pids': sorted(overlap),
        'unknown_pids': sorted(unknown),
        'counts': {'native_only': len(native_only), 'ytmusic_source_backed': len(yt_active),
                   'native_ytmusic_overlap': len(overlap), 'unknown': len(unknown),
                   'total_active': len(active_pids)},
        'inventory_receipts': receipts,
    }


def check_native():
    paths = {
        'native_graph': NATIVE / 'accepted-baseline-graphs.json',
        'native_report': NATIVE / 'baseline-rebase-report.json',
        'native_receipt': NATIVE / 'native-baseline-alias-child-receipt.json',
        'native_state_file': NATIVE / 'baseline-state.json',
        'map': MAP,
        'snapshot': SNAPSHOT,
        'music': MUSIC,
        'runtime_manifest': RUNTIME_MANIFEST,
        'historical_native_manifest': HISTORICAL_NATIVE_MANIFEST,
    }
    for key, path in paths.items():
        require(path.is_file(), f'missing pinned input: {path}')
        require(sha(path) == PINNED[key], f'pinned input changed: {path}')
    library_file_hashes = {
        'Library.musicdb': '3869b162bf7be71fe0502dcdb175ca9899c123643170e6c47be702b737c3140c',
        'Genius.itdb': '1fd5019e6eee66d5d2b0dba1f74d9563ce2cfd50d64b417c11d4e1d34275a190',
        'Library Preferences.musicdb': 'ecb3b4619800d6b5b0db08d6ae0470e9281afeb3c9f7c55b90399e9458b6c804',
    }
    for name, expected in library_file_hashes.items():
        require(sha(ORIGINAL / name) == expected, f'original bundle input changed: {name}')
    state = read(NATIVE / 'baseline-state.json')
    graph = read(NATIVE / 'accepted-baseline-graphs.json')
    report = read(NATIVE / 'baseline-rebase-report.json')
    receipt = read(NATIVE / 'native-baseline-alias-child-receipt.json')
    historical_manifest = read(HISTORICAL_NATIVE_MANIFEST)
    require(sha(HISTORICAL_NATIVE_MANIFEST) == PINNED['historical_native_manifest']
            and historical_manifest.get('producer') == 'opengenius-final-campaign-runtime-v06'
            and historical_manifest.get('code_root') == str(HISTORICAL_NATIVE_ROOT)
            and len(historical_manifest.get('modules', [])) == 171,
            'historical Native v06 runtime manifest differs from its fixed pin')
    historical_modules = {row['path']: row['sha256'] for row in historical_manifest['modules']}
    require(len(historical_modules) == 171,
            'historical Native v06 runtime module closure is duplicated or incomplete')
    for relative, digest in historical_modules.items():
        path = HISTORICAL_NATIVE_ROOT / relative
        require(path.is_file() and sha(path) == digest,
                'historical Native v06 runtime module changed: ' + str(path))
    expected_native_code_paths = {
        'scripts/rebind_native_baseline_alias_chain.py',
        'scripts/rebind_genius_baseline_identity.py',
        'scripts/rebind_radio_growth_alias_chain.py',
        'scripts/rebind_radio_growth_identity.py',
        'scripts/rebind_radio_growth_album_append_chain.py',
        'scripts/merge_rebased_radio_growth.py',
        'scripts/plan_genius_update.py',
        'scripts/update_genius_bundle.py',
        'scripts/music_identity_map.py',
    }
    code_inputs = receipt.get('code_inputs')
    require(isinstance(code_inputs, list) and len(code_inputs) == 9,
            'Native child receipt does not contain the exact historical code closure')
    observed_native_code_paths = set()
    for code_input in code_inputs:
        require(isinstance(code_input, dict) and set(code_input) == {'path', 'sha256'},
                'Native child code receipt is malformed')
        path = Path(code_input['path']).resolve()
        try:
            relative = path.relative_to(HISTORICAL_NATIVE_ROOT).as_posix()
        except ValueError:
            raise ValueError('Native child code receipt escapes its historical v06 runtime') from None
        require(relative in expected_native_code_paths
                and relative not in observed_native_code_paths
                and historical_modules.get(relative) == code_input['sha256'] == sha(path),
                'Native child code receipt differs from the historical v06 closure: ' + relative)
        observed_native_code_paths.add(relative)
    require(observed_native_code_paths == expected_native_code_paths,
            'Native child code receipt omits a historical v06 module')
    require(state.get('state_sha256') == PINNED['native_state_seal'], 'native baseline seal changed')
    require(state.get('producer') == 'opengenius-synthetic-rows-v1', 'unexpected native baseline producer')
    require(len(state.get('rows', [])) == 515, 'native baseline must have 515 assignments')
    require(state.get('source', {}).get('library_sha256') == PINNED['library'], 'native state Library mismatch')
    require(state.get('source', {}).get('identity_map_sha256') == PINNED['map'], 'native state map mismatch')
    require(report.get('producer') == 'opengenius-typed-native-genius-baseline-alias-child-v1', 'unexpected rebase report')
    require(report.get('zero_id_library_sha256') == PINNED['library'], 'rebase report Library mismatch')
    require(report.get('identity_map_sha256') == PINNED['map'], 'rebase report map mismatch')
    require(report.get('next_state_sha256') == PINNED['native_state_seal'], 'rebase report state mismatch')
    require(receipt.get('producer') == report.get('producer'), 'native receipt/report producer mismatch')
    require(receipt.get('preserved_track_count') == 3004, 'native receipt track count mismatch')
    require(receipt.get('preserved_state_assignment_count') == 515, 'native receipt assignment count mismatch')
    require(receipt.get('preserved_directed_edge_count') == 3341, 'native receipt edge count mismatch')
    require(receipt.get('pid_gid_assignments_unchanged') is True
            and receipt.get('metadata_bytes_unchanged') is True
            and receipt.get('ordered_relation_bytes_unchanged') is True,
            'native child does not attest exact baseline preservation')
    require(receipt.get('network_requests') == 0, 'native baseline child was not a zero-request operation')
    require(graph.get('producer') == 'verified-identity-map-rebound-state-graph-export', 'unexpected native graph producer')
    require(graph.get('source_state_sha256') == PINNED['native_state_file'], 'native graph state file mismatch')
    require(len(graph.get('accepted_roots', [])) == 515, 'native graph must preserve 515 rows')
    return {'native_state_file_sha256': PINNED['native_state_file'],
            'native_state_sha256': PINNED['native_state_seal'],
            'native_graph_sha256': PINNED['native_graph'],
            'native_rebase_report_sha256': PINNED['native_report'],
            'native_child_receipt_sha256': PINNED['native_receipt'],
            'identity_map_sha256': PINNED['map'], 'library_track_count': 3004,
            'baseline_assignments': 515, 'baseline_ordered_edges': 3341,
            'runtime_manifest_sha256': PINNED['runtime_manifest']}


def check_final(growth_dir, source_audit_path, goal_metric='ytmusic-backed'):
    require(goal_metric in ('ytmusic-backed', 'total-genuine'),
            'unsupported root-count goal metric')
    growth_dir = Path(growth_dir).resolve()
    audit_path = Path(source_audit_path).resolve()
    require(SOURCE_AUDITOR_SHA256 != 'TO_BE_PINNED'
            and SOURCE_AUDITOR.is_file() and sha(SOURCE_AUDITOR) == SOURCE_AUDITOR_SHA256,
            'independent source-auditor code differs from its fixed v11 pin')
    require(growth_dir.is_dir(), 'growth checkpoint directory is missing')
    graph_path = growth_dir / 'accepted-graphs.json'
    evaluation_path = growth_dir / 'accepted-evaluation.json'
    state_path = growth_dir / 'state.json'
    merge_path = growth_dir / 'merge-audit.json'
    for path in (graph_path, evaluation_path, state_path, merge_path, audit_path):
        require(path.is_file(), f'missing final source-audit input: {path}')
    graph, evaluation, state, merge, audit = map(read, (graph_path, evaluation_path, state_path, merge_path, audit_path))
    audit_receipt_path = audit_path.with_name(audit_path.stem + '.receipt.json')
    require(audit_receipt_path.is_file(), 'detached source-audit hash receipt is missing')
    audit_receipt = read(audit_receipt_path)
    require(audit.get('producer') == 'opengenius-independent-ytmusic-source-audit-v1',
            'source audit was not produced by the frozen independent auditor')
    require(audit.get('status') == 'passed'
            and audit.get('runtime_manifest_sha256') == PINNED['runtime_manifest'],
            'source audit is incomplete or bound to a different trusted runtime')
    runtime_manifest = read(RUNTIME_ROOT / 'runtime-manifest.json')
    runtime_modules = runtime_manifest.get('modules', [])
    expected_runtime_modules = {row['path']: row['sha256'] for row in runtime_modules}
    require(len(expected_runtime_modules) == 174
            and audit.get('trusted_runtime_module_count') == 174
            and audit.get('trusted_runtime_modules_sha256') == expected_runtime_modules,
            'source audit runtime module closure differs from the approved manifest')
    for relative, digest in expected_runtime_modules.items():
        path = RUNTIME_ROOT / relative
        require(path.is_file() and sha(path) == digest,
                'approved frozen runtime module changed: ' + str(path))
    historical_manifest = read(HISTORICAL_NATIVE_MANIFEST)
    require(sha(HISTORICAL_NATIVE_MANIFEST) == PINNED['historical_native_manifest']
            and historical_manifest.get('producer') == 'opengenius-final-campaign-runtime-v06'
            and historical_manifest.get('code_root') == str(HISTORICAL_NATIVE_ROOT),
            'historical Native v06 manifest differs from its fixed pin')
    historical_modules = historical_manifest.get('modules', [])
    expected_historical_modules = {row['path']: row['sha256'] for row in historical_modules}
    require(len(expected_historical_modules) == 171,
            'historical Native v06 module closure is duplicated or incomplete')
    for relative, digest in expected_historical_modules.items():
        path = HISTORICAL_NATIVE_ROOT / relative
        require(path.is_file() and sha(path) == digest,
                'historical Native v06 module changed: ' + str(path))
    require(audit_receipt.get('producer') == 'opengenius-independent-ytmusic-source-audit-receipt-v1'
            and audit_receipt.get('source_audit_path') == str(audit_path)
            and audit_receipt.get('source_audit_sha256') == sha(audit_path),
            'detached receipt does not bind the exact source-audit file')
    roots = graph.get('accepted_roots')
    require(isinstance(roots, list), 'final graph has no accepted_roots array')
    active = [row for row in roots if isinstance(row, dict)
              and isinstance(row.get('ordered_target_pids'), list) and row['ordered_target_pids']]
    pids = [row.get('root_pid') for row in active]
    require(all(isinstance(pid, str) and pid for pid in pids) and len(pids) == len(set(pids)),
            'nonempty outgoing root PIDs are malformed or duplicated')
    require(graph.get('producer') == 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1',
            'final graph is not an accepted radio growth merge')
    require(graph.get('library_sha256') == PINNED['library'], 'final graph Library mismatch')
    require(graph.get('identity_map_sha256') == PINNED['map'], 'final graph identity map mismatch')
    require(evaluation.get('library_sha256') == PINNED['library'], 'evaluation Library mismatch')
    require(evaluation.get('identity_map_sha256', PINNED['map']) == PINNED['map'], 'evaluation map mismatch')
    require(evaluation.get('selection_policy') is None, 'Genius package cannot encode an application selection policy')
    require(evaluation.get('relation_rows') == roots, 'evaluation relation order differs from accepted graph')
    require(state.get('source', {}).get('library_sha256') == PINNED['library'], 'final state Library mismatch')
    require(state.get('source', {}).get('identity_map_sha256') == PINNED['map'], 'final state map mismatch')
    require(evaluation.get('virtual_state_sha256') == state.get('state_sha256')
            and merge.get('next_state_sha256') == state.get('state_sha256'), 'final graph/evaluation/state seals differ')
    require(merge.get('output_active_outgoing_root_count') == len(active), 'merge root count differs from final graph')
    expected_origin = _recompute_source_origin(graph, set(pids))
    auditor_spec = importlib.util.spec_from_file_location('_pinned_source_auditor_v11', SOURCE_AUDITOR)
    require(auditor_spec is not None and auditor_spec.loader is not None,
            'pinned independent source auditor cannot be loaded')
    auditor_module = importlib.util.module_from_spec(auditor_spec)
    auditor_spec.loader.exec_module(auditor_module)
    require(Path(auditor_module.__file__).resolve() == SOURCE_AUDITOR.resolve(),
            'source auditor resolved outside its pinned path')
    expected_pilot = auditor_module._verify_historical_pilot()
    origin = audit.get('source_root_origin')
    require(isinstance(origin, dict)
            and origin.get('goal_metric') == goal_metric
            and origin.get('known_source_inventory_path') == str(SOURCE_ORIGIN_INVENTORY)
            and origin.get('known_source_inventory_sha256') == SOURCE_ORIGIN_INVENTORY_SHA256,
            'source audit lacks the pinned source-origin inventory and requested metric')
    require(audit.get('source_policy_audit', {}).get('historical_retrieval_pilot') == expected_pilot,
            'source audit does not preserve the exact no-root retrieval-pilot history')
    for field in ('native_only_pids', 'ytmusic_source_backed_pids',
                  'native_ytmusic_overlap_pids', 'unknown_pids', 'counts'):
        require(origin.get(field) == expected_origin[field],
                'source audit source-origin PID sets differ from independently recomputed evidence: ' + field)
    require(not expected_origin['unknown_pids'],
            'final accepted graph has active roots without verified Native or YTMusic origin')
    qualifying = (expected_origin['counts']['ytmusic_source_backed']
                  if goal_metric == 'ytmusic-backed' else expected_origin['counts']['total_active'])
    require(origin.get('qualifying_root_count') == qualifying and qualifying >= 1000,
            f'packaging requires at least 1000 {goal_metric} roots; found {qualifying}')
    full = audit.get('independent_full_chain_replay', {})
    require(full.get('status') == 'passed', 'independent full-source replay has not passed')
    require(full.get('graph_receipt_matches') is True, 'replay receipt does not match the accepted graph')
    require(Path(full.get('checkpoint', '')).resolve() == growth_dir, 'source audit names a different checkpoint')
    require(full.get('outgoing_root_count') == len(active), 'source audit root count differs from final graph')
    require(full.get('state_sha256') == sha(state_path), 'source audit state file hash mismatch')
    require(full.get('accepted_graphs_sha256') == sha(graph_path)
            and full.get('progress_growth_receipt_sha256') == sha(graph_path),
            'source audit graph/progress receipt hashes differ')
    require(full.get('replayed_state_sha256') == state.get('state_sha256'),
            'independent replay state seal differs from the final state')
    require(audit_receipt.get('checkpoint_path') == str(growth_dir)
            and audit_receipt.get('accepted_graphs_sha256') == sha(graph_path)
            and audit_receipt.get('state_file_sha256') == sha(state_path)
            and audit_receipt.get('runtime_manifest_sha256') == PINNED['runtime_manifest'],
            'detached source-audit receipt does not bind the exact checkpoint and frozen runtime')
    closure = audit.get('source_closure_sha256')
    require(isinstance(closure, dict) and closure, 'source audit has no hashed source closure')
    for source, expected_hash in closure.items():
        path = Path(source)
        require(path.is_file() and isinstance(expected_hash, str) and sha(path) == expected_hash,
                'source audit closure input changed: ' + source)
    require(closure.get(str(SOURCE_ORIGIN_INVENTORY)) == SOURCE_ORIGIN_INVENTORY_SHA256,
            'source audit closure omits or misbinds the known source-origin inventory')
    require(closure.get(str(HISTORICAL_NATIVE_MANIFEST)) == PINNED['historical_native_manifest'],
            'source audit closure omits or misbinds the historical Native v06 manifest')
    for relative, digest in expected_historical_modules.items():
        source = str(HISTORICAL_NATIVE_ROOT / relative)
        require(closure.get(source) == digest,
                'source audit closure omits or misbinds a historical Native v06 module: ' + source)
    for source, expected_hash in expected_origin['inventory_receipts'].items():
        require(closure.get(source) == expected_hash,
                'source audit closure omits or misbinds historical source input: ' + source)
    required_closure = {
        str(graph_path): sha(graph_path), str(evaluation_path): sha(evaluation_path),
        str(state_path): sha(state_path), str(merge_path): sha(merge_path),
        str(RUNTIME_MANIFEST): PINNED['runtime_manifest'],
        str(HISTORICAL_NATIVE_MANIFEST): PINNED['historical_native_manifest'],
        '/tmp/opengenius-final-campaign-native-v13-child-v04/baseline-state.json':
            '0ff818736f942e4e849487dd486fdf7a4b39c993c32369a889c60f6e3e383b95',
        '/tmp/opengenius-final-campaign-native-v13-child-v04/accepted-baseline-graphs.json':
            '8d14cf54cc75acb17b7fc15757691b2c7b4528d539dfdf86960475d6068207e3',
        '/tmp/opengenius-final-campaign-native-v13-child-v04/baseline-rebase-report.json':
            'bdfc0d0789119e58543ff1d2691920e96e7a7ff98b6c00fcfbc06997254578dc',
        '/tmp/opengenius-final-campaign-native-v13-child-v04/native-baseline-alias-child-receipt.json':
            '1bd5c2193d7a9a672704171be87e9b93012abab638ac3f520734f8160aee1809',
        '/mnt/data/mizoyuzu/Projects/personal/opengenius/data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json':
            '53a232dc9818e1039e016858b3c5c85a54e6fe4be899c94fa3cdd8ee44d7587f',
        '/tmp/opengenius-album-alias-append-v03/03-cafe-stella-map.json':
            '195c9646dc1f579708ebb19b263c2f5c9828bb3e9542e6b6179253aae7fd0d67',
        str(SOURCE_AUDITOR): SOURCE_AUDITOR_SHA256,
        str(Path(__file__).resolve()): sha(Path(__file__).resolve()),
        str(UNION_ADAPTER): expected_runtime_modules['scripts/project_radio_source_union_v04.py'],
        str(PILOT_PROGRESS): PILOT_PROGRESS_SHA256,
        str(PILOT_PROFILE): PILOT_PROFILE_SHA256,
        str(SOURCE_ORIGIN_INVENTORY): SOURCE_ORIGIN_INVENTORY_SHA256,
    }
    for source, expected_hash in required_closure.items():
        require(closure.get(source) == expected_hash,
                'source audit closure omits or misbinds a final checkpoint file: ' + source)
    return {'status': 'ready_for_separate_copy_packaging', 'growth_directory': str(growth_dir),
            'source_audit_path': str(audit_path), 'source_audit_sha256': sha(audit_path),
            'outgoing_nonempty_roots': len(active), 'ordered_edges': sum(map(len, (r['ordered_target_pids'] for r in roots))),
            'goal_metric': goal_metric, 'qualifying_roots': qualifying,
            'ytmusic_source_backed_roots': expected_origin['counts']['ytmusic_source_backed'],
            'native_only_roots': expected_origin['counts']['native_only'],
            'native_ytmusic_overlap_roots': expected_origin['counts']['native_ytmusic_overlap'],
            'unknown_root_origins': expected_origin['counts']['unknown'],
            'indexed_assignments': len(state.get('rows', [])),
            'accepted_graphs_sha256': sha(graph_path), 'accepted_evaluation_sha256': sha(evaluation_path),
            'state_file_sha256': sha(state_path), 'merge_audit_sha256': sha(merge_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--growth-dir', type=Path, help='frozen final accepted growth checkpoint')
    parser.add_argument('--source-audit', type=Path, help='independent whole-source replay audit JSON')
    parser.add_argument('--metric', choices=('ytmusic-backed', 'total-genuine'),
                        default='ytmusic-backed',
                        help='root threshold semantics; defaults to strict YTMusic-backed roots')
    parser.add_argument('--baseline-only', action='store_true', help='check the pinned v13 native baseline prerequisites')
    args = parser.parse_args()
    if not args.baseline_only and (args.growth_dir is None or args.source_audit is None):
        parser.error('final gate requires --growth-dir and --source-audit (or use --baseline-only)')
    result = check_native()
    if not args.baseline_only:
        result.update(check_final(args.growth_dir, args.source_audit, args.metric))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
