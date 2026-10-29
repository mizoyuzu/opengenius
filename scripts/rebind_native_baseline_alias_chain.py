"""Private typed native 515-row child for the reviewed v04→v13 map chain.

This producer is deliberately separate from radio growth. It starts by
replaying the existing native v04 receipt through the trusted native baseline
producer, then proves the exact approved metadata-only map chain and reseals
only the identity-map hash in the native state.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

from music_identity_map import IdentityMap, load_library
from plan_genius_update import _hash as state_hash, _source as identity_source, plan_genius_update
from update_genius_bundle import read_bundle, state_rows, update_bundle
from rebind_genius_baseline_identity import (
    rebind_baseline, _read, _receipt, _reseal_map_hash, _sha,
)
from rebind_radio_growth_alias_chain import (
    staged_v04_to_v11_transitions, staged_v11_to_v12_transition,
    verify_v04_v11_identity_map_alias_chain,
)
from rebind_radio_growth_identity import verify_reviewed_album_alias_map_delta
from rebind_radio_growth_album_append_chain import verify_reviewed_album_append_chain
from merge_rebased_radio_growth import _relation_map, _state_graph_rows, write_json

PRODUCER = 'opengenius-typed-native-genius-baseline-alias-child-v1'
EXPECTED_V04_RECEIPT_SHA256 = '35058a3366adfb9076edf6c802dd3f2c5b5798ffad86ef783cf36652ff4eacd1'
EXPECTED_V13_MAP_SHA256 = '195c9646dc1f579708ebb19b263c2f5c9828bb3e9542e6b6179253aae7fd0d67'
EXPECTED_MUSIC_EXECUTABLE_SHA256 = 'a8ddce118ebe19132a26d8b4d7efab7020fa38386aadedeb5f9ce629e6981f6a'


def _read(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    return path, raw, json.loads(raw)


def _manifest(rows, *, workspace_root):
    found = {}
    for row in rows:
        value = Path(row['path'])
        path = (value if value.is_absolute() else Path(workspace_root) / value).resolve()
        digest = _sha(path.read_bytes())
        if digest != row['sha256']:
            raise ValueError('A source in the native alias chain changed: ' + str(path))
        found[(str(path), digest)] = {'path': str(path), 'sha256': digest}
    return [found[key] for key in sorted(found)]


def _code_receipts():
    modules = (Path(__file__), Path(rebind_baseline.__code__.co_filename),
               Path(verify_v04_v11_identity_map_alias_chain.__code__.co_filename),
               Path(verify_reviewed_album_alias_map_delta.__code__.co_filename),
               Path(verify_reviewed_album_append_chain.__code__.co_filename),
               Path(_relation_map.__code__.co_filename),
               Path(plan_genius_update.__code__.co_filename),
               Path(update_bundle.__code__.co_filename),
               Path(load_library.__code__.co_filename))
    return [_receipt(path) for path in modules]


def _assert_state_only_map_change(parent, child, expected_map_sha256):
    old = deepcopy(parent)
    new = deepcopy(child)
    if old.get('source', {}).get('identity_map_sha256') is None:
        raise ValueError('Native parent state has no identity-map source hash')
    if new.get('source', {}).get('identity_map_sha256') != expected_map_sha256:
        raise ValueError('Native child does not bind the requested final map')
    old['source']['identity_map_sha256'] = expected_map_sha256
    old['state_sha256'] = state_hash({k: v for k, v in old.items() if k != 'state_sha256'})
    if old != child:
        raise ValueError('Native child changed more than the state map hash and seal')


def _assert_native_rows_and_graph(parent_state, parent_graph, child_state, child_graph):
    if parent_graph.get('accepted_roots') != child_graph.get('accepted_roots'):
        raise ValueError('Native child changed root rows or ordered edges')
    if parent_state.get('rows') != child_state.get('rows'):
        raise ValueError('Native child changed any PID/GID/metadata/relation row')
    parent_ids, parent_metadata, parent_relations = state_rows(parent_state)
    child_ids, child_metadata, child_relations = state_rows(child_state)
    if ((parent_ids, parent_metadata, parent_relations) !=
            (child_ids, child_metadata, child_relations)):
        raise ValueError('Native child changed the native PID/GID/metadata/relation projection')
    if _relation_map(parent_graph['accepted_roots'], 'Native parent graph') != _relation_map(
            child_graph['accepted_roots'], 'Native child graph'):
        raise ValueError('Native child changed ordered relation targets')


def _assert_exact_child_receipt(actual, expected):
    if not isinstance(actual, dict) or actual != expected:
        raise ValueError('Native child receipt differs from the complete recomputed receipt')


def verify_v04_to_v13_map_chain(*, repo_root, append_chain_receipt_path,
                                append_final_map_path):
    """Replay the exact approved v04→v11→v12→v13 metadata-only chain."""
    root = Path(repo_root).resolve()
    maps = root / 'data/new-library-rebased-20261009-v03/zero-id-source'
    snapshot = maps / 'track-snapshot.json'
    old_map = maps / 'identity-map-aliases-v04.json'
    map_v11 = maps / 'identity-map-aliases-v11-nine-credit-thirty-title-staged.json'
    map_v12 = maps / 'identity-map-aliases-v12-sanoba-album-alias-staged.json'
    final_map = Path(append_final_map_path).resolve()
    if _sha(final_map.read_bytes()) != EXPECTED_V13_MAP_SHA256:
        raise ValueError('Final native alias map is not the reviewed v13 map')
    stages = staged_v04_to_v11_transitions(root)
    v04_v11 = verify_v04_v11_identity_map_alias_chain(
        track_snapshot=snapshot, input_identity_map_path=old_map,
        output_identity_map_path=map_v11, transitions=stages)
    sanoba = staged_v11_to_v12_transition(root)
    sanoba_proof = verify_reviewed_album_alias_map_delta(
        track_snapshot=snapshot,
        input_identity_map_path=sanoba['input_identity_map_path'],
        output_identity_map_path=sanoba['output_identity_map_path'],
        proposals_path=sanoba['proposals_path'],
        source_evidence_path=sanoba['source_evidence_path'],
        candidate_audit_path=sanoba['candidate_audit_path'],
        review_decisions_path=sanoba['review_decisions_path'],
        receipt_path=sanoba['receipt_path'])
    append = verify_reviewed_album_append_chain(
        track_snapshot=snapshot, chain_receipt_path=append_chain_receipt_path,
        start_map_path=map_v12, final_map_path=final_map)
    if (v04_v11['input_identity_map']['sha256'] != _sha(old_map.read_bytes())
            or v04_v11['output_identity_map']['sha256'] != _sha(map_v11.read_bytes())
            or sanoba_proof['input_identity_map']['sha256'] != _sha(map_v11.read_bytes())
            or sanoba_proof['output_identity_map']['sha256'] != _sha(map_v12.read_bytes())
            or append['input_identity_map']['sha256'] != _sha(map_v12.read_bytes())
            or append['output_identity_map']['sha256'] != EXPECTED_V13_MAP_SHA256
            or v04_v11.get('recording_identity_verified') is not False
            or v04_v11.get('recording_edges_added') != 0
            or sanoba_proof.get('recording_identity_verified') is not False
            or sanoba_proof.get('recording_edges_added') != 0
            or append.get('recording_identity_verified') is not False
            or append.get('recording_edges_added') != 0):
        raise ValueError('One of the reviewed native map transitions is incomplete or non-metadata')
    return {
        'track_snapshot': _receipt(snapshot),
        'input_identity_map': _receipt(old_map),
        'v11_identity_map': _receipt(map_v11),
        'v12_identity_map': _receipt(map_v12),
        'output_identity_map': _receipt(final_map),
        'v04_to_v11': v04_v11,
        'v11_to_v12': sanoba_proof,
        'v12_to_v13': append,
        'proof_input_receipts': _manifest(
            v04_v11['proof_input_receipts'] +
            sanoba_proof.get('proof_input_receipts', []) +
            append['proof_input_receipts'] + [
                _receipt(snapshot), _receipt(old_map), _receipt(map_v11),
                _receipt(map_v12), _receipt(final_map)], workspace_root=root),
        'recording_identity_verified': False,
        'recording_edges_added': 0,
    }


def rebind_native_baseline_alias_chain(*, repo_root, native_v04_dir,
        source_bundle_path, executable_path, append_chain_receipt_path,
        append_final_map_path, output_dir, replay_dir):
    """Produce a source-verified v13 native baseline with unchanged SQL rows."""
    root = Path(repo_root).resolve()
    native_v04_dir = Path(native_v04_dir).resolve()
    output_dir = Path(output_dir).resolve()
    replay_dir = Path(replay_dir).resolve()
    if output_dir.exists() or replay_dir.exists():
        raise FileExistsError('Native rebind outputs must use fresh private directories')
    receipt_path = native_v04_dir / 'baseline-identity-rebinding-receipt.json'
    report_path = native_v04_dir / 'baseline-identity-rebinding-report.json'
    state_path = native_v04_dir / 'baseline-state.json'
    graph_path = native_v04_dir / 'accepted-baseline-graphs.json'
    v04_receipt_path, v04_receipt_bytes, v04_receipt = _read(receipt_path)
    v04_report_path, v04_report_bytes, v04_report = _read(report_path)
    v04_state_path, v04_state_bytes, v04_state = _read(state_path)
    v04_graph_path, v04_graph_bytes, v04_graph = _read(graph_path)
    if (_sha(v04_receipt_bytes) != EXPECTED_V04_RECEIPT_SHA256
            or _sha(v04_receipt_bytes) != v04_report.get('receipt_sha256')
            or v04_report.get('receipt_path') != str(v04_receipt_path)
            or v04_receipt.get('producer') != 'opengenius-typed-genius-baseline-identity-rebind-v1'
            or v04_receipt.get('recording_identity_verified') is not False
            or v04_receipt.get('recording_edges_added') != 0
            or v04_receipt.get('preserved_track_count') != 3004
            or v04_receipt.get('preserved_state_assignment_count') != 515
            or v04_receipt.get('preserved_directed_edge_count') != 3341
            or v04_graph.get('producer') != 'opengenius-typed-rebound-baseline-graph-v1'
            or v04_state.get('source', {}).get('identity_map_sha256') !=
               v04_receipt.get('new_identity_map', {}).get('sha256')):
        raise ValueError('Existing native v04 receipt is not the exact reviewed 515-track baseline')
    paths = {key: Path(v04_receipt[key]['path']).resolve() for key in (
        'old_identity_map', 'new_identity_map', 'credit_intermediate_map',
        'credit_application_receipt', 'credit_review_decisions', 'credit_proposals',
        'title_application_receipt', 'title_review_decisions', 'title_proposals',
        'track_snapshot', 'source_baseline_state', 'source_baseline_graph', 'source_rebase_report')}
    if Path(source_bundle_path).resolve().is_dir() is False:
        raise ValueError('Pinned original native bundle is missing')
    bundle_files = read_bundle(Path(source_bundle_path).resolve())
    bundle_hashes = {name: _sha(blob) for name, blob in sorted(bundle_files.items())}
    if bundle_hashes != v04_receipt.get('source_bundle_file_sha256'):
        raise ValueError('Original native bundle differs from the v04 receipt')
    executable_path = Path(executable_path).resolve()
    if (not executable_path.is_file()
            or _sha(executable_path.read_bytes()) != EXPECTED_MUSIC_EXECUTABLE_SHA256):
        raise ValueError('Music executable input differs from the reviewed local binary')

    # Reproduce the existing v04 native child with the current trusted writer.
    # This validates its original typed graph/report and physical SQL without
    # modifying the source bundle or reusing its serialized assertions blindly.
    replayed = rebind_baseline(
        bundle_path=source_bundle_path,
        track_snapshot_path=paths['track_snapshot'],
        old_identity_map_path=paths['old_identity_map'],
        new_identity_map_path=paths['new_identity_map'],
        credit_intermediate_map_path=paths['credit_intermediate_map'],
        credit_application_receipt_path=paths['credit_application_receipt'],
        credit_review_path=paths['credit_review_decisions'],
        credit_proposals_path=paths['credit_proposals'],
        title_application_receipt_path=paths['title_application_receipt'],
        title_review_path=paths['title_review_decisions'],
        title_proposals_path=paths['title_proposals'],
        baseline_state_path=paths['source_baseline_state'],
        baseline_graph_path=paths['source_baseline_graph'],
        rebase_report_path=paths['source_rebase_report'],
        executable_path=executable_path, output_dir=replay_dir)
    replay_state = json.loads((replay_dir / 'baseline-state.json').read_bytes())
    replay_graph = json.loads((replay_dir / 'accepted-baseline-graphs.json').read_bytes())
    replay_receipt = json.loads((replay_dir / 'baseline-identity-rebinding-receipt.json').read_bytes())
    replay_report = json.loads((replay_dir / 'baseline-identity-rebinding-report.json').read_bytes())
    if (replay_state != v04_state
            or replay_graph.get('accepted_roots') != v04_graph.get('accepted_roots')
            or replayed['state_sha256'] != v04_state.get('state_sha256')
            or replayed['track_count'] != 3004
            or replayed['assignment_count'] != 515
            or replayed['directed_edge_count'] != 3341
            or replayed['sql_integrity_check'] != 'ok'
            or replay_receipt.get('source_bundle_file_sha256') != bundle_hashes
            or replay_receipt.get('recording_identity_verified') is not False
            or replay_receipt.get('recording_edges_added') != 0
            or replay_report.get('physical_bundle_integrity') != 'ok'):
        raise ValueError('Trusted native v04 baseline replay differs from its reviewed receipt')

    chain = verify_v04_to_v13_map_chain(
        repo_root=root, append_chain_receipt_path=append_chain_receipt_path,
        append_final_map_path=append_final_map_path)
    final_map_path = Path(append_final_map_path).resolve()
    final_map_bytes = final_map_path.read_bytes()
    final_map_hash = _sha(final_map_bytes)
    tracks, library_hash, _ = load_library(track_snapshot=paths['track_snapshot'])
    if len(tracks) != 3004 or library_hash != replay_state['source']['library_sha256']:
        raise ValueError('Native v13 snapshot does not match the exact 3,004-track Library')
    _, _, final_map = _read(final_map_path)
    parent_map = json.loads(paths['new_identity_map'].read_bytes())
    old_matcher = IdentityMap(parent_map, tracks, library_hash)
    new_matcher = IdentityMap(final_map, tracks, library_hash)
    _, old_labels = identity_source(old_matcher, library_hash, _sha(paths['new_identity_map'].read_bytes()))
    _, new_labels = identity_source(new_matcher, library_hash, final_map_hash)
    if old_labels != new_labels:
        raise ValueError('Approved native metadata aliases changed canonical Library identity labels')
    parent_graph_roots = v04_graph['accepted_roots']
    parent_state_rows = v04_state['rows']
    rebound_state = _reseal_map_hash(v04_state, final_map_hash)
    if plan_genius_update(rebound_state, parent_graph_roots, new_matcher,
                          library_hash, final_map_hash)['next_state'].get('state_sha256') != rebound_state.get('state_sha256'):
        raise ValueError('Native v13 state does not replay against its unchanged baseline roots')
    _assert_state_only_map_change(v04_state, rebound_state, final_map_hash)
    if len(parent_state_rows) != 515 or len(parent_graph_roots) != 515:
        raise ValueError('Native v04 parent shape differs from its 515-row receipt')
    edge_count = sum(len(row['ordered_target_pids']) for row in parent_graph_roots)
    if edge_count != 3341:
        raise ValueError('Native v04 parent edge count differs from its receipt')

    # Run current native SQL synchronization in memory; never write the source.
    prepared_files, sql_report, _ = update_bundle(
        bundle_files, rebound_state, parent_graph_roots, new_matcher,
        library_hash, final_map_hash, Path(executable_path).resolve(),
        allow_unverified_profile=True)
    changed_files = [name for name in sorted(bundle_files)
                     if prepared_files[name] != bundle_files[name]]
    if (set(changed_files) - {'Library.musicdb'}
            or sql_report.get('sql_integrity_check') != 'ok'
            or sql_report.get('changed_id_slots')
            or not sql_report.get('config_and_auxiliary_tables_preserved')
            or not sql_report.get('local_genius_state_preserved')):
        raise ValueError('Native v13 SQL synchronization changed source metadata or IDs')

    output_dir.mkdir(parents=True)
    state_out = output_dir / 'baseline-state.json'
    graph_out = output_dir / 'accepted-baseline-graphs.json'
    report_out = output_dir / 'baseline-rebase-report.json'
    write_json(state_out, rebound_state)
    original_rebase_report = json.loads(paths['source_rebase_report'].read_bytes())
    audit = deepcopy(original_rebase_report['relation_revalidation'])
    code_inputs = _code_receipts()
    report = {
        'schema_version': 1,
        'producer': PRODUCER,
        'zero_id_library_sha256': library_hash,
        'identity_map_sha256': final_map_hash,
        'next_state_sha256': rebound_state['state_sha256'],
        'relation_revalidation': audit,
        'native_baseline_alias_child': {
            'parent_native_v04_receipt': _receipt(v04_receipt_path),
            'parent_native_v04_report': _receipt(v04_report_path),
            'parent_native_v04_state': _receipt(v04_state_path),
            'parent_native_v04_graph': _receipt(v04_graph_path),
            'child_receipt_path': str((output_dir / 'native-baseline-alias-child-receipt.json').resolve()),
            'source_bundle_path': str(Path(source_bundle_path).resolve()),
            'source_bundle_file_sha256': bundle_hashes,
            'source_bundle_executable': _receipt(executable_path),
            'track_snapshot': _receipt(paths['track_snapshot']),
            'old_identity_map': _receipt(paths['old_identity_map']),
            'output_identity_map': _receipt(final_map_path),
            'map_chain': chain,
            'code_inputs': code_inputs,
            'preserved_track_count': 3004,
            'preserved_state_assignment_count': 515,
            'preserved_directed_edge_count': 3341,
            'pid_gid_assignments_unchanged': True,
            'metadata_bytes_unchanged': True,
            'ordered_relation_bytes_unchanged': True,
            'state_only_identity_map_hash_change': True,
            'physical_bundle_sql_check': sql_report,
            'prepared_library_changed_files': changed_files,
            'source_bundle_unchanged_on_disk': True,
            'recording_identity_verified': False,
            'recording_edges_added': 0,
            'network_requests': 0,
        },
        'recording_identity_verified': False,
        'recording_edges_added_by_rebinding': 0,
        'recording_edges_added': 0,
    }
    write_json(report_out, report)
    report_bytes = report_out.read_bytes()
    graph = {
        'schema_version': 1,
        'producer': 'verified-identity-map-rebound-state-graph-export',
        'source_state_path': str(state_out.resolve()),
        'source_state_sha256': _sha(state_out.read_bytes()),
        'source_identity_map_rebinding_report_sha256': _sha(report_bytes),
        'accepted_roots': parent_graph_roots,
        'identity_status': 'unverified_metadata_candidate',
    }
    graph_out_path = output_dir / 'accepted-baseline-graphs.json'
    write_json(graph_out_path, graph)
    report_doc = report
    code_rows = [_receipt(Path(item['path'])) for item in code_inputs]
    raw_rows = audit.get('raw_input_provenance') or []
    proof_rows = chain['proof_input_receipts'] + raw_rows + code_rows + [
        _receipt(v04_receipt_path), _receipt(v04_report_path), _receipt(v04_state_path),
        _receipt(v04_graph_path), _receipt(paths['source_baseline_state']),
        _receipt(paths['source_baseline_graph']), _receipt(paths['source_rebase_report']),
        _receipt(paths['track_snapshot']),
        *[_receipt(Path(source_bundle_path).resolve() / name) for name in bundle_hashes],
        _receipt(executable_path),
    ]
    proof_inputs = _manifest(proof_rows, workspace_root=root)
    receipt_out = output_dir / 'native-baseline-alias-child-receipt.json'
    receipt = {
        'schema_version': 1,
        'producer': PRODUCER,
        'parent_native_v04_receipt': _receipt(v04_receipt_path),
        'parent_native_v04_report': _receipt(v04_report_path),
        'parent_native_v04_state': _receipt(v04_state_path),
        'parent_native_v04_graph': _receipt(v04_graph_path),
        'source_bundle_path': str(Path(source_bundle_path).resolve()),
        'source_bundle_file_sha256': bundle_hashes,
        'track_snapshot': _receipt(paths['track_snapshot']),
        'old_identity_map': _receipt(paths['new_identity_map']),
        'new_identity_map': _receipt(final_map_path),
        'map_chain_proof_inputs': chain['proof_input_receipts'],
        'proof_input_receipts': proof_inputs,
        'code_inputs': code_inputs,
        'state': _receipt(state_out),
        'graph': _receipt(graph_out_path),
        'report': _receipt(report_out),
        'preserved_track_count': 3004,
        'preserved_state_assignment_count': 515,
        'preserved_directed_edge_count': 3341,
        'pid_gid_assignments_unchanged': True,
        'metadata_bytes_unchanged': True,
        'ordered_relation_bytes_unchanged': True,
        'recording_identity_verified': False,
        'recording_edges_added': 0,
        'network_requests': 0,
    }
    write_json(receipt_out, receipt)
    return {
        'output_dir': str(output_dir),
        'state_path': str(state_out), 'graph_path': str(graph_out_path),
        'report_path': str(report_out), 'receipt_path': str(receipt_out),
        'state_sha256': _sha(state_out.read_bytes()),
        'graph_sha256': _sha(graph_out_path.read_bytes()),
        'report_sha256': _sha(report_out.read_bytes()),
        'receipt_sha256': _sha(receipt_out.read_bytes()),
        'track_count': 3004, 'assignment_count': 515,
        'directed_edge_count': 3341, 'recording_edges_added': 0,
    }


def _native_v04_inputs(repo_root):
    root = Path(repo_root).resolve()
    baseline_dir = root / 'data/ytmusic/genius-baseline-515-v04-typed-rebind-prepared-v01'
    receipt_path = baseline_dir / 'baseline-identity-rebinding-receipt.json'
    report_path = baseline_dir / 'baseline-identity-rebinding-report.json'
    state_path = baseline_dir / 'baseline-state.json'
    graph_path = baseline_dir / 'accepted-baseline-graphs.json'
    original = root / 'data/new-library-rebased-20261009-v03'
    return {
        'baseline_dir': baseline_dir, 'receipt_path': receipt_path,
        'report_path': report_path, 'state_path': state_path, 'graph_path': graph_path,
        'bundle_path': original / 'Music Library.musiclibrary',
        'executable_path': Path('/mnt/data/mizoyuzu/Music.app/Contents/MacOS/Music'),
    }


def _replay_native_v04(repo_root, replay_dir):
    root = Path(repo_root).resolve()
    inputs = _native_v04_inputs(root)
    _, _, receipt = _read(inputs['receipt_path'])
    source_paths = {key: Path(receipt[key]['path']).resolve() for key in (
        'old_identity_map', 'new_identity_map', 'credit_intermediate_map',
        'credit_application_receipt', 'credit_review_decisions', 'credit_proposals',
        'title_application_receipt', 'title_review_decisions', 'title_proposals',
        'track_snapshot', 'source_baseline_state', 'source_baseline_graph', 'source_rebase_report')}
    result = rebind_baseline(
        bundle_path=inputs['bundle_path'], track_snapshot_path=source_paths['track_snapshot'],
        old_identity_map_path=source_paths['old_identity_map'],
        new_identity_map_path=source_paths['new_identity_map'],
        credit_intermediate_map_path=source_paths['credit_intermediate_map'],
        credit_application_receipt_path=source_paths['credit_application_receipt'],
        credit_review_path=source_paths['credit_review_decisions'],
        credit_proposals_path=source_paths['credit_proposals'],
        title_application_receipt_path=source_paths['title_application_receipt'],
        title_review_path=source_paths['title_review_decisions'],
        title_proposals_path=source_paths['title_proposals'],
        baseline_state_path=source_paths['source_baseline_state'],
        baseline_graph_path=source_paths['source_baseline_graph'],
        rebase_report_path=source_paths['source_rebase_report'],
        executable_path=inputs['executable_path'], output_dir=replay_dir)
    return inputs, receipt, source_paths, result


def verify_native_alias_child_report(*, graph_doc, report, state, library_hash,
                                     identity_hash, graph_path, report_path, state_path,
                                     repo_root):
    """Dedicated static dispatch verifier for this exact 515-PID native child."""
    if (not isinstance(report, dict) or report.get('producer') != PRODUCER
            or report.get('schema_version') != 1
            or report.get('recording_identity_verified') is not False
            or report.get('recording_edges_added') != 0
            or report.get('recording_edges_added_by_rebinding') != 0):
        raise ValueError('Unsupported or non-metadata native alias child report')
    binding = report.get('native_baseline_alias_child')
    if (not isinstance(binding, dict)
            or binding.get('preserved_track_count') != 3004
            or binding.get('preserved_state_assignment_count') != 515
            or binding.get('preserved_directed_edge_count') != 3341
            or binding.get('pid_gid_assignments_unchanged') is not True
            or binding.get('metadata_bytes_unchanged') is not True
            or binding.get('ordered_relation_bytes_unchanged') is not True
            or binding.get('state_only_identity_map_hash_change') is not True
            or binding.get('recording_identity_verified') is not False
            or binding.get('recording_edges_added') != 0
            or binding.get('network_requests') != 0):
        raise ValueError('Native alias child preservation contract is incomplete')
    root = Path(repo_root).resolve()
    inputs = _native_v04_inputs(root)
    v04_receipt_path, v04_receipt_bytes, v04_receipt = _read(inputs['receipt_path'])
    v04_report_path, v04_report_bytes, v04_report = _read(inputs['report_path'])
    v04_state_path, v04_state_bytes, v04_state = _read(inputs['state_path'])
    v04_graph_path, v04_graph_bytes, v04_graph = _read(inputs['graph_path'])
    if (_sha(v04_receipt_bytes) != EXPECTED_V04_RECEIPT_SHA256
            or binding.get('parent_native_v04_receipt') != _receipt(v04_receipt_path)
            or binding.get('parent_native_v04_report') != _receipt(v04_report_path)
            or binding.get('parent_native_v04_state') != _receipt(v04_state_path)
            or binding.get('parent_native_v04_graph') != _receipt(v04_graph_path)
            or Path(binding.get('source_bundle_path', '')).resolve() != inputs['bundle_path'].resolve()
            or binding.get('output_identity_map') != {'path': str(Path(binding['output_identity_map']['path']).resolve()),
                                                       'sha256': identity_hash}
            or identity_hash != EXPECTED_V13_MAP_SHA256
            or report.get('identity_map_sha256') != identity_hash
            or report.get('zero_id_library_sha256') != library_hash
            or report.get('next_state_sha256') != state.get('state_sha256')):
        raise ValueError('Native child does not bind exact v04 parent, Library, state, or v13 map')
    code_receipts = binding.get('code_inputs')
    if not isinstance(code_receipts, list) or code_receipts != _code_receipts():
        raise ValueError('Native child verifier/producers differ from the executing code')
    for item in code_receipts:
        path = Path(item.get('path', '')).resolve()
        if not path.is_file() or _sha(path.read_bytes()) != item.get('sha256'):
            raise ValueError('Native child code source changed')
    new_map_path = Path(binding['output_identity_map']['path']).resolve()
    if _sha(new_map_path.read_bytes()) != identity_hash:
        raise ValueError('Native child v13 map file changed')

    # Fast source/path checks before the trusted native writer is invoked.
    bundle_files = read_bundle(inputs['bundle_path'])
    bundle_hashes = {name: _sha(blob) for name, blob in sorted(bundle_files.items())}
    if (bundle_hashes != v04_receipt.get('source_bundle_file_sha256')
            or binding.get('source_bundle_file_sha256') != bundle_hashes
            or binding.get('source_bundle_executable') != _receipt(inputs['executable_path'])
            or binding.get('source_bundle_executable', {}).get('sha256')
               != EXPECTED_MUSIC_EXECUTABLE_SHA256):
        raise ValueError('Native bundle or SQL bridge differs from the pinned v04 source')
    if graph_doc.get('producer') != 'verified-identity-map-rebound-state-graph-export':
        raise ValueError('Native alias child must use the typed identity-map graph export')

    # Recreate v04 with the current trusted native consumer in a private temp
    # directory. Compare every stored source assignment and ordered relation.
    with tempfile.TemporaryDirectory(prefix='native-v04-replay-') as temp:
        replay_dir = Path(temp) / 'v04'
        replay_inputs, replay_receipt, source_paths, replay_result = _replay_native_v04(root, replay_dir)
        replay_state = json.loads((replay_dir / 'baseline-state.json').read_bytes())
        replay_graph = json.loads((replay_dir / 'accepted-baseline-graphs.json').read_bytes())
        replay_sql = json.loads((replay_dir / 'baseline-identity-rebinding-receipt.json').read_bytes())
        if (replay_state != v04_state
                or replay_graph.get('accepted_roots') != v04_graph.get('accepted_roots')
                or replay_result.get('sql_integrity_check') != 'ok'
                or replay_sql.get('source_bundle_file_sha256') != bundle_hashes
                or replay_result.get('track_count') != 3004
                or replay_result.get('assignment_count') != 515
                or replay_result.get('directed_edge_count') != 3341):
            raise ValueError('Trusted native consumer no longer reproduces the v04 parent')

    # Independently recompute each approved source-bound map transition.
    map_chain = binding.get('map_chain')
    chain_receipts = (map_chain.get('proof_input_receipts')
                      if isinstance(map_chain, dict) else None)
    append_receipts = [row for row in chain_receipts or []
                       if isinstance(row, dict)
                       and Path(str(row.get('path', ''))).name == 'v12-to-v13-chain-receipt.json']
    if len(append_receipts) != 1:
        raise ValueError('Native child must bind exactly one reviewed v12→v13 append-chain receipt')
    append_chain_receipt_path = Path(append_receipts[0]['path']).resolve()
    if (not append_chain_receipt_path.is_file()
            or _sha(append_chain_receipt_path.read_bytes()) != append_receipts[0].get('sha256')):
        raise ValueError('Native child append-chain receipt changed or is missing')
    chain = verify_v04_to_v13_map_chain(
        repo_root=root, append_chain_receipt_path=append_chain_receipt_path,
        append_final_map_path=new_map_path)
    if binding.get('map_chain') != chain:
        raise ValueError('Native child does not carry the complete verified alias-chain proof')
    parent_ids, parent_metadata, parent_relations = state_rows(v04_state)
    rebound = _reseal_map_hash(v04_state, identity_hash)
    _assert_state_only_map_change(v04_state, rebound, identity_hash)
    if state != rebound:
        raise ValueError('Native child state differs from exact map-only reseal of v04')
    tracks, replay_library_hash, _ = load_library(
        track_snapshot=source_paths['track_snapshot'])
    if replay_library_hash != library_hash:
        raise ValueError('Native alias child Library hash differs from the verified snapshot')
    final_map_doc = json.loads(new_map_path.read_bytes())
    new_matcher = IdentityMap(final_map_doc, tracks, library_hash)
    prepared_files, sql_report, _ = update_bundle(
        bundle_files, rebound, v04_graph['accepted_roots'], new_matcher,
        library_hash, identity_hash, inputs['executable_path'],
        allow_unverified_profile=True)
    changed_files = [name for name in sorted(bundle_files)
                     if prepared_files[name] != bundle_files[name]]
    if (set(changed_files) - {'Library.musicdb'}
            or sql_report.get('sql_integrity_check') != 'ok'
            or sql_report.get('changed_id_slots')
            or not sql_report.get('config_and_auxiliary_tables_preserved')
            or not sql_report.get('local_genius_state_preserved')
            or binding.get('prepared_library_changed_files') != changed_files
            or binding.get('physical_bundle_sql_check') != sql_report):
        raise ValueError('Native child does not reproduce the v13 SQL sync-only plan')
    if (len(v04_state.get('rows', [])) != 515
            or len(v04_graph.get('accepted_roots', [])) != 515
            or sum(len(row.get('ordered_target_pids', [])) for row in v04_graph['accepted_roots']) != 3341):
        raise ValueError('Native v04 parent rows/edges do not match exact baseline shape')
    expected_graph_roots = v04_graph['accepted_roots']
    if graph_doc.get('accepted_roots') != expected_graph_roots:
        raise ValueError('Native child graph changed a root, order, or edge')
    expected_audit = json.loads(source_paths['source_rebase_report'].read_bytes())['relation_revalidation']
    if report.get('relation_revalidation') != expected_audit:
        raise ValueError('Native child raw source receipts differ from the audited native baseline')
    child_receipt_path = Path(binding.get('child_receipt_path', '')).resolve()
    expected_child_receipt_path = Path(report_path).resolve().parent / 'native-baseline-alias-child-receipt.json'
    child_receipt_bytes = child_receipt_path.read_bytes()
    child_receipt = json.loads(child_receipt_bytes)
    if child_receipt_path != expected_child_receipt_path:
        raise ValueError('Native child output receipt set is incomplete or misbound')

    raw_receipts = expected_audit.get('raw_input_provenance', [])
    for item in code_receipts:
        path = Path(item.get('path', '')).resolve()
        if not path.is_file() or _sha(path.read_bytes()) != item.get('sha256'):
            raise ValueError('Native child code source changed')
    bundle_file_receipts = [_receipt(inputs['bundle_path'] / name) for name in bundle_hashes]
    proof_inputs = _manifest(chain['proof_input_receipts'] + raw_receipts + code_receipts + bundle_file_receipts + [
        _receipt(v04_receipt_path), _receipt(v04_report_path), _receipt(v04_state_path),
        _receipt(v04_graph_path), _receipt(source_paths['source_baseline_state']),
        _receipt(source_paths['source_baseline_graph']), _receipt(source_paths['source_rebase_report']),
        _receipt(source_paths['track_snapshot']), _receipt(inputs['executable_path'])],
        workspace_root=root)
    if child_receipt.get('proof_input_receipts') != proof_inputs:
        raise ValueError('Native child receipt omits or reorders an input in its source closure')
    if child_receipt.get('parent_native_v04_receipt') != _receipt(v04_receipt_path):
        raise ValueError('Native child receipt points to a different v04 parent')
    expected_child_receipt = {
        'schema_version': 1,
        'producer': PRODUCER,
        'parent_native_v04_receipt': _receipt(v04_receipt_path),
        'parent_native_v04_report': _receipt(v04_report_path),
        'parent_native_v04_state': _receipt(v04_state_path),
        'parent_native_v04_graph': _receipt(v04_graph_path),
        'source_bundle_path': str(inputs['bundle_path'].resolve()),
        'source_bundle_file_sha256': bundle_hashes,
        'track_snapshot': _receipt(source_paths['track_snapshot']),
        'old_identity_map': _receipt(source_paths['new_identity_map']),
        'new_identity_map': _receipt(new_map_path),
        'map_chain_proof_inputs': chain['proof_input_receipts'],
        'proof_input_receipts': proof_inputs,
        'code_inputs': code_receipts,
        'state': _receipt(state_path),
        'graph': _receipt(graph_path),
        'report': _receipt(report_path),
        'preserved_track_count': 3004,
        'preserved_state_assignment_count': 515,
        'preserved_directed_edge_count': 3341,
        'pid_gid_assignments_unchanged': True,
        'metadata_bytes_unchanged': True,
        'ordered_relation_bytes_unchanged': True,
        'recording_identity_verified': False,
        'recording_edges_added': 0,
        'network_requests': 0,
    }
    _assert_exact_child_receipt(child_receipt, expected_child_receipt)
    return {
        'producer': PRODUCER, 'verified': True, 'library_sha256': library_hash,
        'identity_map_sha256': identity_hash, 'track_count': 3004,
        'assignment_count': 515, 'directed_edge_count': 3341,
        'recording_identity_verified': False, 'recording_edges_added': 0,
        'proof_input_receipts': proof_inputs,
    }
