"""Independently replay a final YT Music campaign under the pinned typed-union runtime."""
import argparse
import copy
import hashlib
import inspect
import importlib.util
import json
import os
import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v04')
RUNTIME_MANIFEST = RUNTIME / 'runtime-manifest.json'
RUNTIME_MANIFEST_SHA256 = 'e001a34d8eddb7e702880aca28d665ccd99cf1fb954761956a7fcbf1c79ae46d'
UNION_ADAPTER = RUNTIME / 'scripts/project_radio_source_union_v04.py'
HISTORICAL_NATIVE_RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v06')
HISTORICAL_NATIVE_MANIFEST_SHA256 = '2310e0eb13b21da42baebf5f806f71bda37490a3c3f9b228b901bc420acba5f5'
EXPECTED_PROGRESS_DIR = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261013-v13-radio-search-fusion-live'
EXPECTED_MAP = Path('/tmp/opengenius-album-alias-append-v03/03-cafe-stella-map.json')
EXPECTED_SNAPSHOT = ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
EXPECTED_NATIVE = Path('/tmp/opengenius-final-campaign-native-v13-child-v04')
EXPECTED_PROJECTION = Path('/tmp/opengenius-native-alias-projection-v06-v01')
EXPECTED_SOURCE_UNION = Path('/tmp/opengenius-radio-source-union-v04-candidate')
PILOT_PROGRESS = ROOT / 'data/ytmusic/retrieval-query-pilot-20261013-v01/progress.json'
PILOT_PROGRESS_SHA256 = '1eea8551bdf70703c556b2273f36f921c2e99e4640f1ef69d33b41ff5c3ce5bf'
PILOT_PROFILE = PILOT_PROGRESS.parent / 'retrieval-profile.json'
PILOT_PROFILE_SHA256 = '56b67fd28d717d22e73db5396cc607b7cd83ad2bdee6de5fe347bf219191a0bb'
SOURCE_ORIGIN_INVENTORY = ROOT / 'data/ytmusic/root-source-origin-inventory-20261013-v01.json'
SOURCE_ORIGIN_INVENTORY_SHA256 = 'ecc9743c246949b5ddb3eca59491aacf4c6d9fa970ac7970058d75ad24523514'
CONTRACT_GROWTH = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261012-v09-radio-only-scaled-acquisition/live/batch-0011/growth'
EXPECTED = {
    'library': '1ea4b3a0ee26495ddb5041d2c6683ec85327dfd6aca602a089b884be22c5c5b7',
    'snapshot': '53a232dc9818e1039e016858b3c5c85a54e6fe4be899c94fa3cdd8ee44d7587f',
    'map': '195c9646dc1f579708ebb19b263c2f5c9828bb3e9542e6b6179253aae7fd0d67',
    'native_state_file': '0ff818736f942e4e849487dd486fdf7a4b39c993c32369a889c60f6e3e383b95',
    'native_state_seal': '188e671a4d3d6743fd33d025a1684c831d1ddd9f87d72bae08250340eee6bd89',
    'native_graph': '8d14cf54cc75acb17b7fc15757691b2c7b4528d539dfdf86960475d6068207e3',
    'native_report': 'bdfc0d0789119e58543ff1d2691920e96e7a7ff98b6c00fcfbc06997254578dc',
    'native_receipt': '1bd5c2193d7a9a672704171be87e9b93012abab638ac3f520734f8160aee1809',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def _source_origin_inventory():
    require(SOURCE_ORIGIN_INVENTORY.is_file()
            and sha(SOURCE_ORIGIN_INVENTORY) == SOURCE_ORIGIN_INVENTORY_SHA256,
            'known source-origin inventory is missing or changed')
    inventory = load(SOURCE_ORIGIN_INVENTORY)
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
    sets = inventory.get('source_pid_sets')
    require(isinstance(sets, dict), 'known source-origin inventory lacks exact PID sets')
    native = set(sets.get('original_native_baseline_nonempty_root_pids', []))
    known_yt = set(sets.get('current_732_ytmusic_source_backed_root_pids', []))
    current_unknown = set(sets.get('current_732_unknown_baseline_root_pids', []))
    require(len(native) == 159 and len(known_yt) == 573 and not native & known_yt
            and not current_unknown,
            'known historical Native/YT source-origin PID sets are inconsistent')
    return inventory, receipts, native, known_yt


def _verify_historical_pilot():
    """Pin the no-root 16-seed retrieval pilot as history, never as root credit."""
    require(PILOT_PROGRESS.is_file() and sha(PILOT_PROGRESS) == PILOT_PROGRESS_SHA256,
            'authoritative retrieval-pilot progress changed')
    require(PILOT_PROFILE.is_file() and sha(PILOT_PROFILE) == PILOT_PROFILE_SHA256,
            'authoritative retrieval-pilot profile changed')
    progress = load(PILOT_PROGRESS)
    profile = load(PILOT_PROFILE)
    require(progress.get('schema_version') == 1
            and progress.get('status') == 'paused_at_checkpoint'
            and progress.get('http_requests') == 3935
            and isinstance(progress.get('attempted_pids'), list)
            and len(progress['attempted_pids']) == 1823
            and len(set(progress['attempted_pids'])) == 1823
            and progress.get('successful_outgoing_roots') == 733,
            'authoritative retrieval-pilot checkpoint counters or status changed')
    require(profile.get('producer') == 'opengenius-retrieval-query-pilot-profile-v1'
            and profile.get('profile_id') == 'retrieval-pilot-20261013-v01'
            and profile.get('schema_version') == 1
            and profile.get('base_progress_sha256') ==
            'c523add8ee459cd8ad84efc7bd68b7d556b30c6cc7600fceb5ea6703625e45c8'
            and profile.get('base_accepted_graph_sha256') ==
            '937451668dda15b4ff20bc2e03a504c538e89c3c82cf2e617153a059e90c75fe'
            and profile.get('source_library_sha256') == EXPECTED['library']
            and profile.get('identity_map_sha256') == EXPECTED['map']
            and profile.get('batch_number') == 2
            and profile.get('http_request_cap') == 80,
            'retrieval-pilot profile no longer binds the accepted base and bounded experiment')
    batches = progress.get('batches')
    require(isinstance(batches, list) and len(batches) == 2,
            'retrieval-pilot checkpoint does not preserve both completed batches')
    expected_requests = (85, 18)
    union_attempts = set()
    for index, batch in enumerate(batches):
        require(batch.get('status') == 'completed'
                and batch.get('source_policy') == 'authenticated-watch-radio-only-v1'
                and type(batch.get('http_requests')) is int
                and batch['http_requests'] == expected_requests[index],
                'retrieval-pilot batch status, source policy, or request count changed')
        seeds = batch.get('seed_pids')
        attempted = batch.get('attempted_pids')
        expected_count = 80 if index == 0 else 16
        require(isinstance(seeds, list) and len(seeds) == expected_count
                and len(set(seeds)) == expected_count
                and isinstance(attempted, list) and set(attempted) == set(seeds),
                'retrieval-pilot batch seed and attempt receipts do not match')
        require(not union_attempts.intersection(seeds),
                'retrieval-pilot batches repeat attempted PIDs')
        union_attempts.update(seeds)
        require(batch.get('growth_receipt') == batches[0].get('growth_receipt'),
                'no-root pilot batch changed its parent accepted-growth graph')
    pilot_seeds = profile.get('seeds')
    require(isinstance(pilot_seeds, list) and len(pilot_seeds) == 16
            and batches[1]['seed_pids'] == [row.get('persistent_id') for row in pilot_seeds],
            'pilot batch PID order differs from its pinned 8+8 retrieval profile')
    arms = [row.get('query_arm') for row in pilot_seeds]
    require(arms.count('album_aware') == 8 and arms.count('baseline_control') == 8,
            'retrieval-pilot profile is not the exact eight/eight comparison')
    summary_receipt = batches[1].get('collector_summary')
    require(isinstance(summary_receipt, dict)
            and set(summary_receipt) == {'path', 'sha256', 'http_requests', 'request_budget'}
            and summary_receipt.get('http_requests') == 18
            and summary_receipt.get('request_budget') == 80,
            'pilot batch has no exact collector summary receipt')
    summary_path = Path(summary_receipt['path']).resolve()
    require(summary_path.is_file() and sha(summary_path) == summary_receipt['sha256'],
            'pilot batch collector summary changed')
    summary = load(summary_path)
    results = summary.get('results')
    profile_by_pid = {row.get('persistent_id'): row for row in pilot_seeds}
    search_receipts_match = isinstance(results, list) and all(
        isinstance(row.get('search_request_evidence'), dict)
        and row['search_request_evidence'].get('arguments', {}).get('query')
            == profile_by_pid.get(row.get('persistent_id'), {}).get('query')
        and row['search_request_evidence'].get('arguments', {}).get('filter') == 'songs'
        and row['search_request_evidence'].get('arguments', {}).get('limit') == 5
        and row['search_request_evidence'].get('cache_policy') == 'fresh-append-only-namespace-v1'
        and row['search_request_evidence'].get('cache_hit') is False
        for row in results if isinstance(row, dict))
    require(summary.get('account_authentication_verified') is True
            and summary.get('status') == 'completed'
            and summary.get('http_requests') == 18
            and isinstance(results, list) and len(results) == 16
            and [row.get('persistent_id') for row in results] == batches[1]['seed_pids']
            and search_receipts_match
            and all(row.get('status') == 'search_unresolved'
                    and type(row.get('candidate_video_ids')) is int
                    and row['candidate_video_ids'] >= 0
                    and row.get('title_fallback_attempted') is False
                    for row in results)
            and not batches[1].get('snapshots') and not batches[1].get('snapshot_inputs'),
            'pilot does not remain an authenticated, zero-root diagnostic')
    return {'path': str(PILOT_PROGRESS), 'sha256': PILOT_PROGRESS_SHA256,
            'profile_path': str(PILOT_PROFILE), 'profile_sha256': PILOT_PROFILE_SHA256,
            'batches': 2, 'http_requests': 18, 'attempted_pid_count': 16,
            'roots_credited': 0, 'collector_summary_sha256': summary_receipt['sha256']}


def _verified_run_source_pids(normalized_runs, final_active_pids):
    """Derive roots from replay-normalized authenticated source runs, never counters."""
    sourced = set()
    verified_run_count = 0
    source_union_seen = False
    for index, run in enumerate(normalized_runs):
        if run.get('producer') == 'opengenius-radio-typed-source-union-run-v1':
            require(not source_union_seen and index == 1,
                    'typed source-union origin must be the single second replay-normalized run')
            require(run.get('source_policy') == 'authenticated-watch-radio-only-v1',
                    'typed source union lacks authenticated radio policy')
            manifest = load(RUNTIME_MANIFEST)
            manifest_modules = {row['path']: row['sha256'] for row in manifest['modules']}
            helper_path = UNION_ADAPTER.resolve()
            relative_helper = helper_path.relative_to(RUNTIME.resolve()).as_posix()
            require(relative_helper == 'scripts/project_radio_source_union_v04.py'
                    and manifest_modules.get(relative_helper) == sha(helper_path),
                    'typed source-origin consumer is outside the pinned runtime manifest')
            spec = importlib.util.spec_from_file_location(
                '_audit_trusted_project_radio_source_union_v04', helper_path)
            require(spec is not None and spec.loader is not None,
                    'typed source-origin consumer cannot be loaded from its pinned path')
            adapter = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(adapter)
            require(Path(adapter.__file__).resolve() == helper_path,
                    'typed source-origin consumer resolved outside the pinned runtime')
            # The frozen whole-chain dispatcher already rebuilt and source-verified
            # this transition inside the shared proof-cache scope. Bind the
            # normalized run to the pinned source documents without launching a
            # second 1,650-input source-proof walk.
            adapter._validate_saved_union_run_contract(run)
            typed_rows = run.get('typed_source_origin_graphs')
            require(isinstance(typed_rows, list),
                    'typed source union omits its source-origin graph rows')
            typed_pids = _typed_active_root_pids(typed_rows, final_active_pids)
            require(len(typed_pids) == 609,
                    'typed source-origin PIDs do not match the exact active v04 source set')
            sourced.update(typed_pids)
            source_union_seen = True
        graph_rows = run.get('new_graphs', [])
        if not graph_rows:
            # Unresolved or empty-but-verified collection batches earn no root
            # credit. The typed projection also has no source graphs and must
            # remain a zero-call transition.
            if run.get('producer') == 'opengenius-radio-native-alias-projection-run-v1':
                require(run.get('http_requests') == 0 and run.get('attempted_pid_delta') == 0,
                        'typed Native alias projection claims collection activity')
            else:
                require(run.get('source_policy') == 'authenticated-watch-radio-only-v1',
                        f'empty final growth run {index} lacks authenticated radio source policy')
            continue
        require(run.get('source_policy') == 'authenticated-watch-radio-only-v1',
                f'final v13 growth run {index} lacks authenticated radio source policy')
        snapshot_rows = run.get('snapshot_inputs')
        require(isinstance(snapshot_rows, list) and snapshot_rows,
                f'final v13 growth run {index} has no replayed snapshot receipts')
        snapshot_pids = set()
        for receipt in snapshot_rows:
            require(isinstance(receipt, dict) and set(receipt) == {'path', 'sha256'},
                    'replayed source snapshot receipt is malformed')
            path = Path(receipt['path']).resolve()
            require(path.is_file() and sha(path) == receipt['sha256'],
                    'replayed source snapshot changed: ' + str(path))
            snapshot = load(path)
            seed = snapshot.get('local_seed')
            pid = seed.get('persistent_id') if isinstance(seed, dict) else None
            require(isinstance(pid, str) and pid, 'replayed source snapshot has no exact seed PID')
            require(snapshot.get('account_authentication_verified') is True,
                    'replayed final source snapshot is not authenticated')
            require(pid not in snapshot_pids, 'replayed source run repeats a snapshot PID')
            snapshot_pids.add(pid)
        run_roots = set()
        for row in graph_rows:
            require(isinstance(row, dict) and isinstance(row.get('root_pid'), str)
                    and isinstance(row.get('ordered_target_pids'), list),
                    'replayed source graph row is malformed')
            if row['ordered_target_pids']:
                run_roots.add(row['root_pid'])
        require(run_roots <= snapshot_pids,
                f'final source run {index} has a nonempty root without a same-PID authenticated snapshot')
        sourced.update(run_roots & final_active_pids)
        verified_run_count += 1
    return sourced, verified_run_count


def _typed_active_root_pids(rows, final_active_pids):
    """Count only unique typed source rows with nonempty ordered relations."""
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
    require(active <= final_active_pids,
            'typed source-origin PIDs are not active in the final accepted graph')
    return active


def _classify_final_source_roots(final_active_pids, native_pids, known_yt_pids, normalized_runs):
    run_yt_pids, run_count = _verified_run_source_pids(normalized_runs, final_active_pids)
    yt_pids = (known_yt_pids | run_yt_pids) & final_active_pids
    native_present = native_pids & final_active_pids
    overlap = native_present & yt_pids
    native_only = native_present - yt_pids
    unknown = final_active_pids - (native_only | yt_pids)
    require(not unknown,
            'final accepted graph contains active roots with no Native or replay-verified YT source origin')
    return {
        'native_only_pids': sorted(native_only),
        'ytmusic_source_backed_pids': sorted(yt_pids),
        'native_ytmusic_overlap_pids': sorted(overlap),
        'unknown_pids': sorted(unknown),
        'verified_final_growth_run_count': run_count,
        'counts': {'native_only': len(native_only), 'ytmusic_source_backed': len(yt_pids),
                   'native_ytmusic_overlap': len(overlap), 'unknown': len(unknown),
                   'total_active': len(final_active_pids)},
    }


def _goal_root_count(source_origin, goal_metric):
    require(goal_metric in ('ytmusic-backed', 'total-genuine'),
            'unsupported root-count goal metric')
    counts = source_origin['counts']
    require(counts['unknown'] == 0
            and counts['native_only'] + counts['ytmusic_source_backed'] == counts['total_active'],
            'source-origin PID sets do not form an exact final-root partition')
    return (counts['ytmusic_source_backed'] if goal_metric == 'ytmusic-backed'
            else counts['total_active'])


def _require_goal(source_origin, goal_metric):
    qualifying_count = _goal_root_count(source_origin, goal_metric)
    require(qualifying_count >= 1000,
            f'final checkpoint has fewer than 1000 roots for requested {goal_metric} metric: '
            f'{qualifying_count}')
    return qualifying_count


def verify_runtime():
    require(sha(RUNTIME_MANIFEST) == RUNTIME_MANIFEST_SHA256,
            'frozen runtime manifest hash differs from its approved pin')
    manifest = load(RUNTIME_MANIFEST)
    require(manifest.get('producer') == 'opengenius-final-campaign-runtime-v11-source-union-v04'
            and manifest.get('code_root') == str(RUNTIME), 'unexpected frozen runtime identity')
    modules = manifest.get('modules')
    require(isinstance(modules, list) and len(modules) == 174,
            'frozen runtime manifest does not enumerate the approved 174 modules')
    seen = set()
    for row in modules:
        require(isinstance(row, dict) and set(row) == {'path', 'sha256'},
                'frozen runtime module receipt is malformed')
        relative = Path(row['path'])
        require(not relative.is_absolute() and '..' not in relative.parts,
                'frozen runtime module path escapes its root')
        path = RUNTIME / relative
        require(path.is_file() and not path.is_symlink() and sha(path) == row['sha256'],
                'a frozen runtime module changed: ' + str(path))
        require(relative.as_posix() not in seen, 'runtime manifest repeats a module')
        seen.add(relative.as_posix())
    require(manifest.get('trusted_data_root') == str(ROOT),
            'frozen runtime names a different trusted data root')
    return manifest


def verify_contracts(manifest):
    install_frozen_imports()
    import expand_genuine_roots as trusted_expand
    import merge_rebased_radio_growth as merge_growth
    import genuine_root_recovery as recovery
    from plan_genius_update import plan_genius_update
    from rebind_native_baseline_alias_chain import verify_native_alias_child_report
    from radio_snapshot_proof import radio_snapshot_proof_cache_scope, radio_snapshot_proof_validation_pass
    from seed_video_evidence import seed_video_selector_cache_scope

    modules = {row['path']: row['sha256'] for row in manifest['modules']}
    loaded_modules = (trusted_expand, merge_growth, recovery, plan_genius_update.__module__,
                      verify_native_alias_child_report.__module__,
                      radio_snapshot_proof_cache_scope.__module__,
                      radio_snapshot_proof_validation_pass.__module__,
                      seed_video_selector_cache_scope.__module__)
    for module in loaded_modules:
        module = module if not isinstance(module, str) else sys.modules[module]
        path = Path(module.__file__).resolve()
        relative = path.relative_to(RUNTIME).as_posix()
        require(modules.get(relative) == sha(path),
                'contract module is not the exact frozen manifest file: ' + relative)

    expected_parameters = {
        trusted_expand._validate_prior_attempt_ledger: ('path', 'library_hash', 'track_snapshot_sha256'),
        trusted_expand._validate_growth_receipts: ('growth_dir',),
        trusted_expand._verify_progress_receipts: ('progress', 'library_hash', 'identity_hash'),
        merge_growth._verify_rebase_receipt: ('graph_path', 'report_path', 'state_path', 'library_hash', 'identity_hash'),
        merge_growth._replay_previous_growth: ('previous_dir', 'baseline_state', 'baseline_roots',
                                               'baseline_provenance', 'matcher', 'library_hash', 'identity_hash'),
        recovery.replay_growth_checkpoint: ('previous_growth', 'track_snapshot', 'identity_map_path',
                                            'baseline_state_path', 'baseline_graph_export_path', 'rebase_report_path'),
    }
    for function, expected in expected_parameters.items():
        actual = tuple(inspect.signature(function).parameters)
        require(actual[:len(expected)] == expected,
                'frozen verifier call signature changed: ' + function.__module__ + '.' + function.__name__)
    def returned_dict_keys(function):
        tree = ast.parse(Path(function.__code__.co_filename).read_text(encoding='utf-8'))
        node = next(child for child in tree.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == function.__name__)
        keys = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and isinstance(child.value, ast.Dict):
                keys.update(key.value for key in child.value.keys
                            if isinstance(key, ast.Constant) and isinstance(key.value, str))
        return keys

    ledger_return = {'attempted_pids', 'same_map_attempted_pids', 'http_requests',
                     'same_map_attempted_pids_by_identity_map',
                     'diagnostic_attempted_pids', 'diagnostic_http_requests', 'outgoing_roots'}
    require(returned_dict_keys(trusted_expand._validate_prior_attempt_ledger) == ledger_return,
            'frozen prior-attempt ledger return fields changed')
    replay_ast = ast.parse(Path(recovery.__file__).read_text(encoding='utf-8'))
    replay_node = next(node for node in replay_ast.body
                       if isinstance(node, ast.FunctionDef) and node.name == 'replay_growth_checkpoint')
    observed_replay_keys = set()
    for node in ast.walk(replay_node):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            observed_replay_keys.update(key.value for key in node.value.keys
                                        if isinstance(key, ast.Constant) and isinstance(key.value, str))
    require({'state_sha256', 'roots', 'accepted_graphs_sha256', 'outgoing_root_count'} <= observed_replay_keys,
            'frozen checkpoint replay return contract changed')
    dispatcher_source = Path(merge_growth.__file__).read_text(encoding='utf-8')
    dispatcher_constants = merge_growth._replay_previous_growth.__code__.co_consts
    require('opengenius-radio-native-alias-projection-checkpoint-v1' in dispatcher_constants,
            'frozen growth dispatcher lacks NativeAliasProjection replay support')
    require('opengenius-radio-typed-source-union-run-v1' in dispatcher_source
            and 'project_radio_source_union_v04.py' in dispatcher_source,
            'frozen growth dispatcher lacks the pinned typed source-union branch')
    require('radio-only-search-album-fusion-v1'
            in recovery.runner_config_sha256.__code__.co_consts,
            'frozen recovery validator lacks fused collection mode support')
    require(CONTRACT_GROWTH.is_dir(), 'known 732-root growth contract fixture is missing')
    graph, evaluation, state, merge = map(load, (
        CONTRACT_GROWTH / 'accepted-graphs.json', CONTRACT_GROWTH / 'accepted-evaluation.json',
        CONTRACT_GROWTH / 'state.json', CONTRACT_GROWTH / 'merge-audit.json'))
    roots = graph.get('accepted_roots')
    require(graph.get('producer') == 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1'
            and isinstance(roots, list) and evaluation.get('relation_rows') == roots,
            'known normal-growth checkpoint graph/evaluation export contract changed')
    require(state.get('state_sha256') == evaluation.get('virtual_state_sha256')
            and state.get('state_sha256') == merge.get('next_state_sha256')
            and merge.get('output_active_outgoing_root_count') == 732,
            'known normal-growth checkpoint state/merge export contract changed')
    require(sum(bool(row.get('ordered_target_pids')) for row in roots) == 732,
            'known growth format fixture no longer has the expected 732 roots')
    native_receipt = load(EXPECTED_NATIVE / 'native-baseline-alias-child-receipt.json')
    historical_manifest_path = HISTORICAL_NATIVE_RUNTIME / 'runtime-manifest.json'
    require(sha(historical_manifest_path) == HISTORICAL_NATIVE_MANIFEST_SHA256,
            'historical Native v06 runtime manifest differs from its fixed pin')
    historical_manifest = load(historical_manifest_path)
    require(historical_manifest.get('producer') == 'opengenius-final-campaign-runtime-v06'
            and historical_manifest.get('code_root') == str(HISTORICAL_NATIVE_RUNTIME)
            and historical_manifest.get('trusted_data_root') == str(ROOT),
            'historical Native v06 runtime identity differs')
    historical_modules = historical_manifest.get('modules')
    require(isinstance(historical_modules, list) and len(historical_modules) == 171,
            'historical Native v06 manifest does not enumerate the approved module closure')
    historical_hashes = {}
    for row in historical_modules:
        require(isinstance(row, dict) and set(row) == {'path', 'sha256'},
                'historical Native v06 module receipt is malformed')
        relative = Path(row['path'])
        require(not relative.is_absolute() and '..' not in relative.parts
                and relative.as_posix() not in historical_hashes,
                'historical Native v06 module path is unsafe or duplicated')
        path = HISTORICAL_NATIVE_RUNTIME / relative
        require(path.is_file() and not path.is_symlink() and sha(path) == row['sha256'],
                'historical Native v06 module changed: ' + str(path))
        historical_hashes[relative.as_posix()] = row['sha256']
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
    code_inputs = native_receipt.get('code_inputs')
    require(isinstance(code_inputs, list) and len(code_inputs) == 9,
            'Native child receipt does not contain its exact nine-code closure')
    seen_native_code = set()
    for code_input in code_inputs:
        require(isinstance(code_input, dict) and set(code_input) == {'path', 'sha256'},
                'Native child code receipt is malformed')
        path = Path(code_input['path']).resolve()
        try:
            relative = path.relative_to(HISTORICAL_NATIVE_RUNTIME).as_posix()
        except ValueError:
            raise ValueError('Native child code receipt escapes its historical v06 root') from None
        require(relative in expected_native_code_paths and relative not in seen_native_code
                and historical_hashes.get(relative) == code_input['sha256'] == sha(path),
                'Native child code receipt differs from its exact historical v06 closure: ' + relative)
        seen_native_code.add(relative)
    require(seen_native_code == expected_native_code_paths,
            'Native child code receipt omits a fixed historical v06 module')
    return {'contract_status': 'passed', 'trusted_api_count': len(expected_parameters),
            'runtime_module_count': len(modules), 'normal_growth_fixture_roots': 732,
            'native_code_receipts': len(code_inputs),
            'historical_native_runtime_manifest_sha256': HISTORICAL_NATIVE_MANIFEST_SHA256,
            'projection_dispatch': 'present', 'typed_source_union_dispatch': 'present',
            'fusion_mode_dispatch': 'present'}


def install_frozen_imports():
    # Keep the campaign producer frozen and prevent Python from writing pyc files there.
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['OPENGENIUS_TRUSTED_DATA_ROOT'] = str(ROOT)
    frozen_scripts = str(RUNTIME / 'scripts')
    public_scripts = str(ROOT / 'scripts')
    sys.path[:] = [p for p in sys.path if str(Path(p or '.').resolve()) != public_scripts]
    sys.path.insert(0, frozen_scripts)


def _config_file(config, name, expected_path=None, expected_sha=None):
    row = config.get(name)
    require(isinstance(row, dict) and set(row) == {'path', 'sha256'},
            'progress config lacks exact ' + name + ' path/hash receipt')
    path = Path(row['path']).resolve()
    require(path.is_relative_to(ROOT.resolve()) or path.is_relative_to(Path('/tmp').resolve()),
            name + ' source path is outside approved data roots')
    require(path.is_file() and sha(path) == row['sha256'], name + ' source receipt changed')
    if expected_path is not None:
        require(path == Path(expected_path).resolve(), name + ' points to an unexpected source path')
    if expected_sha is not None:
        require(row['sha256'] == expected_sha, name + ' hash differs from the reviewed v13 source')
    return path


def _receipt_closure(roots):
    """Collect and hash-check file receipts reachable from validated JSON roots."""
    receipts = {}
    visited_json = set()
    queue = [Path(path).resolve() for path in roots]

    def approved_manifest_code_root(manifest_path, document):
        manifest_path = Path(manifest_path).resolve()
        if (manifest_path == RUNTIME_MANIFEST.resolve()
                and sha(manifest_path) == RUNTIME_MANIFEST_SHA256
                and document.get('producer') == 'opengenius-final-campaign-runtime-v11-source-union-v04'
                and document.get('code_root') == str(RUNTIME.resolve())):
            return RUNTIME.resolve()
        historical_path = HISTORICAL_NATIVE_RUNTIME / 'runtime-manifest.json'
        if (manifest_path == historical_path.resolve()
                and sha(manifest_path) == HISTORICAL_NATIVE_MANIFEST_SHA256
                and document.get('producer') == 'opengenius-final-campaign-runtime-v06'
                and document.get('code_root') == str(HISTORICAL_NATIVE_RUNTIME.resolve())):
            return HISTORICAL_NATIVE_RUNTIME.resolve()
        return None

    while queue:
        path = queue.pop()
        if path in visited_json or not path.is_file():
            continue
        visited_json.add(path)
        try:
            document = json.loads(path.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue

        def walk(value):
            if isinstance(value, dict):
                if (set(value) == {'path', 'sha256'} and isinstance(value['path'], str)
                        and isinstance(value['sha256'], str)):
                    raw_source = Path(value['path'])
                    if not raw_source.is_absolute():
                        require('..' not in raw_source.parts,
                                'relative source receipt contains parent traversal')
                        receipt_base = approved_manifest_code_root(path, document) or ROOT.resolve()
                        raw_source = receipt_base / raw_source
                    source = raw_source.resolve()
                    allowed_roots = (ROOT.resolve(), Path('/tmp').resolve(),
                                     Path('/mnt/data/mizoyuzu/Music.app').resolve())
                    require(any(source == allowed or source.is_relative_to(allowed)
                                for allowed in allowed_roots),
                            'source receipt resolves outside approved data roots: ' + str(source))
                    require(source.is_file() and sha(source) == value['sha256'],
                            'source closure receipt changed: ' + str(source))
                    receipts[str(source)] = value['sha256']
                    if source.suffix.lower() == '.json':
                        queue.append(source)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(document)
    receipts.update({str(path): sha(path) for path in sorted(visited_json)})
    return receipts


def _replay_trusted_sources(*, trusted_expand, merge_growth,
                            identity_map, plan_genius_update,
                            load_library, progress, snapshot_path,
                            map_path, ledger_path, projection_dir,
                            baseline_state_path, baseline_graph_path,
                            rebase_report_path):
    # Use the same shared invocation scopes as the approved runner. Without
    # these scopes, ledger validation, progress validation, and typed replay
    # would independently repeat the expensive radio-source proof closure.
    from radio_snapshot_proof import (radio_snapshot_proof_cache_scope,
                                     radio_snapshot_proof_validation_pass)
    from seed_video_evidence import seed_video_selector_cache_scope

    with radio_snapshot_proof_cache_scope(), seed_video_selector_cache_scope(), \
            radio_snapshot_proof_validation_pass():
        tracks, library_hash, _ = load_library(track_snapshot=snapshot_path)
        require(len(tracks) == 3004 and library_hash == EXPECTED['library'],
                'snapshot does not describe the pinned 3004-track Library')
        identity_hash = sha(map_path)
        prior = trusted_expand._validate_prior_attempt_ledger(
            ledger_path, library_hash, sha(snapshot_path), authorized_rebound_retry_pids=set(),
            current_identity_map_sha256=identity_hash)
        require(prior['outgoing_roots'] == 732,
                'v09b1-10 diagnostic ledger does not resolve to the expected 732-root base')
        trusted_expand._validate_growth_receipts(projection_dir)
        require(progress.get('batches'), 'final campaign has no fused collection batches')
        for batch in progress['batches']:
            require(batch.get('source_policy') == progress['config']['source_policy'],
                    'a final campaign batch has the wrong source policy')
        verified_progress = copy.deepcopy(progress)
        release_pids = set(progress['config'].get('authorized_retry_release_pids', []))
        require(not release_pids, 'v13 source transition must not release prior same-map attempts')
        trusted_expand._verify_progress_receipts(
            verified_progress, library_hash, identity_hash,
            prior['attempted_pids'] - release_pids, prior['http_requests'])
        require(verified_progress['attempted_pids'] == progress['attempted_pids'],
                'progress attempted PIDs differ after trusted receipt replay')
        final_batch = progress['batches'][-1]
        final_dir = Path(final_batch.get('growth_output', '')).resolve()
        growth_receipt = final_batch.get('growth_receipt')
        require(final_dir.is_dir() and isinstance(growth_receipt, dict)
                and set(growth_receipt) == {'path', 'sha256'},
                'final progress does not identify its last accepted growth checkpoint')
        require(Path(growth_receipt['path']).resolve() == final_dir / 'accepted-graphs.json'
                and sha(final_dir / 'accepted-graphs.json') == growth_receipt['sha256'],
                'last progress graph receipt does not bind the final checkpoint')
        matcher = identity_map(json.loads(map_path.read_bytes()), tracks, library_hash)
        baseline_graph, _, baseline_state, provenance = merge_growth._verify_rebase_receipt(
            baseline_graph_path, rebase_report_path, baseline_state_path,
            library_hash, identity_hash)
        rebuilt_baseline = plan_genius_update(
            baseline_state, baseline_graph['accepted_roots'], matcher,
            library_hash, identity_hash)['next_state']
        require(rebuilt_baseline['state_sha256'] == baseline_state['state_sha256'],
                'trusted Native baseline does not reproduce its typed state')
        state, roots, normalized_runs, accepted_hash = merge_growth._replay_previous_growth(
            final_dir, baseline_state, baseline_graph['accepted_roots'], provenance,
            matcher, library_hash, identity_hash)
        replay = {'state_sha256': state['state_sha256'], 'roots': roots,
                  'accepted_graphs_sha256': accepted_hash,
                  'outgoing_root_count': sum(bool(row['ordered_target_pids']) for row in roots),
                  'state_document': state, 'normalized_runs': normalized_runs}
    return tracks, library_hash, identity_hash, prior, final_dir, growth_receipt, replay


def audit(progress_path, audit_output, goal_metric='ytmusic-backed'):
    require(goal_metric in ('ytmusic-backed', 'total-genuine'),
            'unsupported root-count goal metric')
    inventory, inventory_receipts, native_origin_pids, known_yt_origin_pids = (
        _source_origin_inventory())
    pilot = _verify_historical_pilot()
    manifest = verify_runtime()
    verify_contracts(manifest)
    install_frozen_imports()
    import expand_genuine_roots as trusted_expand
    import merge_rebased_radio_growth as merge_growth
    from music_identity_map import IdentityMap, load_library
    from plan_genius_update import plan_genius_update
    from radio_snapshot_proof import radio_snapshot_proof_cache_scope
    from radio_snapshot_proof import radio_snapshot_proof_validation_pass
    from seed_video_evidence import seed_video_selector_cache_scope

    # Assert key trusted imports came from the frozen runtime, and not a
    # same-named module already visible from the public worktree.
    runtime_modules = {row['path']: row['sha256'] for row in manifest['modules']}
    for module in (trusted_expand, merge_growth, IdentityMap.__module__, load_library.__module__,
                   plan_genius_update.__module__,
                   radio_snapshot_proof_cache_scope.__module__, radio_snapshot_proof_validation_pass.__module__,
                   seed_video_selector_cache_scope.__module__):
        loaded = module if not isinstance(module, str) else sys.modules[module]
        source_path = Path(loaded.__file__).resolve()
        try:
            relative = source_path.relative_to(RUNTIME).as_posix()
        except ValueError:
            raise ValueError('trusted campaign module loaded outside the frozen runtime: ' + str(source_path))
        require(runtime_modules.get(relative) == sha(source_path),
                'loaded trusted module differs from its frozen manifest receipt: ' + relative)

    progress_path = Path(progress_path).resolve()
    audit_output = Path(audit_output).resolve()
    receipt_output = audit_output.with_name(audit_output.stem + '.receipt.json')
    require(not audit_output.exists() and not receipt_output.exists(),
            'source-audit outputs must be new; preserve prior receipts')
    protected = (EXPECTED_PROGRESS_DIR.resolve(), EXPECTED_PROJECTION.resolve(),
                 EXPECTED_NATIVE.resolve(), RUNTIME.resolve(),
                 (ROOT / 'data/new-library-rebased-20261009-v03').resolve(),
                 SOURCE_ORIGIN_INVENTORY.resolve())
    for source_root in protected:
        require(not (audit_output == source_root or audit_output.is_relative_to(source_root)
                     or source_root.is_relative_to(audit_output)),
                'source-audit output overlaps a frozen campaign or baseline input')
    inventory_doc = load(SOURCE_ORIGIN_INVENTORY)
    for source_path in inventory_doc.get('source_artifact_sha256', {}):
        source_path = Path(source_path).resolve()
        require(audit_output != source_path,
                'source-audit output would overwrite a historical source receipt')
    require(progress_path.parent == EXPECTED_PROGRESS_DIR.resolve(),
            'progress path is not in the designated v13 fused acquisition output')
    progress = load(progress_path)
    config = progress.get('config')
    require(isinstance(config, dict) and progress.get('schema_version') == 1,
            'final progress header is malformed')
    require(progress.get('status') in ('paused_at_checkpoint', 'complete'),
            'final progress is not a frozen completed checkpoint')
    require(config.get('source_policy') == 'authenticated-watch-radio-only-v1'
            and config.get('collector_collection_mode') == 'radio-only-search-album-fusion-v1',
            'final progress is not bound to the approved fused authenticated source mode')
    require(config.get('dynamic_current_radio_hints') is False
            and config.get('external_seed_video_evidence') is None,
            'unexpected unbound external or dynamic source evidence is present')

    snapshot_path = _config_file(config, 'track_snapshot', EXPECTED_SNAPSHOT, EXPECTED['snapshot'])
    map_path = _config_file(config, 'identity_map', EXPECTED_MAP, EXPECTED['map'])
    baseline_state_path = _config_file(
        config, 'baseline_state', EXPECTED_NATIVE / 'baseline-state.json', EXPECTED['native_state_file'])
    baseline_graph_path = _config_file(
        config, 'baseline_graph_export', EXPECTED_NATIVE / 'accepted-baseline-graphs.json', EXPECTED['native_graph'])
    rebase_report_path = _config_file(
        config, 'rebase_report', EXPECTED_NATIVE / 'baseline-rebase-report.json', EXPECTED['native_report'])
    ledger_path = _config_file(config, 'prior_attempt_ledger')
    previous = config.get('previous_growth')
    require(isinstance(previous, dict) and set(previous) == {'path', 'sha256'},
            'final progress lacks its zero-call projection parent receipt')
    projection_dir = Path(previous['path']).resolve()
    require(projection_dir in (EXPECTED_PROJECTION.resolve(), EXPECTED_SOURCE_UNION.resolve()),
            'final progress does not start from the designated typed Native/source-union checkpoint')
    projection_graph = projection_dir / 'accepted-graphs.json'
    require(projection_graph.is_file() and sha(projection_graph) == previous['sha256'],
            'typed previous-growth parent graph receipt changed')
    if projection_dir == EXPECTED_SOURCE_UNION.resolve():
        require(sha(RUNTIME_MANIFEST) == RUNTIME_MANIFEST_SHA256,
                'source-union parent does not use the approved v11 runtime manifest')
    require(sha(EXPECTED_NATIVE / 'native-baseline-alias-child-receipt.json') == EXPECTED['native_receipt'],
            'native alias child receipt changed')

    tracks, library_hash, identity_hash, prior, final_dir, growth_receipt, replay = (
        _replay_trusted_sources(
            trusted_expand=trusted_expand,
            merge_growth=merge_growth,
            identity_map=IdentityMap, plan_genius_update=plan_genius_update,
            load_library=load_library, progress=progress,
            snapshot_path=snapshot_path,
            map_path=map_path, ledger_path=ledger_path,
            projection_dir=projection_dir, baseline_state_path=baseline_state_path,
            baseline_graph_path=baseline_graph_path,
            rebase_report_path=rebase_report_path))
    graph_path = final_dir / 'accepted-graphs.json'
    evaluation_path = final_dir / 'accepted-evaluation.json'
    state_path = final_dir / 'state.json'
    merge_path = final_dir / 'merge-audit.json'
    graph, evaluation, state, merge = map(load, (graph_path, evaluation_path, state_path, merge_path))
    roots = graph.get('accepted_roots')
    require(isinstance(roots, list), 'replayed final graph has no root array')
    root_pids = [row.get('root_pid') for row in roots if row.get('ordered_target_pids')]
    require(len(root_pids) == len(set(root_pids)),
            'final checkpoint has duplicate nonempty outgoing root PIDs')
    source_origin = _classify_final_source_roots(
        set(root_pids), native_origin_pids, known_yt_origin_pids,
        replay['normalized_runs'])
    qualifying_count = _require_goal(source_origin, goal_metric)
    require(replay['outgoing_root_count'] == len(root_pids)
            == progress.get('successful_outgoing_roots'),
            'progress, accepted graph, and independent whole-chain replay root totals differ')
    require(replay['accepted_graphs_sha256'] == sha(graph_path),
            'trusted replay does not bind the final accepted graph bytes')
    require(replay['roots'] == roots and replay['state_document'] == state,
            'final graph/state documents differ from materialized trusted replay outputs')
    require(evaluation.get('relation_rows') == roots,
            'final evaluation ordered relation rows differ from accepted graph')
    require(evaluation.get('virtual_state_sha256') == state.get('state_sha256')
            and merge.get('next_state_sha256') == state.get('state_sha256')
            and replay.get('state_sha256') == state.get('state_sha256'),
            'final state seal differs between typed replay, evaluation, and merge audit')
    require(merge.get('output_active_outgoing_root_count') == len(root_pids),
            'merge audit root count differs from replayed graph')

    # The validated receipt graph includes v09 progress, its recursively validated
    # attempt ledger (v09b1-10 and older ledgers), all batch receipts, and the
    # projection/native transition inputs. Requiring every path/hash pair here
    # makes the audit artifact carry the actual input closure, not a claimed scope.
    source_roots = [progress_path, ledger_path, graph_path, evaluation_path,
                    state_path, merge_path, projection_graph,
                    EXPECTED_NATIVE / 'native-baseline-alias-child-receipt.json',
                    baseline_state_path, baseline_graph_path, rebase_report_path,
                    snapshot_path, map_path, RUNTIME_MANIFEST,
                    HISTORICAL_NATIVE_RUNTIME / 'runtime-manifest.json',
                    Path(__file__).resolve(),
                    SOURCE_ORIGIN_INVENTORY, PILOT_PROGRESS, PILOT_PROFILE]
    closure = _receipt_closure(source_roots)
    closure.update(inventory_receipts)
    runtime_modules = {row['path']: row['sha256'] for row in manifest['modules']}
    audit_doc = {
        'schema_version': 1,
        'producer': 'opengenius-independent-ytmusic-source-audit-v1',
        'status': 'passed',
        'runtime_manifest_sha256': RUNTIME_MANIFEST_SHA256,
        'historical_native_runtime_manifest_sha256': HISTORICAL_NATIVE_MANIFEST_SHA256,
        'trusted_runtime_module_count': len(runtime_modules),
        'trusted_runtime_modules_sha256': runtime_modules,
        'independent_full_chain_replay': {
            'checkpoint': str(final_dir),
            'status': 'passed',
            'outgoing_root_count': len(root_pids),
            'state_sha256': sha(state_path),
            'replayed_state_sha256': replay['state_sha256'],
            'accepted_graphs_sha256': sha(graph_path),
            'progress_growth_receipt_sha256': growth_receipt['sha256'],
            'graph_receipt_matches': True,
        },
        'source_root_origin': {
            'goal_metric': goal_metric,
            'qualifying_root_count': qualifying_count,
            'known_source_inventory_path': str(SOURCE_ORIGIN_INVENTORY),
            'known_source_inventory_sha256': SOURCE_ORIGIN_INVENTORY_SHA256,
            'known_source_inventory_scope': inventory['status'],
            'native_only_pids': source_origin['native_only_pids'],
            'ytmusic_source_backed_pids': source_origin['ytmusic_source_backed_pids'],
            'native_ytmusic_overlap_pids': source_origin['native_ytmusic_overlap_pids'],
            'unknown_pids': source_origin['unknown_pids'],
            'counts': source_origin['counts'],
            'verified_final_growth_run_count': source_origin['verified_final_growth_run_count'],
            'classification_basis': 'known historical source PID inventory with every historical input rehashed, unioned with same-PID authenticated snapshot roots from whole-chain replay and the exact v11 typed source-union origin PID vectors revalidated against the accepted rematch archive; final active PID set must be exactly classified',
        },
        'source_policy_audit': {
            'prior_attempt_ledger_outgoing_roots': prior['outgoing_roots'],
            'prior_attempt_ledger_http_requests': prior['http_requests'],
            'final_progress_http_requests': progress.get('http_requests'),
            'final_progress_attempted_pid_count': len(progress.get('attempted_pids', [])),
            'final_collector_collection_mode': config['collector_collection_mode'],
            'final_source_policy': config['source_policy'],
            'typed_previous_growth_parent_sha256': previous['sha256'],
            'typed_previous_growth_parent_path': str(projection_dir),
            'new_campaign_batches_verified': len(progress['batches']),
            'historical_retrieval_pilot': pilot,
            'typed_source_union_run_verified': any(
                run.get('producer') == 'opengenius-radio-typed-source-union-run-v1'
                for run in replay['normalized_runs']),
            'v09b1_10_history_verified_by_recursive_ledger': True,
            'historical_source_inventory_receipt_count': len(inventory_receipts),
            'historical_source_inventory_sha256': SOURCE_ORIGIN_INVENTORY_SHA256,
        },
        'final_files_sha256': {
            'accepted_graphs': sha(graph_path),
            'accepted_evaluation': sha(evaluation_path),
            'state': sha(state_path),
            'merge_audit': sha(merge_path),
            'progress': sha(progress_path),
        },
        'source_closure_sha256': closure,
    }
    audit_output.parent.mkdir(parents=True, exist_ok=True)
    audit_bytes = (json.dumps(audit_doc, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    with audit_output.open('xb') as stream:
        stream.write(audit_bytes)
    receipt = {
        'schema_version': 1,
        'producer': 'opengenius-independent-ytmusic-source-audit-receipt-v1',
        'source_audit_path': str(audit_output),
        'source_audit_sha256': hashlib.sha256(audit_bytes).hexdigest(),
        'checkpoint_path': str(final_dir),
        'accepted_graphs_sha256': sha(graph_path),
        'state_file_sha256': sha(state_path),
        'runtime_manifest_sha256': RUNTIME_MANIFEST_SHA256,
    }
    with receipt_output.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')
    return {'status': 'passed', 'outgoing_root_count': len(root_pids),
            'source_audit': str(audit_output), 'source_audit_sha256': sha(audit_output),
            'receipt': str(receipt_output), 'receipt_sha256': sha(receipt_output),
            'source_closure_file_count': len(closure), 'trusted_runtime_module_count': len(runtime_modules)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--progress', type=Path)
    parser.add_argument('--output-audit', type=Path)
    parser.add_argument('--metric', choices=('ytmusic-backed', 'total-genuine'),
                        default='ytmusic-backed',
                        help='root threshold semantics; defaults to strict YTMusic-backed roots')
    parser.add_argument('--verify-runtime-only', action='store_true')
    parser.add_argument('--verify-contract-only', action='store_true')
    args = parser.parse_args()
    if args.verify_runtime_only or args.verify_contract_only:
        manifest = verify_runtime()
        result = {'status': 'runtime_verified', 'module_count': len(manifest['modules']),
                  'runtime_manifest_sha256': RUNTIME_MANIFEST_SHA256}
        if args.verify_contract_only:
            result.update(verify_contracts(manifest))
        print(json.dumps(result, sort_keys=True))
        return
    if args.progress is None:
        parser.error('--progress is required for a full source replay')
    if args.output_audit is None:
        parser.error('--output-audit is required for a full source replay')
    print(json.dumps(audit(args.progress, args.output_audit, args.metric), sort_keys=True))


if __name__ == '__main__':
    main()
