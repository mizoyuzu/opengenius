"""Typed source-union checkpoint producer and consumer for frozen v10 inputs."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path


ROOT = Path('/mnt/data/mizoyuzu/Projects/personal/opengenius')
SOURCE_CHILD = Path('/tmp/opengenius-radio-source-map-rematch-v16-732-actual-child-v01')
SOURCE_RUN = Path('/tmp/opengenius-radio-source-map-rematch-v16-run-v01')
ARCHIVE = ROOT / 'data/ytmusic/typed-radio-source-rematch-v16-accepted-20261013-v01'
ARCHIVE_MANIFEST = ARCHIVE / 'archive-manifest.json'
V10 = Path('/tmp/opengenius-final-campaign-runtime-v10-native-index-compat-v01')
V10_MANIFEST = V10 / 'runtime-manifest.json'
CURRENT_GROWTH = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261013-v13-radio-search-fusion-live/batch-0001/growth'
TRACK_SNAPSHOT = ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
IDENTITY_MAP = Path('/tmp/opengenius-album-alias-append-v03/03-cafe-stella-map.json')
NATIVE_BASELINE = Path('/tmp/opengenius-final-campaign-native-v13-child-v04')
SOURCE_PARENT_732 = Path('/tmp/opengenius-real-parent-alias-v01/latest-batch11-v13-development-v03/v13-append-chain-child/accepted-baseline-graphs.json')
V11_UNION_RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v02')
ADAPTER_PATH = V11_UNION_RUNTIME / 'scripts/project_radio_source_union_v02.py'

ARCHIVE_MANIFEST_SHA256 = '23527427700e6d2b64f54bd7bedaa2eaa3f5bc2d348dafecf9910f847b46ca92'
V10_MANIFEST_SHA256 = 'bdb1df6a6f714e09f6a1b33f43c4f5c58e1dc5cc2b89d7d67e28d56e1b04da3f'
V10_MODULE_HASHES = {
    'merge_rebased_radio_growth.py': '453632e85a89e57ec57344e07971ab3f19cd9ba7496b8a20968d7d0356449678',
    'music_identity_map.py': 'f1befec3b36108e7904fa97aa9abf6adc43226707018ea07fa1f13e8be66f169',
    'plan_genius_update.py': 'ae67b48265d3a5bade390e45a3e29b9bedf5c65101cc4e9f838ecad323056848',
    'update_genius_bundle.py': 'd3d74efe1e64c7801ad8f2e70a60359f0a2847b16e9f63dfa8b87c73fbcea25b',
}
CURRENT_GROWTH_SHA256 = '937451668dda15b4ff20bc2e03a504c538e89c3c82cf2e617153a059e90c75fe'
SOURCE_PARENT_732_SHA256 = '1d7496bd50fc2770b46e807a5e86375be3bcfc05d104ee88cbebaba8c361ab25'
INPUT_HASHES = {
    str(TRACK_SNAPSHOT.resolve()): '53a232dc9818e1039e016858b3c5c85a54e6fe4be899c94fa3cdd8ee44d7587f',
    str(IDENTITY_MAP.resolve()): '195c9646dc1f579708ebb19b263c2f5c9828bb3e9542e6b6179253aae7fd0d67',
    str((NATIVE_BASELINE / 'baseline-state.json').resolve()): '0ff818736f942e4e849487dd486fdf7a4b39c993c32369a889c60f6e3e383b95',
    str((NATIVE_BASELINE / 'accepted-baseline-graphs.json').resolve()): '8d14cf54cc75acb17b7fc15757691b2c7b4528d539dfdf86960475d6068207e3',
    str((NATIVE_BASELINE / 'baseline-rebase-report.json').resolve()): 'bdfc0d0789119e58543ff1d2691920e96e7a7ff98b6c00fcfbc06997254578dc',
    str((NATIVE_BASELINE / 'native-baseline-alias-child-receipt.json').resolve()): '1bd5c2193d7a9a672704171be87e9b93012abab638ac3f520734f8160aee1809',
}
EXPECTED_SOURCE_HASHES = {
    'accepted-graphs.json': '682d53a1baac103b6fa3f200ee71f9d04aa2c6215c18b931e11ba3ee3f662948',
    'identity-map-rebinding-report.json': '2446b347e3ec96fdc536675087b5edb8e73e23caa9e05728dc4d484b51adcbea',
    'state.json': '182fabadd69120977ceaee9a767ef8a8840d98cb178eeae51d5633885c740db9',
}
SOURCE_PRODUCER = 'opengenius-typed-radio-source-map-rematch-growth-v1'
PROJECTION_PRODUCER = 'opengenius-radio-native-radio-source-rematch-projection-checkpoint-v1'
PROJECTION_RUN_PRODUCER = 'opengenius-radio-native-source-rematch-projection-run-v1'
SOURCE_POLICY = 'authenticated-watch-radio-only-v1'
_NATIVE_VERIFICATION_CACHE = {}
_ACTIVE_MERGER = None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    return path, raw, json.loads(raw)


def _receipt(path: Path) -> dict:
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': _sha(path.read_bytes())}


def _verify_adapter_receipt(receipt, runtime_root=V11_UNION_RUNTIME):
    runtime_root = Path(runtime_root).resolve()
    manifest = json.loads((runtime_root / 'runtime-manifest.json').read_bytes())
    relative = 'scripts/project_radio_source_union_v02.py'
    helper = (runtime_root / relative).resolve()
    module_hashes = {row.get('path'): row.get('sha256')
                     for row in manifest.get('modules', []) if isinstance(row, dict)}
    actual_hash = _sha(helper.read_bytes())
    if (module_hashes.get(relative) != actual_hash
            or receipt != {'path': str(helper), 'sha256': actual_hash}):
        raise ValueError('Typed source-union adapter receipt differs from the fixed runtime pin')
    return helper, actual_hash


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _rows_by_pid(state, label):
    rows = state.get('rows')
    if not isinstance(rows, list):
        raise ValueError(label + ' has no rows')
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('persistent_id'), str)
                or row['persistent_id'] in result):
            raise ValueError(label + ' has malformed or duplicate PID rows')
        result[row['persistent_id']] = row
    return result


def _relation_map(rows, label):
    if not isinstance(rows, list):
        raise ValueError(label + ' is not a row list')
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('root_pid'), str)
                or not isinstance(row.get('ordered_target_pids'), list)
                or row['root_pid'] in result
                or any(not isinstance(pid, str) for pid in row['ordered_target_pids'])
                or len(set(row['ordered_target_pids'])) != len(row['ordered_target_pids'])):
            raise ValueError(label + ' has malformed, duplicate, or repeated edge rows')
        result[row['root_pid']] = list(row['ordered_target_pids'])
    return result


def _validate_union_run_contract(run, source_rows, current_rows, native_rows,
                                 source_parent_rows, normalized_c10_delta,
                                 required_source_receipts):
    """Cheap strict shape/vector guard shared by production replay and tests."""
    if (not isinstance(run, dict)
            or run.get('producer') != 'opengenius-radio-typed-source-union-run-v1'
            or run.get('source_policy') != SOURCE_POLICY
            or type(run.get('http_requests')) is not int or run.get('http_requests') != 0
            or type(run.get('attempted_pid_delta')) is not int
            or run.get('attempted_pid_delta') != 0):
        raise ValueError('Typed source-union run has the wrong producer, policy, or zero-traffic boundary')
    if (run.get('collector_summary') != normalized_c10_delta.get('collector_summary')
            or run.get('seed_manifest') != normalized_c10_delta.get('seed_manifest')
            or run.get('snapshot_inputs') != normalized_c10_delta.get('snapshot_inputs')
            or run.get('skipped_snapshots') != normalized_c10_delta.get('skipped_snapshots')
            or run.get('new_graphs') != normalized_c10_delta.get('new_graphs')):
        raise ValueError('Standard collector fields do not exactly match the normalized C10 delta')
    nested = run.get('current_c10_growth')
    if (not isinstance(nested, dict)
            or nested.get('collector_delta_run') != normalized_c10_delta
            or nested.get('new_graphs') != normalized_c10_delta.get('new_graphs')):
        raise ValueError('C10 collector proof differs from its normalized standard run')
    expected_counts = {
        'union_nonempty_roots': 768,
        'union_ordered_edges': 18047,
        'shared_v16_c10_active_roots': 732,
        'c10_only_new_roots': 1,
        'c10_only_new_edges': 26,
    }
    if any(type(run.get(key)) is not int or run.get(key) != value
           for key, value in expected_counts.items()):
        raise ValueError('Typed source-union counters do not match the exact relation contract')
    source_active = {pid: edges for pid, edges in source_rows.items() if edges}
    current_active = {pid: edges for pid, edges in current_rows.items() if edges}
    parent_active = {pid: edges for pid, edges in source_parent_rows.items() if edges}
    shared = set(source_active) & set(current_active)
    if (len(source_rows) != 908 or len(source_active) != 767
            or sum(map(len, source_active.values())) != 18021
            or len(current_rows) != 929 or len(current_active) != 733
            or sum(map(len, current_active.values())) != 17002
            or len(shared) != 732
            or any(source_active[pid] != current_active[pid] for pid in shared)
            or len(set(source_active) - set(current_active)) != 35
            or len(set(current_active) - set(source_active)) != 1
            or len(parent_active) != 732
            or set(parent_active) != shared
            or any(parent_active[pid] != source_active[pid] for pid in shared)):
        raise ValueError('Source, C10, and verified 732-root parent vectors do not form the exact typed union')
    delta_pids = set(current_active) - set(source_active)
    rematch_pids = set(source_active) - set(parent_active)
    expected_delta = [{'root_pid': pid, 'ordered_target_pids': current_active[pid]}
                      for pid in sorted(delta_pids)]
    if (expected_delta != normalized_c10_delta.get('new_graphs')
            or len(expected_delta) != 1
            or sum(len(row['ordered_target_pids']) for row in expected_delta) != 26):
        raise ValueError('Current C10 standard run is not exactly its one 26-edge authenticated delta')
    native_nonempty = {pid for pid, edges in native_rows.items() if edges}
    union_active = {pid: source_active.get(pid, current_active.get(pid))
                    for pid in current_rows
                    if source_active.get(pid, current_active.get(pid))}
    expected_origins = {pid: edges for pid, edges in union_active.items()
                        if pid not in native_nonempty}
    expected_origin_rows = [
        {'root_pid': pid, 'ordered_target_pids': edges}
        for pid, edges in expected_origins.items()
    ]
    recorded_origins = _relation_map(run.get('typed_source_origin_graphs'),
                                     'Typed source-origin graphs')
    if (run.get('typed_source_origin_graphs') != expected_origin_rows
            or recorded_origins != expected_origins
            or len(recorded_origins) != 609
            or sum(map(len, recorded_origins.values())) != 14706):
        raise ValueError('Typed source-origin rows differ from exact non-Native union vectors')
    expected_rematch = {pid: source_active[pid] for pid in rematch_pids}
    expected_rematch_rows = [
        {'root_pid': pid, 'ordered_target_pids': expected_rematch[pid]}
        for pid in sorted(expected_rematch)
    ]
    recorded_rematch = _relation_map(run.get('rematch_new_graphs'), 'Rematch new graphs')
    if (run.get('rematch_new_graphs') != expected_rematch_rows
            or recorded_rematch != expected_rematch
            or len(recorded_rematch) != 35
            or sum(map(len, recorded_rematch.values())) != 1045):
        raise ValueError('Rematch-new rows differ from the exact 35-root/1,045-edge delta')
    transition = run.get('source_rematch_transition')
    if (not isinstance(transition, dict)
            or transition.get('source_policy') != SOURCE_POLICY
            or type(transition.get('new_from_source_parent_roots')) is not int
            or transition.get('new_from_source_parent_roots') != 35
            or type(transition.get('new_from_source_parent_edges')) is not int
            or transition.get('new_from_source_parent_edges') != 1045
            or transition.get('recording_identity_verified') is not False
            or type(transition.get('recording_edges_added')) is not int
            or transition.get('recording_edges_added') != 0):
        raise ValueError('Typed rematch transition lacks exact source policy or proof counters')
    receipts = transition.get('accepted_typed_radio_source_rematch')
    if receipts != required_source_receipts:
        raise ValueError('Typed rematch source proof receipts are missing or changed')


def _validate_saved_union_run_contract(run):
    """Compare typed row receipts with the pinned saved v04/v16/C10 documents."""
    source_graph = json.loads((SOURCE_CHILD / 'accepted-graphs.json').read_bytes())
    source_report = json.loads((SOURCE_CHILD / 'identity-map-rebinding-report.json').read_bytes())
    current_path = CURRENT_GROWTH / 'accepted-graphs.json'
    current_bytes = current_path.read_bytes()
    if _sha(current_bytes) != CURRENT_GROWTH_SHA256:
        raise ValueError('Pinned C10 accepted graph changed before source-union contract check')
    current_graph = json.loads(current_bytes)
    native_graph = json.loads((NATIVE_BASELINE / 'accepted-baseline-graphs.json').read_bytes())
    parent_bytes = SOURCE_PARENT_732.read_bytes()
    if _sha(parent_bytes) != SOURCE_PARENT_732_SHA256:
        raise ValueError('Pinned 732-root source parent graph changed')
    parent_graph = json.loads(parent_bytes)
    if source_report.get('producer') != SOURCE_PRODUCER:
        raise ValueError('Pinned v16 source report has the wrong producer')
    current_runs = current_graph.get('growth_runs')
    if not isinstance(current_runs, list) or len(current_runs) != 2:
        raise ValueError('Pinned C10 checkpoint no longer has its exact two runs')
    required_source_receipts = {
        'graph_export': _receipt(SOURCE_CHILD / 'accepted-graphs.json'),
        'rebase_report': _receipt(SOURCE_CHILD / 'identity-map-rebinding-report.json'),
        'state': _receipt(SOURCE_CHILD / 'state.json'),
        'independent_consumer': _receipt(SOURCE_CHILD / 'independent-consumer-return.json'),
        'terminal_status': _receipt(SOURCE_RUN / 'terminal-status.json'),
        'archive_manifest': _receipt(ARCHIVE_MANIFEST),
    }
    return _validate_union_run_contract(
        run,
        _relation_map(source_graph.get('accepted_roots'), 'Pinned v16 accepted graph'),
        _relation_map(current_graph.get('accepted_roots'), 'Pinned C10 accepted graph'),
        _relation_map(native_graph.get('accepted_roots'), 'Pinned Native v04 graph'),
        _relation_map(parent_graph.get('accepted_roots'), 'Pinned 732-root parent graph'),
        current_runs[1], required_source_receipts)


def _replay_standard_suffix(runs, state, roots, matcher, library_hash,
                           identity_hash, merger):
    normalized_runs = []
    for run in runs:
        if (not isinstance(run, dict)
                or run.get('producer') == 'opengenius-radio-native-alias-projection-run-v1'):
            raise ValueError('Only standard collector runs may follow the typed source-union prefix')
        new_roots, normalized = merger._validate_run_receipt(
            run, matcher.tracks, matcher, library_hash, identity_hash)
        if normalized.get('source_policy') != SOURCE_POLICY:
            raise ValueError('A later run after source union must retain authenticated radio-only policy')
        roots, _ = merger._append_graphs(roots, new_roots)
        state = merger.plan_genius_update(
            state, roots, matcher, library_hash, identity_hash)['next_state']
        if (run.get('resulting_state_sha256') is not None
                and run['resulting_state_sha256'] != state['state_sha256']):
            raise ValueError('A later collector run virtual state does not replay')
        normalized['resulting_state_sha256'] = state['state_sha256']
        normalized_runs.append(normalized)
    return state, roots, normalized_runs


def _verify_archive_and_source_child():
    """Verify the exact completed v16 child and its fixed producer/consumer closure."""
    archive_manifest_path, archive_bytes, archive_manifest = _read(ARCHIVE_MANIFEST)
    if _sha(archive_bytes) != ARCHIVE_MANIFEST_SHA256:
        raise ValueError('Accepted source archive manifest changed')
    if (archive_manifest.get('status') != 'verified_private_source_rematch_artifact_archive'
            or archive_manifest.get('source_producer') != SOURCE_PRODUCER
            or archive_manifest.get('source_policy') != SOURCE_POLICY
            or archive_manifest.get('runner_sha256') != '77ad3264f681cff3bab4ea2448324f5e9dc8eecd14694f4d1c964da0319f165c'):
        raise ValueError('Source archive is not the exact accepted fixed rematch producer')
    mirrors = archive_manifest.get('archive_file_manifest')
    if not isinstance(mirrors, list) or len(mirrors) != 42:
        raise ValueError('Source archive copy manifest has an unexpected shape')
    for row in mirrors:
        if not isinstance(row, dict):
            raise ValueError('Malformed source archive copy receipt')
        archived = (ARCHIVE / row['archive_path']).resolve()
        if _sha(archived.read_bytes()) != row['sha256']:
            raise ValueError('A source archive mirror changed')
        original = Path(row['original_path']).resolve()
        if _sha(original.read_bytes()) != row['sha256']:
            raise ValueError('An original source or producer-code receipt changed')
    _, _, terminal = _read(SOURCE_RUN / 'terminal-status.json')
    _, _, consumer = _read(SOURCE_CHILD / 'independent-consumer-return.json')
    if (terminal.get('child_returncode') != 0
            or terminal.get('runner_sha256') != archive_manifest['runner_sha256']
            or consumer.get('status') != 'independent_consumer_pass'
            or consumer.get('http_requests_added') != 0
            or consumer.get('attempted_pid_delta') != 0
            or consumer.get('source_policy') != SOURCE_POLICY
            or consumer.get('nonempty_root_count') != 767
            or consumer.get('ordered_edge_count') != 18021
            or consumer.get('root_deltas') != 35
            or consumer.get('edge_deltas') != 1045):
        raise ValueError('Source child terminal marker or independent consumer result is not accepted')
    loaded = {}
    for name, expected_hash in EXPECTED_SOURCE_HASHES.items():
        _, raw, doc = _read(SOURCE_CHILD / name)
        if _sha(raw) != expected_hash:
            raise ValueError('Accepted source child artifact hash changed: ' + name)
        loaded[name] = doc
    if consumer.get('output_hashes') != EXPECTED_SOURCE_HASHES:
        raise ValueError('Independent consumer no longer binds the exact source child documents')
    graph, report, state = (loaded['accepted-graphs.json'],
                            loaded['identity-map-rebinding-report.json'],
                            loaded['state.json'])
    radio = report.get('radio_source_map_rematch')
    if (report.get('producer') != SOURCE_PRODUCER
            or report.get('source_policy') != SOURCE_POLICY
            or report.get('zero_id_library_sha256') != '1ea4b3a0ee26495ddb5041d2c6683ec85327dfd6aca602a089b884be22c5c5b7'
            or report.get('identity_map_sha256') != INPUT_HASHES[str(IDENTITY_MAP.resolve())]
            or not isinstance(radio, dict)
            or radio.get('source_policy') != SOURCE_POLICY
            or radio.get('http_requests_added') != 0
            or radio.get('attempted_pid_delta') != 0
            or radio.get('recording_identity_verified') is not False
            or radio.get('recording_edges_added_by_rebinding') != 0
            or radio.get('genuine_recommendation_edges_added') != 1045
            or radio.get('newly_active_root_count') != 35
            or len(radio.get('new_graphs', [])) != 45
            or state.get('state_sha256') != '731c247dbf925a03fa35dcf041d29ae698d69ac1cd5a856c03089c821965f4d8'
            or graph.get('producer') != 'verified-radio-source-map-rematched-state-graph-export'):
        raise ValueError('Accepted source report violates the exact typed producer/policy contract')
    proof_receipts = radio.get('proof_input_receipts')
    if not isinstance(proof_receipts, list) or len(proof_receipts) != 1650:
        raise ValueError('Accepted source child has an unexpected proof-input closure size')
    seen = set()
    for row in proof_receipts:
        if (not isinstance(row, dict) or set(row) != {'path', 'sha256'}
                or not isinstance(row.get('path'), str)
                or not isinstance(row.get('sha256'), str)):
            raise ValueError('Malformed source proof-input receipt')
        path = Path(row['path']).resolve()
        key = (str(path), row['sha256'])
        if key in seen or _sha(path.read_bytes()) != row['sha256']:
            raise ValueError('A source proof input is duplicated or changed')
        seen.add(key)
    source_rows = _relation_map(graph.get('accepted_roots'), 'Accepted rematch graph')
    state_rows = _rows_by_pid(state, 'Accepted rematch state')
    merger, _, _, _ = _trusted_v10_modules()
    state_relations = _relation_map(merger._state_graph_rows(state), 'Accepted rematch state graph')
    if (set(source_rows) - set(state_relations)
            or any(source_rows[pid] != state_relations[pid] for pid in source_rows)
            or any(edges for pid, edges in state_relations.items() if pid not in source_rows)
            or len(source_rows) != 908
            or sum(bool(edges) for edges in source_rows.values()) != 767
            or sum(map(len, source_rows.values())) != 18021
            or len(state_rows) != 957):
        raise ValueError('Accepted source child graph/state shape or exact ordered edges changed')
    return graph, report, state, source_rows, state_rows, proof_receipts


def _trusted_v10_modules(merger_override=None):
    merger_override = merger_override or _ACTIVE_MERGER
    _, runtime_bytes, manifest = _read(V10_MANIFEST)
    if _sha(runtime_bytes) != V10_MANIFEST_SHA256:
        raise ValueError('Frozen v10 runtime manifest changed')
    by_path = {row['path']: row['sha256'] for row in manifest.get('modules', [])}
    for filename, expected in V10_MODULE_HASHES.items():
        rel = 'scripts/' + filename
        path = V10 / rel
        if by_path.get(rel) != expected or _sha(path.read_bytes()) != expected:
            raise ValueError('Frozen v10 trusted module changed: ' + filename)
    scripts_path = str((V10 / 'scripts').resolve())
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    trusted_root = str(Path(manifest['trusted_data_root']).resolve())
    supplied_root = os.environ.get('OPENGENIUS_TRUSTED_DATA_ROOT')
    if supplied_root is not None and str(Path(supplied_root).resolve()) != trusted_root:
        raise ValueError('Existing trusted data root differs from frozen v10 manifest')
    os.environ.setdefault('OPENGENIUS_TRUSTED_DATA_ROOT', trusted_root)
    merger = merger_override or importlib.import_module('merge_rebased_radio_growth')
    identity = importlib.import_module('music_identity_map')
    planner = importlib.import_module('plan_genius_update')
    bundle = importlib.import_module('update_genius_bundle')
    for index, (module, filename) in enumerate(zip((merger, identity, planner, bundle), V10_MODULE_HASHES)):
        expected_path = (V10 / 'scripts' / filename).resolve()
        actual_path = Path(module.__file__).resolve()
        if index == 0 and merger_override is not None:
            # The supplied merger is the versioned current dispatcher clone;
            # its original v10 inputs are separately pinned by this manifest.
            if not actual_path.is_file():
                raise ValueError('Versioned source-union dispatcher is unreadable')
        elif _sha(actual_path.read_bytes()) != V10_MODULE_HASHES[filename]:
            raise ValueError('Imported module content differs from fixed v10 source: ' + filename)
    return merger, identity, planner, bundle


def _verify_fixed_inputs():
    for path_text, expected in INPUT_HASHES.items():
        path = Path(path_text)
        if _sha(path.read_bytes()) != expected:
            raise ValueError('A fixed native projection input changed: ' + path.name)


def _build_projection_documents(native_context=None, merger_override=None):
    global _ACTIVE_MERGER
    if merger_override is not None:
        _ACTIVE_MERGER = merger_override
    _verify_fixed_inputs()
    source_graph, source_report, source_state, source_relations, source_rows, proof_receipts = _verify_archive_and_source_child()
    merger, identity, planner, _ = _trusted_v10_modules(merger_override)
    baseline_state = json.loads((NATIVE_BASELINE / 'baseline-state.json').read_bytes())
    baseline_graph = json.loads((NATIVE_BASELINE / 'accepted-baseline-graphs.json').read_bytes())
    baseline_report = json.loads((NATIVE_BASELINE / 'baseline-rebase-report.json').read_bytes())
    baseline_receipt = json.loads((NATIVE_BASELINE / 'native-baseline-alias-child-receipt.json').read_bytes())
    native_audit = baseline_report.get('relation_revalidation') or {}
    if (baseline_report.get('producer') != 'opengenius-typed-native-genius-baseline-alias-child-v1'
            or baseline_report.get('identity_map_sha256') != INPUT_HASHES[str(IDENTITY_MAP.resolve())]
            or baseline_report.get('next_state_sha256') != baseline_state.get('state_sha256')
            or baseline_report.get('recording_identity_verified') is not False
            or baseline_report.get('recording_edges_added') != 0
            or len(baseline_state.get('rows', [])) != 515
            or len(baseline_graph.get('accepted_roots', [])) != 515
            or sum(len(row['ordered_target_pids']) for row in baseline_graph['accepted_roots']) != 3341
            or baseline_receipt.get('preserved_state_assignment_count') != 515
            or baseline_receipt.get('preserved_directed_edge_count') != 3341
            or baseline_receipt.get('recording_identity_verified') is not False
            or baseline_receipt.get('recording_edges_added') != 0):
        raise ValueError('Native baseline does not match the pinned approved v04→v13 child')

    tracks, library_hash, _ = identity.load_library(track_snapshot=TRACK_SNAPSHOT)
    map_bytes = IDENTITY_MAP.read_bytes()
    identity_hash = _sha(map_bytes)
    matcher = identity.IdentityMap(json.loads(map_bytes), tracks, library_hash)
    from radio_snapshot_proof import radio_snapshot_proof_cache_scope, radio_snapshot_proof_validation_pass
    from seed_video_evidence import seed_video_selector_cache_scope
    native_cache_key = (library_hash, identity_hash,
                        tuple((path, _sha(Path(path).read_bytes())) for path in sorted(INPUT_HASHES)
                              if path.startswith(str(NATIVE_BASELINE.resolve()))))
    native_verified = native_context or _NATIVE_VERIFICATION_CACHE.get(native_cache_key)
    if native_verified is None:
        with radio_snapshot_proof_cache_scope(), seed_video_selector_cache_scope(), radio_snapshot_proof_validation_pass():
            native_verified = merger._verify_rebase_receipt(
                NATIVE_BASELINE / 'accepted-baseline-graphs.json',
                NATIVE_BASELINE / 'baseline-rebase-report.json',
                NATIVE_BASELINE / 'baseline-state.json',
                library_hash, identity_hash)
        _NATIVE_VERIFICATION_CACHE[native_cache_key] = native_verified
    verified_graph, verified_report, verified_state, baseline_provenance = native_verified
    if (verified_graph != baseline_graph or verified_report != baseline_report
            or verified_state != baseline_state
            or len(baseline_provenance.get('proof_input_receipts', [])) == 0
            or baseline_provenance.get('raw_observation_receipt_count') != native_audit.get('source_observation_count')):
        raise ValueError('Frozen v10 native rebase verifier result differs from exact v04 files')
    baseline_rows = _rows_by_pid(baseline_state, 'Native baseline state')
    for pid, row in baseline_rows.items():
        source_row = source_rows.get(pid)
        if source_row is None:
            raise ValueError('Source child dropped a native baseline PID')
        for key in ('genius_id', 'metadata_hex'):
            if source_row.get(key) != row.get(key):
                raise ValueError('Source child changed a native PID/GID or metadata field')
        if _canonical(source_row.get('equivalence_labels')) != _canonical(row.get('equivalence_labels')):
            raise ValueError('Source child changed a native identity-label assignment')
    native_relations = _relation_map(baseline_graph['accepted_roots'], 'Native baseline graph')
    if set(native_relations) - set(source_relations):
        raise ValueError('Source child dropped a native baseline relation row')
    for pid, edges in native_relations.items():
        if edges and source_relations[pid] != edges:
            raise ValueError('Source child changed a nonempty native ordered edge vector')
    _, library_hash_from_map, _ = identity.load_library(track_snapshot=TRACK_SNAPSHOT)
    if library_hash_from_map != library_hash:
        raise ValueError('Trusted Library hash changed while proving native baseline')
    projected_state = planner.plan_genius_update(
        baseline_state, source_graph['accepted_roots'], matcher,
        library_hash, identity_hash)['next_state']
    projected_rows = merger._state_graph_rows(projected_state)
    projected_relations = _relation_map(projected_rows, 'Projected native state graph')
    if (set(source_relations) - set(projected_relations)
            or any(projected_relations[pid] != edges for pid, edges in source_relations.items())
            or any(edges for pid, edges in projected_relations.items() if pid not in source_relations)):
        raise ValueError('Native planner projection changed source ordered relations')
    projected_by_pid = _rows_by_pid(projected_state, 'Projected native state')
    if set(projected_by_pid) != set(source_rows):
        raise ValueError('Native planner projection has a different PID assignment set from source child')
    for pid, row in baseline_rows.items():
        result = projected_by_pid.get(pid)
        if (result is None or result.get('genius_id') != row.get('genius_id')
                or result.get('metadata_hex') != row.get('metadata_hex')
                or _canonical(result.get('equivalence_labels')) != _canonical(row.get('equivalence_labels'))):
            raise ValueError('Native planner projection changed a preserved PID/GID/metadata assignment')

    # Authenticate the C10 run with the frozen collector verifier. Only its
    # single new PID may join v16; all shared active vectors must match.
    current_path = CURRENT_GROWTH / 'accepted-graphs.json'
    current_raw = current_path.read_bytes()
    if _sha(current_raw) != CURRENT_GROWTH_SHA256:
        raise ValueError('Pinned current C10 accepted graph changed')
    current = json.loads(current_raw)
    current_state = json.loads((CURRENT_GROWTH / 'state.json').read_bytes())
    current_relations_doc = json.loads((CURRENT_GROWTH / 'relations.json').read_bytes())
    current_eval = json.loads((CURRENT_GROWTH / 'accepted-evaluation.json').read_bytes())
    current_relations = _relation_map(current.get('accepted_roots'), 'Current C10 graph')
    current_runs = current.get('growth_runs')
    source_active = {pid: edges for pid, edges in source_relations.items() if edges}
    current_active = {pid: edges for pid, edges in current_relations.items() if edges}
    shared = set(source_active) & set(current_active)
    if (current.get('producer') != 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1'
            or current.get('library_sha256') != library_hash
            or current.get('identity_map_sha256') != identity_hash
            or current.get('source_policy') != SOURCE_POLICY
            or not isinstance(current_runs, list) or len(current_runs) != 2
            or len(source_active) != 767 or sum(map(len, source_active.values())) != 18021
            or len(current_active) != 733 or sum(map(len, current_active.values())) != 17002
            or len(shared) != 732
            or any(source_active[pid] != current_active[pid] for pid in shared)
            or len(set(source_active) - set(current_active)) != 35
            or len(set(current_active) - set(source_active)) != 1):
        raise ValueError('Current C10 and v16 graphs do not have the exact 732+35+1 relation')
    if (current.get('baseline_state_sha256') != baseline_state.get('state_sha256')
            or current.get('baseline_rebase_provenance') != baseline_provenance
            or current.get('source_policy_transition') != current_runs[0].get('source_policy_transition')):
        raise ValueError('Current C10 checkpoint is not bound to this exact native baseline/policy boundary')
    current_native_state, current_native_roots, current_native_run = merger._verify_native_alias_projection_run(
        current_runs[0], baseline_state=baseline_state,
        baseline_roots=baseline_graph['accepted_roots'],
        baseline_provenance=baseline_provenance, matcher=matcher,
        library_hash=library_hash, identity_hash=identity_hash)
    native_parent_relations = _relation_map(current_native_roots, 'C10 native alias source parent')
    native_parent_active = {pid: edges for pid, edges in native_parent_relations.items() if edges}
    if (current_native_run != current_runs[0]
            or len(native_parent_active) != 732
            or sum(map(len, native_parent_active.values())) != 16976
            or set(native_parent_active) != shared
            or any(native_parent_active[pid] != source_active[pid] for pid in shared)):
        raise ValueError('C10 first run does not replay the exact verified 732-root source parent')
    c10_delta, normalized_c10_delta = merger._validate_run_receipt(
        current_runs[1], matcher.tracks, matcher, library_hash, identity_hash)
    delta_relations = _relation_map(c10_delta, 'Validated C10 delta')
    if (set(delta_relations) != set(current_active) - set(source_active)
            or len(delta_relations) != 1
            or any(current_active[pid] != delta_relations[pid] for pid in delta_relations)
            or sum(map(len, delta_relations.values())) != 26
            or normalized_c10_delta.get('source_policy') != SOURCE_POLICY):
        raise ValueError('C10 collector replay differs from its sole 26-edge current-only root')
    # C10 was accepted as a two-step history: the native projection run
    # produced current_native_state/current_native_roots, then the collector
    # delta updated that state. Planning the aggregate graph from the original
    # baseline is not equivalent because planner similarity vectors retain
    # sequential update rounding. Replay that exact transition here.
    current_replayed_roots, _ = merger._append_graphs(current_native_roots, c10_delta)
    current_replayed_state = planner.plan_genius_update(
        current_native_state, current_replayed_roots, matcher,
        library_hash, identity_hash)['next_state']
    if (_canonical(current_replayed_state) != _canonical(current_state)
            or _relation_map(current_relations_doc.get('relation_rows'), 'C10 relations') != current_relations
            or current_eval.get('virtual_state_sha256') != current_state.get('state_sha256')
            or _relation_map(current_eval.get('relation_rows'), 'C10 evaluation') != current_relations):
        raise ValueError('Pinned current C10 state/relation/evaluation documents do not reproduce')
    # Keep every PID row and its order from the authenticated current parent.
    # It carries 20 empty assignments plus the new current root that v16 did
    # not have; overwrite only the complete authoritative v16 edge vectors.
    source_graph_rows = {row['root_pid']: row for row in source_graph['accepted_roots']}
    current_root_order = [row['root_pid'] for row in current['accepted_roots']]
    if not set(source_graph_rows).issubset(set(current_root_order)):
        raise ValueError('Current C10 parent is missing a v16 source assignment row')
    union_roots = []
    for current_row in current['accepted_roots']:
        pid = current_row['root_pid']
        authoritative = source_graph_rows.get(pid)
        if authoritative is None:
            union_roots.append(current_row)
        else:
            union_roots.append({**current_row,
                                'ordered_target_pids': list(authoritative['ordered_target_pids'])})
    union_relations = _relation_map(union_roots, 'Typed source union')
    if (len(union_relations) != 929
            or sum(bool(edges) for edges in union_relations.values()) != 768
            or sum(map(len, union_relations.values())) != 18047
            or any(union_relations[pid] != edges for pid, edges in source_relations.items())):
        raise ValueError('Typed source union is not exactly v16 plus the validated C10 delta')
    union_state = planner.plan_genius_update(
        baseline_state, union_roots, matcher, library_hash, identity_hash)['next_state']
    union_state_relations = _relation_map(merger._state_graph_rows(union_state), 'Union projected state')
    union_state_rows = _rows_by_pid(union_state, 'Union projected state')
    if union_state_relations != union_relations or len(union_state_rows) != 958:
        raise ValueError('Planner state does not exactly project the typed union relation graph')
    for pid, row in baseline_rows.items():
        updated = union_state_rows.get(pid)
        if (updated is None or updated.get('genius_id') != row.get('genius_id')
                or updated.get('metadata_hex') != row.get('metadata_hex')
                or _canonical(updated.get('equivalence_labels')) != _canonical(row.get('equivalence_labels'))):
            raise ValueError('Union planner changed a native PID/GID/metadata assignment')
    for pid, edges in native_relations.items():
        if edges and union_relations.get(pid) != edges:
            raise ValueError('Union graph changed a native nonempty ordered edge vector')
    baseline_receipts = {
        'graph_export': _receipt(NATIVE_BASELINE / 'accepted-baseline-graphs.json'),
        'rebase_report': _receipt(NATIVE_BASELINE / 'baseline-rebase-report.json'),
        'state': _receipt(NATIVE_BASELINE / 'baseline-state.json'),
    }
    source_receipts = {
        'graph_export': _receipt(SOURCE_CHILD / 'accepted-graphs.json'),
        'rebase_report': _receipt(SOURCE_CHILD / 'identity-map-rebinding-report.json'),
        'state': _receipt(SOURCE_CHILD / 'state.json'),
        'independent_consumer': _receipt(SOURCE_CHILD / 'independent-consumer-return.json'),
        'terminal_status': _receipt(SOURCE_RUN / 'terminal-status.json'),
        'archive_manifest': _receipt(ARCHIVE_MANIFEST),
    }
    transition = {
        'schema_version': 1,
        'producer': 'opengenius-radio-native-source-rematch-projection-v1',
        'source_policy': SOURCE_POLICY,
        'native_baseline': baseline_receipts,
        'native_frozen_verifier': {
            'producer': baseline_report['producer'],
            'verified': True,
            'proof_input_count': len(baseline_provenance['proof_input_receipts']),
            'provenance': baseline_provenance,
        },
        'accepted_typed_radio_source_rematch': source_receipts,
        'source_child_producer': SOURCE_PRODUCER,
        'source_child_consumer_status': 'independent_consumer_pass',
        'source_child_graph_sha256': EXPECTED_SOURCE_HASHES['accepted-graphs.json'],
        'source_child_report_sha256': EXPECTED_SOURCE_HASHES['identity-map-rebinding-report.json'],
        'source_child_state_sha256': EXPECTED_SOURCE_HASHES['state.json'],
        'baseline_nonempty_root_count': 159,
        'baseline_ordered_edge_count': 3341,
        'baseline_nonempty_edges_preserved_exactly': True,
        'added_source_roots_over_native_baseline': 608,
        'added_source_edges_over_native_baseline': 14680,
        'source_graph_nonempty_roots': 767,
        'source_graph_edges': 18021,
        'new_from_source_parent_roots': 35,
        'new_from_source_parent_edges': 1045,
        'http_requests_added': 0,
        'attempted_pid_delta': 0,
        'recording_identity_verified': False,
        'recording_edges_added': 0,
        'adapter_code': _receipt(ADAPTER_PATH),
        'trusted_v10_runtime_manifest': _receipt(V10_MANIFEST),
        'trusted_v11_runtime_manifest': _receipt(V11_UNION_RUNTIME / 'runtime-manifest.json'),
        'source_proof_input_count': len(proof_receipts),
    }
    native_nonempty_pids = {pid for pid, edges in native_relations.items() if edges}
    source_origin_rows = [row for row in union_roots
                          if row['ordered_target_pids'] and row['root_pid'] not in native_nonempty_pids]
    if len(source_origin_rows) != 609 or sum(len(row['ordered_target_pids']) for row in source_origin_rows) != 14706:
        raise ValueError('Typed source-origin graph is not 609 additional roots / 14,706 edges')
    rematch_parent = _relation_map(current_native_roots,
                                   'Verified 732-root source parent')
    rematch_new_pids = set(source_active) - set(rematch_parent)
    if len(rematch_new_pids) != 35:
        raise ValueError('Typed rematch new-PID set is not exactly the verified 35-root delta')
    rematch_new_graphs = [
        {'root_pid': pid, 'ordered_target_pids': list(source_active[pid])}
        for pid in sorted(rematch_new_pids)
    ]
    if sum(len(row['ordered_target_pids']) for row in rematch_new_graphs) != 1045:
        raise ValueError('Typed rematch edges do not match the exact 1,045-edge delta')
    source_union_run = {
        **normalized_c10_delta,
        'schema_version': 1,
        'producer': 'opengenius-radio-typed-source-union-run-v1',
        'source_policy': SOURCE_POLICY,
        'source_rematch_transition': transition,
        'accepted_typed_radio_source_rematch': source_receipts,
        'native_baseline': baseline_receipts,
        'proof_input_receipts': sorted(
            {_canonical(row).decode('utf-8'): row for row in
             proof_receipts + list(baseline_receipts.values()) + list(source_receipts.values())}.values(),
            key=lambda row: (row['path'], row['sha256'])),
        'http_requests': 0,
        'attempted_pid_delta': 0,
        'current_c10_growth': {
            'accepted_graph': _receipt(CURRENT_GROWTH / 'accepted-graphs.json'),
            'state': _receipt(CURRENT_GROWTH / 'state.json'),
            'relations': _receipt(CURRENT_GROWTH / 'relations.json'),
            'evaluation': _receipt(CURRENT_GROWTH / 'accepted-evaluation.json'),
            'collector_delta_run': normalized_c10_delta,
            'new_graphs': c10_delta,
        },
        # Preserve the standard collector contract for normal replay. The
        # collector's independently validated snapshots are the sole source
        # of this one current C10 PID.
        'snapshot_inputs': normalized_c10_delta['snapshot_inputs'],
        'new_graphs': normalized_c10_delta['new_graphs'],
        # Rematch provenance is separate and is rederived by the typed source
        # consumer from the accepted v16 child/report/consumer receipts.
        'typed_source_origin_graphs': source_origin_rows,
        'rematch_new_graphs': rematch_new_graphs,
        'source_origin_receipts': {
            'v16_accepted_graph': _receipt(SOURCE_CHILD / 'accepted-graphs.json'),
            'v16_rebinding_report': _receipt(SOURCE_CHILD / 'identity-map-rebinding-report.json'),
            'v16_independent_consumer': _receipt(SOURCE_CHILD / 'independent-consumer-return.json'),
            'v16_archive_manifest': _receipt(ARCHIVE_MANIFEST),
            'current_c10_batch_run': _receipt(CURRENT_GROWTH / 'accepted-graphs.json'),
        },
        'shared_v16_c10_active_roots': len(shared),
        'c10_only_new_roots': len(delta_relations),
        'c10_only_new_edges': sum(map(len, delta_relations.values())),
        'union_nonempty_roots': 768,
        'union_ordered_edges': 18047,
        'resulting_state_sha256': union_state['state_sha256'],
    }
    _validate_union_run_contract(
        source_union_run, source_relations, current_relations, native_relations,
        native_parent_relations, normalized_c10_delta, source_receipts)
    union_transition = current_runs[0]['source_policy_transition']
    accepted = {
        'schema_version': 1,
        'producer': 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1',
        'library_sha256': library_hash,
        'identity_map_sha256': identity_hash,
        'source_policy': SOURCE_POLICY,
        'baseline_state_sha256': baseline_state['state_sha256'],
        'baseline_accepted_roots': baseline_graph['accepted_roots'],
        'baseline_rebase_provenance': baseline_provenance,
        'accepted_roots': union_roots,
        'source_policy_transition': union_transition,
        'growth_runs': [current_native_run, source_union_run],
    }
    relations = {'schema_version': 1,
                 'producer': 'opengenius-ytmusic-recommendation-growth-relations-v1',
                 'relation_rows': union_roots}
    evaluation = {'schema_version': 1,
                  'producer': 'opengenius-ytmusic-recommendation-growth-evaluation-v1',
                  'virtual_state_sha256': union_state['state_sha256'],
                  'relation_rows': union_roots,
                  'http_requests_added': 0, 'attempted_pid_delta': 0,
                  'proof_input_receipts': source_union_run['proof_input_receipts']}
    proof = {
        'schema_version': 1, 'producer': 'opengenius-radio-native-source-union-checkpoint-v1',
        'status': 'candidate_typed_union_requires_normal_dispatch',
        'baseline_assignment_count': len(baseline_rows),
        'projected_assignment_count': len(union_state_rows),
        'source_only_assignment_count': len(set(union_state_rows) - set(baseline_rows)),
        'baseline_nonempty_root_count': 159, 'baseline_ordered_edge_count': 3341,
        'added_source_root_count_over_native_baseline': 608,
        'added_source_edge_count_over_native_baseline': 14680,
        'projected_nonempty_root_count': 768,
        'projected_ordered_edge_count': 18047,
        'source_child_new_nonempty_root_count': 36, 'source_child_new_edge_count': 1071,
        'http_requests_added': 0, 'attempted_pid_delta': 0,
        'recording_identity_verified': False, 'recording_edges_added': 0,
        'adapter_code': transition['adapter_code'],
        'trusted_v10_runtime_manifest': transition['trusted_v10_runtime_manifest'],
        'trusted_v11_runtime_manifest': transition['trusted_v11_runtime_manifest'],
    }
    return {'accepted-graphs.json': accepted, 'state.json': union_state,
            'relations.json': relations, 'accepted-evaluation.json': evaluation,
            'projection-proof.json': proof}


def create_projection_candidate(output_dir: Path):
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError('Projection output directory must not already exist')
    docs = _build_projection_documents()
    output_dir.mkdir(parents=True)
    for filename, document in docs.items():
        (output_dir / filename).write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    return _verify_projection_candidate(output_dir, docs)


def _verify_projection_candidate(output_dir: Path, expected=None):
    output_dir = Path(output_dir).resolve()
    expected = expected if expected is not None else _build_projection_documents()
    names = sorted(expected)
    actual_names = sorted(path.name for path in output_dir.iterdir() if path.is_file())
    if actual_names != names:
        raise ValueError('Projection candidate file set differs from the typed output contract')
    for name, wanted in expected.items():
        raw = (output_dir / name).read_bytes()
        try:
            actual = json.loads(raw)
        except Exception as error:
            raise ValueError('Projection candidate JSON is invalid: ' + name) from error
        if _canonical(actual) != _canonical(wanted):
            raise ValueError('Projection candidate differs from a trusted recomputation: ' + name)
    proof = expected['projection-proof.json']
    return {
        'status': 'typed_source_union_static_consumer_pass',
        'producer': 'opengenius-radio-native-source-union-checkpoint-v1',
        'output_dir': str(output_dir),
        'output_hashes': {name: _sha((output_dir / name).read_bytes()) for name in names},
        'native_assignment_count': proof['baseline_assignment_count'],
        'projected_assignment_count': proof['projected_assignment_count'],
        'source_only_assignment_count': proof['source_only_assignment_count'],
        'nonempty_root_count': proof['projected_nonempty_root_count'],
        'ordered_edge_count': proof['projected_ordered_edge_count'],
        'new_from_source_parent_root_count': 36,
        'new_from_source_parent_edge_count': 1071,
        'native_nonempty_root_prefix_count': proof['baseline_nonempty_root_count'],
        'native_ordered_edge_prefix_count': proof['baseline_ordered_edge_count'],
        'shared_v16_c10_active_root_count': 732,
        'c10_only_root_count': 1,
        'c10_only_edge_count': 26,
        'http_requests_added': 0, 'attempted_pid_delta': 0,
        'recording_identity_verified': False, 'recording_edges_added': 0,
        'note': 'Candidate only until this consumer is installed in a versioned normal dispatcher.'}


def replay_union_checkpoint(previous_dir, accepted, baseline_state, baseline_roots,
                            baseline_provenance, matcher, library_hash,
                            identity_hash, merger):
    """Normal-dispatch consumer. Rebuild from exact inputs, never report imports."""
    previous_dir = Path(previous_dir).resolve()
    if accepted.get('producer') != 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1':
        raise ValueError('Source union checkpoint must use the regular growth producer')
    runs = accepted.get('growth_runs')
    if not isinstance(runs, list) or len(runs) < 2 or not isinstance(runs[1], dict):
        raise ValueError('Source union checkpoint lacks its typed second run')
    transition = runs[1].get('source_rematch_transition')
    helper_path, _ = _verify_adapter_receipt(
        transition.get('adapter_code') if isinstance(transition, dict) else None)
    if transition.get('trusted_v11_runtime_manifest') != _receipt(
            V11_UNION_RUNTIME / 'runtime-manifest.json'):
        raise ValueError('Source union v11 manifest receipt differs from the trusted runtime')
    _validate_saved_union_run_contract(runs[1])
    baseline_graph = json.loads((NATIVE_BASELINE / 'accepted-baseline-graphs.json').read_bytes())
    baseline_report = json.loads((NATIVE_BASELINE / 'baseline-rebase-report.json').read_bytes())
    baseline_context = (baseline_graph, baseline_report, baseline_state, baseline_provenance)
    expected = _build_projection_documents(native_context=baseline_context,
                                           merger_override=merger)
    if (_relation_map(baseline_graph.get('accepted_roots'), 'Native baseline roots')
            != _relation_map(baseline_roots, 'Dispatcher native roots')):
        raise ValueError('Source union baseline roots differ from the active normal dispatcher')
    expected_graph = expected['accepted-graphs.json']
    expected_runs = expected_graph['growth_runs']
    runs = accepted.get('growth_runs')
    if (not isinstance(runs, list) or len(runs) < 2
            or _canonical(runs[:2]) != _canonical(expected_runs)):
        raise ValueError('Typed source-union prefix differs from the exact trusted recomputation')
    if (accepted.get('source_policy') != SOURCE_POLICY
            or accepted.get('library_sha256') != library_hash
            or accepted.get('identity_map_sha256') != identity_hash
            or accepted.get('baseline_state_sha256') != baseline_state.get('state_sha256')
            or _canonical(accepted.get('baseline_accepted_roots'))
               != _canonical(expected_graph.get('baseline_accepted_roots'))
            or _canonical(accepted.get('baseline_rebase_provenance'))
               != _canonical(expected_graph.get('baseline_rebase_provenance'))
            or _canonical(accepted.get('source_policy_transition'))
               != _canonical(expected_graph.get('source_policy_transition'))):
        raise ValueError('Typed source-union envelope differs from the exact trusted baseline')
    state = expected['state.json']
    roots = expected_graph['accepted_roots']
    normalized_runs = list(expected_runs)
    state, roots, suffix = _replay_standard_suffix(
        runs[2:], state, roots, matcher, library_hash, identity_hash, merger)
    normalized_runs.extend(suffix)
    expected_final_graph = {**expected_graph,
                            'accepted_roots': roots,
                            'growth_runs': normalized_runs}
    expected_final_relations = {**expected['relations.json'], 'relation_rows': roots}
    expected_final_evaluation = {**expected['accepted-evaluation.json'],
                                 'virtual_state_sha256': state['state_sha256'],
                                 'relation_rows': roots}
    expected_final = {
        'accepted-graphs.json': expected_final_graph,
        'state.json': state,
        'relations.json': expected_final_relations,
        'accepted-evaluation.json': expected_final_evaluation,
    }
    for name in ('accepted-graphs.json', 'state.json', 'relations.json', 'accepted-evaluation.json'):
        path = previous_dir / name
        try:
            actual = json.loads(path.read_bytes())
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError('Source union checkpoint output is missing or invalid: ' + name) from error
        if _canonical(actual) != _canonical(expected_final[name]):
            raise ValueError('Source union output differs from trusted recomputation: ' + name)
    return (state, roots, normalized_runs,
            _sha((previous_dir / 'accepted-graphs.json').read_bytes()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(create_projection_candidate(args.output), ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
