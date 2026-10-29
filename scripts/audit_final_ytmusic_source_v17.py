"""Read-only audit of the accepted 782-root lineage and current title-map child.

The script deliberately stops at the accepted parent. It does not credit an
in-progress campaign or claim that the 1,000-root final whole-source audit ran.
"""
import argparse
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path('/tmp/opengenius-final-campaign-native-v13-child-v04')
ORIGIN_INVENTORY = ROOT / 'data/ytmusic/root-source-origin-inventory-20261013-v01.json'
ORIGIN_INVENTORY_SHA = 'ecc9743c246949b5ddb3eca59491aacf4c6d9fa970ac7970058d75ad24523514'
EXPECTED_LIBRARY_SHA = '1ea4b3a0ee26495ddb5041d2c6683ec85327dfd6aca602a089b884be22c5c5b7'
EXPECTED_SNAPSHOT_SHA = '53a232dc9818e1039e016858b3c5c85a54e6fe4be899c94fa3cdd8ee44d7587f'
OLD_MAP_SHA = '195c9646dc1f579708ebb19b263c2f5c9828bb3e9542e6b6179253aae7fd0d67'
TITLE_MAP = ROOT / 'data/ytmusic/next-three-album-metadata-20261014-v02/reviewed-shiny-ost-title-aliases-v01/identity-map.json'
TITLE_MAP_SHA = 'f4ed5d23c1a6b2909defa135387867937f0133e550fe1db0c30a278e5fda4734'
PARENT_DIR = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261014-v14-source-union-live'
PARENT_PROGRESS = PARENT_DIR / 'progress.json'
PARENT_PROGRESS_SHA = '89161c70cf75292e13edaaa34a1c3494bc4151af162c51f8b215b443734170e8'
PARENT_GROWTH = PARENT_DIR / 'batch-0001/growth'
PARENT_GRAPH_SHA = '856c83333aad80d7159d3f1e02de1b043a9e1db8ce84e33fdd0790ec143d459f'
PARENT_STATE_SHA = '5dbd98eb574ed912d9fab33353b53839737a100a3956ff03874e1e0aeffec506'
PARENT_EVALUATION_SHA = '46a8ff90d3a7ad6a64fe491cc293f9db4b49a595c9d073126e780329be8fd907'
PARENT_MERGE_SHA = '2bea072532ac3eb6363d721deb6f3dbbe71a6e3a7110c8069424500621c6ea22'
V09_TERMINAL = PARENT_DIR / 'v09-replay-terminal.json'
V09_TERMINAL_SHA = 'd0935b9fb2c5c831f8c6a4cc6be50a27e9ea437cb66a816bfcc02d84a971f2be'
PRESERVATION_AUDIT = PARENT_DIR / 'accepted-782-preservation-audit-v01.json'
PRESERVATION_AUDIT_SHA = '45169840de67cc486f649db19268c5f750efe955dee93f6457381474802ea415'
PROOF_CHECK = PARENT_DIR / 'batch-0001/independent-new-radio-proof-check-v01.json'
PROOF_CHECK_SHA = '1c49e3d6842e068b03d23f6411abc8dcddbd09685c8c7ed29bc3bb6b9678e2f3'
SUFFIX_RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v09-suffix-orphan-v01')
SUFFIX_MANIFEST = SUFFIX_RUNTIME / 'runtime-manifest.json'
SUFFIX_MANIFEST_SHA = '29e1336c39be927840fee071a1d40dfb862d4819ed49df1d473a92b013b44ebe'
SUFFIX_HELPER_SHA = 'eb9574e3df6f8fa352b7e7e7ba3c022e50e14bd759ca3c7fafb1528fa243cc15'
TITLE_CHILD = ROOT / 'data/ytmusic/shiny-ost-title-alias-growth-child-20261014-v01'
TITLE_GRAPH = TITLE_CHILD / 'accepted-baseline-graphs.json'
TITLE_GRAPH_SHA = '0b0e12bce6644022ae921045bf9edfa9c4d8ab677bbc93d49070cd10c465e263'
TITLE_REPORT = TITLE_CHILD / 'identity-map-rebinding-report.json'
TITLE_REPORT_SHA = '999e3d4cfb14485fcdbc05fd1f154d6ce1f7501390c62bf95a8a6d41002b6a08'
TITLE_STATE = TITLE_CHILD / 'state.json'
TITLE_STATE_FILE_SHA = '477265d8936f6befa585a93b4826772815eea092872ff9c06d5571538a2eb4d7'
TITLE_STATE_SEAL = 'd2efd3b944c6bf0ecf6cbb98a575a3d4d6e2b54c61efda3d384076ac0d754c0a'
TITLE_TERMINAL = TITLE_CHILD / 'full-replay-terminal-v01.json'
TITLE_TERMINAL_SHA = 'ec54e8e3098756357ecd6be5fc8d09b946c78d80ad1f92c3fbe962fb69bfb1b2'
TITLE_RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v10-title-origin-v01')
TITLE_RUNTIME_MANIFEST = TITLE_RUNTIME / 'runtime-manifest.json'
TITLE_RUNTIME_MANIFEST_SHA = '5623c0e3bf6390cb401557d327b1e6b511629fbdcea5315d00b0bb53467c1c39'
CURRENT_RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v11-title-ledger-v01')
CURRENT_RUNTIME_MANIFEST = CURRENT_RUNTIME / 'runtime-manifest.json'
CURRENT_RUNTIME_MANIFEST_SHA = 'c93e6086ff4c37eced4433cb170991c44a720498e3618b785c2f250568ce2072'
CURRENT_PROGRESS_DIR = ROOT / 'data/ytmusic/automatic-genuine-roots-1000-20261014-v15-shiny-ost-radio-live-v01'
CURRENT_ATTEMPT_LEDGER = CURRENT_PROGRESS_DIR / 'attempt-ledger.json'
CURRENT_ATTEMPT_LEDGER_SHA = 'e0813f1411fde23d969f935ef917394226baeb25fccd32399eefff903b72e1fe'
BASELINE_RUNTIME = Path('/tmp/opengenius-final-campaign-runtime-v11-source-union-v12-baseline-projection-v01')
BASELINE_MANIFEST = BASELINE_RUNTIME / 'runtime-manifest.json'
BASELINE_MANIFEST_SHA = '1c65e4435e03440a63cc4023b8ed244c1d40c2a2ced70eb119a19184e4c430a3'
BASELINE_PROJECTION_SHA = '12fffca61adbfc53adb667e035cd38041853bf1e092ceed37d80d4ae4e25a9a7'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _pin(path, expected, label):
    require(Path(path).is_file() and sha(path) == expected, label + ' hash changed: ' + str(path))


def _active_vectors(graph):
    rows = graph.get('accepted_roots')
    require(isinstance(rows, list), 'accepted graph lacks ordered root rows')
    vectors = {}
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get('root_pid'), str)
                and isinstance(row.get('ordered_target_pids'), list),
                'accepted graph contains a malformed PID/vector row')
        pid = row['root_pid']
        require(pid and pid not in vectors, 'accepted graph duplicates a root PID')
        edges = row['ordered_target_pids']
        require(all(isinstance(edge, str) and edge for edge in edges),
                'accepted graph contains malformed ordered edges')
        vectors[pid] = edges
    return vectors


def _active_root_pids(vectors):
    """Count only nonempty ordered vectors, never assigned-empty rows."""
    return {pid for pid, targets in vectors.items() if targets}


def _source_partition(native_pids, ytmusic_pids, active_pids):
    """Return the exact total-genuine Native∪YT partition, rejecting unknowns."""
    native_pids = set(native_pids)
    ytmusic_pids = set(ytmusic_pids)
    active_pids = set(active_pids)
    require(native_pids <= active_pids and ytmusic_pids <= active_pids,
            'source-origin PID set includes inactive roots')
    overlap = native_pids & ytmusic_pids
    unknown = active_pids - native_pids - ytmusic_pids
    require(not unknown, 'active root lacks a verified Native or YTMusic origin')
    return {
        'native_only_roots': len(native_pids - overlap),
        'ytmusic_source_roots': len(ytmusic_pids),
        'native_ytmusic_overlap': len(overlap),
        'unknown_roots': 0,
        'total_active_roots': len(active_pids),
    }


def _validate_title_child_projection(child_graph, child_state):
    """Recheck the typed title-child graph against the fixed v12 state codec."""
    import importlib.util
    import os
    import sys
    graph_map = _active_vectors(child_graph)
    state_rows = child_state.get('rows')
    require(isinstance(state_rows, list) and len(state_rows) == 972
            and len(graph_map) == 943 and set(graph_map) <=
            {row.get('persistent_id') for row in state_rows},
            'title-child graph/state assignment shape differs from reviewed 943/972 form')
    prior_sys_path = list(sys.path)
    prior_env = os.environ.get('OPENGENIUS_TRUSTED_DATA_ROOT')
    before_modules = set(sys.modules)
    try:
        sys.path.insert(0, str(BASELINE_RUNTIME / 'scripts'))
        os.environ['OPENGENIUS_TRUSTED_DATA_ROOT'] = str(ROOT)
        merge_module = importlib.import_module('merge_rebased_radio_growth')
        merge_path = BASELINE_RUNTIME / 'scripts/merge_rebased_radio_growth.py'
        require(Path(merge_module.__file__).resolve() == merge_path.resolve(),
                'baseline merger loaded from unexpected path')
        manifest_modules = {row['path']: row['sha256'] for row in read(BASELINE_MANIFEST)['modules']}
        for name in ('evaluate_ytmusic_batch', 'music_identity_map', 'plan_genius_update',
                     'seed_video_evidence', 'probe_ytmusic_batch', 'radio_snapshot_proof',
                     'update_genius_bundle', 'genius_format'):
            loaded = sys.modules.get(name)
            require(loaded is not None and Path(loaded.__file__).resolve().parent ==
                    (BASELINE_RUNTIME / 'scripts').resolve(),
                    'frozen merger dependency loaded from unexpected path: ' + name)
            relative = Path(loaded.__file__).resolve().relative_to(BASELINE_RUNTIME.resolve()).as_posix()
            require(manifest_modules.get(relative) == sha(loaded.__file__),
                    'frozen merger dependency differs from manifest: ' + name)
        state_map = {row['root_pid']: row['ordered_target_pids']
                     for row in merge_module._state_graph_rows(child_state)}
    finally:
        for name in set(sys.modules) - before_modules:
            loaded = sys.modules.get(name)
            module_file = getattr(loaded, '__file__', None)
            if module_file and str(BASELINE_RUNTIME / 'scripts') in str(Path(module_file).resolve()):
                sys.modules.pop(name, None)
        sys.path[:] = prior_sys_path
        if prior_env is None:
            os.environ.pop('OPENGENIUS_TRUSTED_DATA_ROOT', None)
        else:
            os.environ['OPENGENIUS_TRUSTED_DATA_ROOT'] = prior_env
    require(len(state_map) == 972 and set(graph_map) <= set(state_map)
            and all(not edges for pid, edges in state_map.items() if pid not in graph_map),
            'title-child state contains a nonempty graph row omitted by export')
    projection_path = BASELINE_RUNTIME / 'scripts/baseline_graph_projection_v01.py'
    projection_spec = importlib.util.spec_from_file_location('_baseline_projection_v12', projection_path)
    require(projection_spec is not None and projection_spec.loader is not None,
            'cannot load fixed baseline projection validator')
    projection = importlib.util.module_from_spec(projection_spec)
    projection_spec.loader.exec_module(projection)
    require(Path(projection.__file__).resolve() == projection_path.resolve()
            and sha(projection_path) == BASELINE_PROJECTION_SHA,
            'baseline projection validator loaded from unexpected bytes/path')
    require(projection.validate_projection(graph_map, state_map) == 972,
            'title-child graph is not an exact projection of state relations')
    return {'graph_rows': len(graph_map), 'state_rows': len(state_map),
            'active_roots': len(_active_root_pids(graph_map)),
            'state_only_empty_rows': len(state_map) - len(graph_map)}


def verify_runtime_lineage():
    """Verify all producer/consumer manifests used for the accepted suffix."""
    for manifest_path, expected_sha, expected_producer, count in (
            (SUFFIX_MANIFEST, SUFFIX_MANIFEST_SHA,
             'opengenius-final-campaign-runtime-v11-source-union-v09-suffix-orphan', 177),
            (TITLE_RUNTIME_MANIFEST, TITLE_RUNTIME_MANIFEST_SHA,
             'opengenius-final-campaign-runtime-v11-source-union-v10-title-origin', 178),
            (CURRENT_RUNTIME_MANIFEST, CURRENT_RUNTIME_MANIFEST_SHA,
             'opengenius-final-campaign-runtime-v11-source-union-v11-title-ledger', 179),
            (BASELINE_MANIFEST, BASELINE_MANIFEST_SHA,
             'opengenius-final-campaign-runtime-v12-baseline-projection', 180)):
        _pin(manifest_path, expected_sha, 'runtime manifest')
        manifest = read(manifest_path)
        runtime_root = manifest_path.parent
        require(manifest.get('producer') == expected_producer
                and manifest.get('code_root') == str(runtime_root)
                and manifest.get('trusted_data_root') == str(ROOT)
                and isinstance(manifest.get('modules'), list)
                and len(manifest['modules']) == count,
                'runtime manifest identity/size differs: ' + str(manifest_path))
        seen = set()
        for entry in manifest['modules']:
            require(isinstance(entry, dict) and set(entry) == {'path', 'sha256'},
                    'runtime module receipt is malformed')
            relative = Path(entry['path'])
            require(not relative.is_absolute() and '..' not in relative.parts
                    and relative.as_posix() not in seen,
                    'runtime manifest contains unsafe or duplicate module path')
            module_path = runtime_root / relative
            _pin(module_path, entry['sha256'], 'runtime module')
            seen.add(relative.as_posix())
    suffix_modules = {row['path']: row['sha256'] for row in read(SUFFIX_MANIFEST)['modules']}
    helper = SUFFIX_RUNTIME / 'scripts/replay_source_union_suffix_v01.py'
    require(suffix_modules.get('scripts/replay_source_union_suffix_v01.py') == SUFFIX_HELPER_SHA
            and sha(helper) == SUFFIX_HELPER_SHA,
            'accepted suffix replay helper is not the fixed v09 manifest module')
    baseline_modules = {row['path']: row['sha256'] for row in read(BASELINE_MANIFEST)['modules']}
    baseline_projection = BASELINE_RUNTIME / 'scripts/baseline_graph_projection_v01.py'
    require(baseline_modules.get('scripts/baseline_graph_projection_v01.py') == BASELINE_PROJECTION_SHA
            and sha(baseline_projection) == BASELINE_PROJECTION_SHA,
            'v12 baseline graph projection helper is not the fixed manifest module')
    return {'suffix_manifest_sha256': SUFFIX_MANIFEST_SHA,
            'title_child_manifest_sha256': TITLE_RUNTIME_MANIFEST_SHA,
            'current_campaign_manifest_sha256': CURRENT_RUNTIME_MANIFEST_SHA,
            'baseline_projection_manifest_sha256': BASELINE_MANIFEST_SHA,
            'baseline_projection_helper_sha256': BASELINE_PROJECTION_SHA,
            'suffix_helper_sha256': SUFFIX_HELPER_SHA}


def verify_accepted_782_parent():
    """Verify source-backed 782-root parent and current-map typed title child."""
    verify_runtime_lineage()
    _pin(PARENT_PROGRESS, PARENT_PROGRESS_SHA, 'accepted parent progress')
    for path, digest, label in (
            (PARENT_GROWTH / 'accepted-graphs.json', PARENT_GRAPH_SHA, '782 accepted graph'),
            (PARENT_GROWTH / 'state.json', PARENT_STATE_SHA, '782 state'),
            (PARENT_GROWTH / 'accepted-evaluation.json', PARENT_EVALUATION_SHA, '782 evaluation'),
            (PARENT_GROWTH / 'merge-audit.json', PARENT_MERGE_SHA, '782 merge audit'),
            (V09_TERMINAL, V09_TERMINAL_SHA, 'v09 full suffix replay terminal'),
            (PRESERVATION_AUDIT, PRESERVATION_AUDIT_SHA, '782 preservation audit'),
            (PROOF_CHECK, PROOF_CHECK_SHA, '14-source proof check'),
            (TITLE_MAP, TITLE_MAP_SHA, 'current reviewed title map'),
            (TITLE_GRAPH, TITLE_GRAPH_SHA, 'typed title-child graph'),
            (TITLE_REPORT, TITLE_REPORT_SHA, 'typed title-child report'),
            (TITLE_STATE, TITLE_STATE_FILE_SHA, 'typed title-child state'),
            (TITLE_TERMINAL, TITLE_TERMINAL_SHA, 'typed title-child terminal'),
            (CURRENT_ATTEMPT_LEDGER, CURRENT_ATTEMPT_LEDGER_SHA, 'current campaign attempt ledger')):
        _pin(path, digest, label)
    parent = read(PARENT_PROGRESS)
    require(parent.get('status') == 'paused_at_checkpoint'
            and parent.get('http_requests') == 3992
            and parent.get('successful_outgoing_roots') == 782
            and len(parent.get('attempted_pids', [])) == 1837
            and len(parent.get('batches', [])) == 1,
            'accepted v14 parent progress counters/status differ')
    parent_batch = parent['batches'][0]
    require(parent_batch.get('status') == 'completed'
            and parent_batch.get('http_requests') == 44
            and len(parent_batch.get('attempted_pids', [])) == 14
            and parent_batch.get('growth_receipt') == {
                'path': str(PARENT_GROWTH / 'accepted-graphs.json'),
                'sha256': PARENT_GRAPH_SHA},
            '14-source accepted parent batch receipt differs')
    terminal = read(V09_TERMINAL)
    require(terminal.get('status') == 'completed' and terminal.get('returncode') == 0
            and terminal.get('new_http_requests') == 0
            and terminal.get('roots') == 782 and terminal.get('attempted_pids') == 1837
            and terminal.get('progress_sha256') == PARENT_PROGRESS_SHA
            and terminal.get('runtime_manifest_sha256') == SUFFIX_MANIFEST_SHA,
            'v09 normal orphan/suffix replay did not complete against exact parent')
    preservation = read(PRESERVATION_AUDIT)
    require(preservation == {
        'edges': 18271, 'native159_ordered_vectors_unchanged': True,
        'native_roots': 159, 'new14_exact_planned_roots': True,
        'normal_terminal_sha256': V09_TERMINAL_SHA,
        'old768_ordered_vectors_unchanged': True, 'remaining_to_target': 218,
        'status': 'accepted_782_checkpoint_preservation_pass',
        'total_roots': 782, 'ytmusic_roots': 623},
        'accepted 782 preservation receipt differs from exact verified counts')
    parent_graph = read(PARENT_GROWTH / 'accepted-graphs.json')
    parent_vectors = _active_vectors(parent_graph)
    parent_active = {pid for pid, edges in parent_vectors.items() if edges}
    require(parent_graph.get('identity_map_sha256') == OLD_MAP_SHA
            and parent_graph.get('library_sha256') == EXPECTED_LIBRARY_SHA
            and len(parent_active) == 782
            and sum(map(len, parent_vectors.values())) == 18271
            and len(parent_graph.get('growth_runs', [])) == 3,
            'accepted v14 parent graph is not the exact old-map three-run checkpoint')
    runs = parent_graph['growth_runs']
    typed_origins = runs[1].get('typed_source_origin_graphs')
    native_manifest = read(ORIGIN_INVENTORY)
    require(sha(ORIGIN_INVENTORY) == ORIGIN_INVENTORY_SHA,
            'historical Native/YT origin inventory changed')
    origin = native_manifest.get('source_pid_sets', {})
    native_pids = set(origin.get('original_native_baseline_nonempty_root_pids', []))
    legacy_yt = set(origin.get('current_732_ytmusic_source_backed_root_pids', []))
    require(len(native_pids) == 159 and len(legacy_yt) == 573 and not native_pids & legacy_yt,
            'historical Native/YT root source sets are inconsistent')
    typed_rows = _active_vectors({'accepted_roots': typed_origins})
    typed_yt = {pid for pid, edges in typed_rows.items() if edges}
    require(len(typed_yt) == 609 and legacy_yt <= typed_yt and not native_pids & typed_yt,
            'v06/v16 typed union no longer proves the exact 609 YT source roots')
    suffix_run = runs[2]
    suffix_rows = _active_vectors({'accepted_roots': suffix_run.get('new_graphs')})
    suffix_yt = {pid for pid, edges in suffix_rows.items() if edges}
    snapshots = suffix_run.get('snapshot_inputs')
    require(suffix_run.get('source_policy') == 'authenticated-watch-radio-only-v1'
            and len(suffix_yt) == 14 and isinstance(snapshots, list) and len(snapshots) == 14,
            'accepted 14-source suffix lacks exact authenticated per-PID rows')
    snapshot_pids = set()
    for row in snapshots:
        require(isinstance(row, dict)
                and set(row) == {'path', 'sha256', 'radio_only_source_proof'}
                and isinstance(row.get('radio_only_source_proof'), dict)
                and set(row['radio_only_source_proof']) == {'path', 'sha256'},
                'accepted 14-source suffix snapshot receipt is malformed')
        _pin(row['path'], row['sha256'], 'accepted source snapshot')
        _pin(row['radio_only_source_proof']['path'],
             row['radio_only_source_proof']['sha256'], 'accepted radio source proof')
        snap = read(row['path'])
        proof = read(row['radio_only_source_proof']['path'])
        pid = snap.get('local_seed', {}).get('persistent_id')
        require(pid and snap.get('account_authentication_verified') is True
                and proof.get('producer') == 'opengenius-authenticated-radio-snapshot-proof-v1'
                and proof.get('policy') == 'authenticated-watch-radio-only-v1'
                and proof.get('identity_map', {}).get('sha256') == OLD_MAP_SHA
                and proof.get('library_sha256') == EXPECTED_LIBRARY_SHA
                and pid not in snapshot_pids,
                'accepted source snapshot is not authenticated/unique')
        snapshot_pids.add(pid)
    require(suffix_yt == snapshot_pids and len(typed_yt | suffix_yt) == 623
            and not (typed_yt | suffix_yt) & native_pids
            and parent_active == native_pids | typed_yt | suffix_yt,
            '782 source PID sets do not partition into 159 Native + 623 YT roots')
    proof_check = read(PROOF_CHECK)
    require(proof_check.get('status') == 'pass' and proof_check.get('verified_proofs') == 14
            and proof_check.get('roots_credited') == 0,
            'independent 14-source proof check does not reconcile')

    child_graph = read(TITLE_GRAPH)
    child_report = read(TITLE_REPORT)
    rebind = child_report.get('identity_map_rebinding', {})
    child_state = read(TITLE_STATE)
    child_terminal = read(TITLE_TERMINAL)
    child_vectors = _active_vectors(child_graph)
    child_active = {pid for pid, edges in child_vectors.items() if edges}
    old_in_child = {pid: child_vectors.get(pid, []) for pid in parent_vectors}
    require(child_graph.get('producer') == 'verified-identity-map-rebound-state-graph-export'
            and child_graph.get('identity_status') == 'unverified_metadata_candidate'
            and child_graph.get('source_policy') == 'authenticated-watch-radio-only-v1'
            and child_graph.get('accepted_roots')
            and len(child_vectors) == len(parent_vectors) == 943 and child_active == parent_active
            and all(old_in_child[pid] == edges for pid, edges in parent_vectors.items())
            and sum(map(len, child_vectors.values())) == 18271,
            'title-child map rebinding changed PID/GID source vectors or active-root set')
    require(sha(TITLE_MAP) == TITLE_MAP_SHA
            and child_graph.get('source_identity_map_rebinding_report_sha256') == TITLE_REPORT_SHA
            and child_state.get('source', {}).get('identity_map_sha256') == TITLE_MAP_SHA
            and child_report.get('identity_map_sha256') == TITLE_MAP_SHA
            and child_report.get('accepted_root_count') == 782
            and child_report.get('directed_edge_count') == 18271
            and child_report.get('indexed_tracks') == 972
            and rebind.get('source_growth', {}).get('path') == str(PARENT_GROWTH)
            and rebind.get('old_growth_replayed_with_old_map') is True
            and rebind.get('pid_gid_assignments_unchanged') is True
            and rebind.get('metadata_bytes_unchanged') is True
            and rebind.get('relation_bytes_unchanged') is True
            and rebind.get('recording_identity_verified') is False
            and rebind.get('recording_edges_added_by_rebinding') == 0
            and rebind.get('activated_title_alias_count') == 43
            and rebind.get('activated_whole_credit_alias_count') == 0,
            'title-child report does not prove a title-only metadata transition')
    projection_result = _validate_title_child_projection(child_graph, child_state)
    require(child_state.get('state_sha256') == TITLE_STATE_SEAL
            and child_terminal.get('returncode') == 0
            and child_terminal.get('status') == 'normal_typed_child_consumer_pass'
            and child_terminal.get('new_http_requests') == 0
            and child_terminal.get('roots') == 782
            and child_terminal.get('state_sha256') == TITLE_STATE_SEAL
            and child_terminal.get('runtime_manifest_sha256') == TITLE_RUNTIME_MANIFEST_SHA,
            'normal title-child consumer receipt differs from its fixed result')

    ledger = read(CURRENT_ATTEMPT_LEDGER)
    require(ledger.get('producer') == 'opengenius-reviewed-title-child-attempt-ledger-v1'
            and ledger.get('schema_version') == 6
            and ledger.get('base_outgoing_roots') == 782
            and ledger.get('http_requests_total') == 3997
            and len(ledger.get('excluded_persistent_ids', [])) == 1837
            and ledger.get('parent_progress') == {
                'path': str(PARENT_PROGRESS), 'sha256': PARENT_PROGRESS_SHA}
            and ledger.get('parent_normal_terminal') == {
                'path': str(V09_TERMINAL), 'sha256': V09_TERMINAL_SHA}
            and ledger.get('child_normal_terminal') == {
                'path': str(TITLE_TERMINAL), 'sha256': TITLE_TERMINAL_SHA},
            'latest-map title-child attempt ledger does not bind the accepted parent/child receipts')
    return {
        'status': 'accepted_parent_lineage_verified_final_whole_source_audit_pending',
        'base_total_genuine_roots': len(parent_active),
        'native_only_roots': len(native_pids),
        'ytmusic_source_roots': len(typed_yt | suffix_yt),
        'native_ytmusic_overlap': len(native_pids & (typed_yt | suffix_yt)),
        'unknown_roots': len(parent_active - native_pids - typed_yt - suffix_yt),
        'ordered_edges': sum(map(len, parent_vectors.values())),
        'source_suffix_helper_sha256': SUFFIX_HELPER_SHA,
        'parent_graph_sha256': PARENT_GRAPH_SHA,
        'current_map_sha256': TITLE_MAP_SHA,
        'current_title_child_terminal_sha256': TITLE_TERMINAL_SHA,
        'current_campaign_attempts': 1837,
        'current_campaign_http_before_live_batch': 3997,
        'title_child_graph_rows': projection_result['graph_rows'],
        'title_child_state_rows': projection_result['state_rows'],
        'title_child_state_only_empty_rows': projection_result['state_only_empty_rows'],
        'root_target': 1000,
        'remaining_genuine_roots': 218,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-accepted-parent', action='store_true')
    args = parser.parse_args()
    if not args.verify_accepted_parent:
        parser.error('only the pre-final accepted-parent audit is available; final whole-source audit runs after target')
    print(json.dumps(verify_accepted_782_parent(), ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
