"""Candidate adapter pinned to the approved native v04→v13 baseline child.

This remains a candidate-only adapter. It replays the frozen v06 native
verifier before projecting the separately accepted v16 radio source child.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CHILD = Path('/tmp/opengenius-radio-source-map-rematch-v16-732-actual-child-v01')
SOURCE_RUN = Path('/tmp/opengenius-radio-source-map-rematch-v16-run-v01')
ARCHIVE = ROOT / 'data/ytmusic/typed-radio-source-rematch-v16-accepted-20261013-v01'
ARCHIVE_MANIFEST = ARCHIVE / 'archive-manifest.json'
V06 = Path('/tmp/opengenius-final-campaign-runtime-v06')
V06_MANIFEST = V06 / 'runtime-manifest.json'
TRACK_SNAPSHOT = ROOT / 'data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json'
IDENTITY_MAP = Path('/tmp/opengenius-album-alias-append-v03/03-cafe-stella-map.json')
NATIVE_BASELINE = Path('/tmp/opengenius-final-campaign-native-v13-child-v04')

ARCHIVE_MANIFEST_SHA256 = '23527427700e6d2b64f54bd7bedaa2eaa3f5bc2d348dafecf9910f847b46ca92'
V06_MANIFEST_SHA256 = '2310e0eb13b21da42baebf5f806f71bda37490a3c3f9b228b901bc420acba5f5'
V06_MODULE_HASHES = {
    'merge_rebased_radio_growth.py': '0e1d22aefff474c05c7024b7df0152cbfd926038f7226458badc62ea576facd9',
    'music_identity_map.py': 'f1befec3b36108e7904fa97aa9abf6adc43226707018ea07fa1f13e8be66f169',
    'plan_genius_update.py': 'ae67b48265d3a5bade390e45a3e29b9bedf5c65101cc4e9f838ecad323056848',
    'update_genius_bundle.py': 'd3d74efe1e64c7801ad8f2e70a60359f0a2847b16e9f63dfa8b87c73fbcea25b',
}
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


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    return path, raw, json.loads(raw)


def _receipt(path: Path) -> dict:
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': _sha(path.read_bytes())}


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
    merger, _, _, _ = _trusted_v06_modules()
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


def _trusted_v06_modules():
    _, runtime_bytes, manifest = _read(V06_MANIFEST)
    if _sha(runtime_bytes) != V06_MANIFEST_SHA256:
        raise ValueError('Frozen v06 runtime manifest changed')
    by_path = {row['path']: row['sha256'] for row in manifest.get('modules', [])}
    for filename, expected in V06_MODULE_HASHES.items():
        rel = 'scripts/' + filename
        path = V06 / rel
        if by_path.get(rel) != expected or _sha(path.read_bytes()) != expected:
            raise ValueError('Frozen v06 trusted module changed: ' + filename)
    scripts_path = str((V06 / 'scripts').resolve())
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    trusted_root = str(Path(manifest['trusted_data_root']).resolve())
    supplied_root = os.environ.get('OPENGENIUS_TRUSTED_DATA_ROOT')
    if supplied_root is not None and str(Path(supplied_root).resolve()) != trusted_root:
        raise ValueError('Existing trusted data root differs from frozen v06 manifest')
    os.environ.setdefault('OPENGENIUS_TRUSTED_DATA_ROOT', trusted_root)
    merger = importlib.import_module('merge_rebased_radio_growth')
    identity = importlib.import_module('music_identity_map')
    planner = importlib.import_module('plan_genius_update')
    bundle = importlib.import_module('update_genius_bundle')
    for module, filename in zip((merger, identity, planner, bundle), V06_MODULE_HASHES):
        expected_path = (V06 / 'scripts' / filename).resolve()
        actual_path = Path(module.__file__).resolve()
        if actual_path != expected_path or _sha(actual_path.read_bytes()) != V06_MODULE_HASHES[filename]:
            raise ValueError('Imported module is not the fixed v06 trusted source: ' + filename)
    return merger, identity, planner, bundle


def _verify_fixed_inputs():
    for path_text, expected in INPUT_HASHES.items():
        path = Path(path_text)
        if _sha(path.read_bytes()) != expected:
            raise ValueError('A fixed native projection input changed: ' + path.name)


def _build_projection_documents():
    _verify_fixed_inputs()
    source_graph, source_report, source_state, source_relations, source_rows, proof_receipts = _verify_archive_and_source_child()
    merger, identity, planner, _ = _trusted_v06_modules()
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
    native_verified = _NATIVE_VERIFICATION_CACHE.get(native_cache_key)
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
        raise ValueError('Frozen v06 native rebase verifier result differs from exact v04 files')
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
        'adapter_code': {'path': str(Path(__file__).resolve()), 'sha256': _sha(Path(__file__).read_bytes())},
        'trusted_v06_runtime_manifest': _receipt(V06_MANIFEST),
        'source_proof_input_count': len(proof_receipts),
    }
    run = {
        'schema_version': 1,
        'producer': PROJECTION_RUN_PRODUCER,
        'source_policy': SOURCE_POLICY,
        'source_policy_transition': transition,
        'accepted_typed_radio_source_rematch': source_receipts,
        'native_baseline': baseline_receipts,
        'proof_input_receipts': sorted(
            {_canonical(row).decode('utf-8'): row for row in
             proof_receipts + list(baseline_receipts.values()) + list(source_receipts.values())}.values(),
            key=lambda row: (row['path'], row['sha256'])),
        'http_requests': 0,
        'attempted_pid_delta': 0,
        'resulting_state_sha256': projected_state['state_sha256'],
    }
    accepted = {
        'schema_version': 1,
        'producer': PROJECTION_PRODUCER,
        'library_sha256': library_hash,
        'identity_map_sha256': identity_hash,
        'source_policy': SOURCE_POLICY,
        'baseline_state_sha256': baseline_state['state_sha256'],
        'baseline_accepted_roots': baseline_graph['accepted_roots'],
        'baseline_rebase_provenance': baseline_provenance,
        'accepted_roots': source_graph['accepted_roots'],
        'source_policy_transition': transition,
        'growth_runs': [run],
    }
    relations = {'schema_version': 1,
                 'producer': 'opengenius-radio-native-source-rematch-projection-relations-v1',
                 'relation_rows': source_graph['accepted_roots']}
    evaluation = {'schema_version': 1,
                  'producer': 'opengenius-radio-native-source-rematch-projection-evaluation-v1',
                  'virtual_state_sha256': projected_state['state_sha256'],
                  'relation_rows': source_graph['accepted_roots'],
                  'http_requests_added': 0, 'attempted_pid_delta': 0,
                  'proof_input_receipts': run['proof_input_receipts']}
    proof = {
        'schema_version': 1, 'producer': PROJECTION_PRODUCER,
        'status': 'candidate_native_projection_not_growth_acceptance',
        'baseline_assignment_count': len(baseline_rows),
        'projected_assignment_count': len(projected_by_pid),
        'source_only_assignment_count': len(set(projected_by_pid) - set(baseline_rows)),
        'baseline_nonempty_root_count': 159, 'baseline_ordered_edge_count': 3341,
        'added_source_root_count_over_native_baseline': 608,
        'added_source_edge_count_over_native_baseline': 14680,
        'projected_nonempty_root_count': sum(bool(row['ordered_target_pids']) for row in source_graph['accepted_roots']),
        'projected_ordered_edge_count': sum(len(row['ordered_target_pids']) for row in source_graph['accepted_roots']),
        'source_child_new_nonempty_root_count': 35, 'source_child_new_edge_count': 1045,
        'http_requests_added': 0, 'attempted_pid_delta': 0,
        'recording_identity_verified': False, 'recording_edges_added': 0,
        'adapter_code': transition['adapter_code'],
        'trusted_v06_runtime_manifest': transition['trusted_v06_runtime_manifest'],
    }
    return {'accepted-graphs.json': accepted, 'state.json': projected_state,
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
    return _verify_projection_candidate(output_dir)


def _verify_projection_candidate(output_dir: Path):
    output_dir = Path(output_dir).resolve()
    expected = _build_projection_documents()
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
        'status': 'native_projection_candidate_static_consumer_pass',
        'producer': PROJECTION_PRODUCER,
        'output_dir': str(output_dir),
        'output_hashes': {name: _sha((output_dir / name).read_bytes()) for name in names},
        'native_assignment_count': proof['baseline_assignment_count'],
        'projected_assignment_count': proof['projected_assignment_count'],
        'source_only_assignment_count': proof['source_only_assignment_count'],
        'nonempty_root_count': proof['projected_nonempty_root_count'],
        'ordered_edge_count': proof['projected_ordered_edge_count'],
        'new_from_source_parent_root_count': proof['source_child_new_nonempty_root_count'],
        'new_from_source_parent_edge_count': proof['source_child_new_edge_count'],
        'native_nonempty_root_prefix_count': proof['baseline_nonempty_root_count'],
        'native_ordered_edge_prefix_count': proof['baseline_ordered_edge_count'],
        'http_requests_added': 0, 'attempted_pid_delta': 0,
        'recording_identity_verified': False, 'recording_edges_added': 0,
        'note': 'Candidate only; must receive dedicated typed dispatch and later source-union validation.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(create_projection_candidate(args.output), ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
