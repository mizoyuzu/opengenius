"""Create and verify offline typed growth rebinds through reviewed map deltas.

This module keeps the existing collector and merge implementation untouched.
Its report format is a staged candidate until a consumer explicitly recognizes
the new producer. A chain rebind changes only the typed state's map hash; the
album child appends the independently reviewed exact album alias.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from merge_rebased_radio_growth import (
    _relation_map, _replay_previous_growth, _state_graph_rows,
    _verify_rebase_receipt, sha, write_json,
)
from music_identity_map import IdentityMap, load_library
from music_identity_map import apply_artist_credit_alias_review
from plan_genius_update import _hash as state_hash, _source as identity_source, plan_genius_update
from rebind_radio_growth_identity import (
    _rebind_state_map_hash, verify_reviewed_album_alias_map_delta,
    verify_reviewed_identity_map_alias_chain, verify_title_alias_transition,
)
from update_genius_bundle import state_rows


CHAIN_PRODUCER = 'opengenius-typed-identity-map-alias-chain-growth-rebinding-v1'
ALBUM_CHILD_PRODUCER = 'opengenius-typed-album-alias-growth-rebinding-v1'
GRAPH_PRODUCER = 'verified-identity-map-rebound-state-graph-export'


def _read(path):
    path = Path(path).resolve()
    data = path.read_bytes()
    return path, data, json.loads(data)


def _receipt(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': sha(path.read_bytes())}


def staged_v04_to_v11_transitions(repo_root):
    """Return the exact six reviewed stage descriptors for the staged v11 map."""
    root = Path(repo_root).resolve()
    maps = root / 'data/new-library-rebased-20261009-v03/zero-id-source'

    def mp(name):
        return maps / name

    credit_receipt = mp('identity-map-aliases-v05-nine-credit-intermediate-staged-receipt.json')
    descriptors = [{
        'operation': 'apply_reviewed_whole_artist_credit_aliases',
        'output_identity_map_path': str(mp('identity-map-aliases-v05-nine-credit-intermediate-staged.json')),
        'receipt_path': str(credit_receipt),
        'proposals_path': str(root / 'data/artist-credit-alias-proposals-20261009-v09.json'),
        'review_decisions_path': str(root / 'data/artist-credit-alias-review-20261009-v06.json'),
    }]
    for stem in (
        'identity-map-aliases-v06-nine-credit-three-title-final-staged',
        'identity-map-aliases-v08-nine-credit-six-title-staged',
        'identity-map-aliases-v09-nine-credit-eight-title-staged',
        'identity-map-aliases-v10-nine-credit-eleven-title-staged',
        'identity-map-aliases-v11-nine-credit-thirty-title-staged',
    ):
        receipt_path = mp(stem + '-receipt.json')
        _, _, receipt = _read(receipt_path)
        descriptors.append({
            'operation': 'apply_reviewed_exact_title_spelling_aliases',
            'output_identity_map_path': str(mp(stem + '.json')),
            'receipt_path': str(receipt_path),
            'proposals_path': receipt['proposals_path'],
            'review_decisions_path': receipt['review_decisions_path'],
        })
    return descriptors


def staged_v11_to_v12_transition(repo_root):
    """Return the separate reviewed album alias child transition."""
    root = Path(repo_root).resolve()
    maps = root / 'data/new-library-rebased-20261009-v03/zero-id-source'
    receipt_path = maps / 'identity-map-aliases-v12-sanoba-album-alias-staged-receipt.json'
    _, _, receipt = _read(receipt_path)
    _, _, proposal = _read(receipt['proposals_path'])
    return {
        'input_identity_map_path': str(maps / 'identity-map-aliases-v11-nine-credit-thirty-title-staged.json'),
        'output_identity_map_path': str(maps / 'identity-map-aliases-v12-sanoba-album-alias-staged.json'),
        'receipt_path': str(receipt_path),
        'proposals_path': receipt['proposals_path'],
        'source_evidence_path': proposal['source_evidence']['path'],
        'candidate_audit_path': proposal['cache_audit']['path'],
        'review_decisions_path': receipt['review_decisions_path'],
    }


def _proof_input_receipts(rows):
    found = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('path'), str):
            raise ValueError('Malformed proof input receipt')
        path = Path(row['path']).resolve()
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != row.get('sha256'):
            raise ValueError('A proof input changed: ' + str(path))
        found[(str(path), digest)] = {'path': str(path), 'sha256': digest}
    return [found[key] for key in sorted(found)]


def _canonical_receipt_rows(rows):
    """Return the unique sorted file/hash closure, rejecting malformed rows."""
    canonical = _proof_input_receipts(rows)
    if not isinstance(rows, list):
        raise ValueError('Proof input receipts must be a list')
    # A receipt list is a set semantically, but the report uses one canonical
    # representation so omission, duplication, and arbitrary row order are
    # visible to the consumer verifier.
    if rows != canonical:
        raise ValueError('Proof input receipts are not the canonical complete set')
    return canonical



def _validate_state_graph_relation_projection(graph_roots, state_roots, label):
    """Require exact graph-root coverage plus state-only empty assignments."""
    graph = _relation_map(graph_roots, label + ' graph')
    state = _relation_map(state_roots, label + ' state')
    if set(graph) - set(state):
        raise ValueError(label + ' graph root is missing from complete state')
    if any(state[pid] != edges for pid, edges in graph.items()):
        raise ValueError(label + ' graph/state ordered relations differ')
    if any(edges for pid, edges in state.items() if pid not in graph):
        raise ValueError(label + ' state-only PID has outgoing edges')
    return graph, state

def _growth_source_receipts(growth_dir, normalized_runs):
    growth_dir = Path(growth_dir).resolve()
    receipts = [_receipt(growth_dir / 'accepted-graphs.json')]
    for run in normalized_runs:
        for key in ('collector_summary', 'seed_manifest'):
            ref = run.get(key) or {}
            if ref.get('path'):
                receipts.append(_receipt(ref['path']))
        evidence = (run.get('seed_manifest') or {}).get('seed_video_evidence') or {}
        if evidence.get('path'):
            receipts.append(_receipt(evidence['path']))
        for ref in run.get('snapshot_inputs', []):
            receipts.append(_receipt(ref['path']))
            radio_proof = ref.get('radio_only_source_proof') or {}
            if radio_proof.get('path'):
                receipts.append(_receipt(radio_proof['path']))
    _, _, accepted = _read(growth_dir / 'accepted-graphs.json')
    legacy_parent = (accepted.get('source_policy_transition') or {}).get('legacy_parent_growth') or {}
    if legacy_parent.get('path'):
        receipts.append({'path': legacy_parent['path'], 'sha256': legacy_parent['sha256']})
    return _proof_input_receipts(receipts)


def _load_saved_parent_growth(growth_dir, library_hash, identity_hash):
    """Load the frozen parent package without repeating its expensive replay.

    This is only candidate construction. The consumer verifier below still
    independently replays every run before accepting the typed rebind.
    """
    growth_dir = Path(growth_dir).resolve()
    source_graph_path, graph_bytes, source_graph = _read(growth_dir / 'accepted-graphs.json')
    source_state_path, state_bytes, source_state = _read(growth_dir / 'state.json')
    virtual_path, virtual_bytes, virtual = _read(growth_dir / 'virtual-update-audit.json')
    relations_path, relations_bytes, relations = _read(growth_dir / 'relations.json')
    evaluation_path, evaluation_bytes, evaluation = _read(growth_dir / 'accepted-evaluation.json')
    merge_path, merge_bytes, merge_audit = _read(growth_dir / 'merge-audit.json')
    inputs_path, inputs_bytes, inputs = _read(growth_dir / 'inputs.json')

    if (source_graph.get('producer') != 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1'
            or source_graph.get('library_sha256') != library_hash
            or source_graph.get('identity_map_sha256') != identity_hash
            or source_state.get('source', {}).get('library_sha256') != library_hash
            or source_state.get('source', {}).get('identity_map_sha256') != identity_hash
            or source_graph.get('baseline_rebase_provenance') != merge_audit.get('rebase_provenance')
            or source_graph.get('direct_parent_state_sha256') != merge_audit.get('baseline_state_sha256')
            or inputs.get('library_sha256') != library_hash
            or inputs.get('identity_map_sha256') != identity_hash):
        raise ValueError('Frozen parent growth package has inconsistent source/map bindings')
    state_seal = state_hash({key: value for key, value in source_state.items()
                             if key != 'state_sha256'})
    if state_seal != source_state.get('state_sha256'):
        raise ValueError('Frozen parent state seal is invalid')
    if (virtual.get('operation') != 'offline_synthetic_incremental_plan'
            or virtual.get('network_requests') != 0 or virtual.get('files_written') != 0
            or virtual.get('music_app_acceptance_verified') is not False
            or virtual.get('ipod_acceptance_verified') is not False
            or virtual.get('next_state') != source_state
            or virtual.get('next_state', {}).get('state_sha256') != state_seal):
        raise ValueError('Persisted virtual-update audit does not bind the exact saved state')

    roots = source_graph.get('accepted_roots')
    state_roots = _state_graph_rows(source_state)
    graph_relations = _relation_map(roots, 'Frozen parent graph')
    state_relations = _relation_map(state_roots, 'Frozen parent state')
    _validate_state_graph_relation_projection(roots, state_roots, 'Frozen parent')
    graph_nonempty = {pid: edges for pid, edges in graph_relations.items() if edges}
    state_nonempty = {pid: edges for pid, edges in state_relations.items() if edges}
    evaluation_relations = _relation_map(evaluation.get('relation_rows'), 'Frozen parent evaluation')
    persisted_relations = _relation_map(relations.get('relation_rows'), 'Frozen parent relations')
    if (graph_nonempty != state_nonempty
            or graph_relations != evaluation_relations
            or graph_relations != persisted_relations
            or evaluation.get('library_sha256') != library_hash
            or evaluation.get('virtual_state_sha256') != source_state.get('state_sha256')
            or merge_audit.get('next_state_sha256') != source_state.get('state_sha256')
            or merge_audit.get('accepted_root_count') != len(roots)
            or merge_audit.get('accepted_edge_count') != sum(len(row['ordered_target_pids']) for row in roots)
            or merge_audit.get('indexed_track_count') != len(source_state.get('rows', []))):
        raise ValueError('Frozen parent graph, evaluation, relations, and state disagree')
    if not isinstance(source_graph.get('growth_runs'), list) or not source_graph['growth_runs']:
        raise ValueError('Frozen parent growth-run package is incomplete')

    # Every source listed by the merged graph must still exist and match the
    # receipt embedded in that run. This is receipt validation, not replay.
    receipts = _growth_source_receipts(growth_dir, source_graph['growth_runs'])
    extra_receipts = _proof_input_receipts([
        {'path': str(source_state_path), 'sha256': sha(state_bytes)},
        {'path': str(virtual_path), 'sha256': sha(virtual_bytes)},
        {'path': str(relations_path), 'sha256': sha(relations_bytes)},
        {'path': str(evaluation_path), 'sha256': sha(evaluation_bytes)},
        {'path': str(merge_path), 'sha256': sha(merge_bytes)},
        {'path': str(inputs_path), 'sha256': sha(inputs_bytes)},
    ])
    receipts = _proof_input_receipts(receipts + extra_receipts)
    return {
        'path': growth_dir,
        'graph_path': source_graph_path,
        'graph_bytes': graph_bytes,
        'graph': source_graph,
        'state_path': source_state_path,
        'state_bytes': state_bytes,
        'state': source_state,
        'roots': roots,
        'state_roots': state_roots,
        'runs': source_graph['growth_runs'],
        'receipts': receipts,
        'state_sha256': state_seal,
        'accepted_graphs_sha256': sha(graph_bytes),
        'virtual_update_audit': {'path': str(virtual_path), 'sha256': sha(virtual_bytes)},
    }


def verify_v04_v11_identity_map_alias_chain(*, track_snapshot,
                                            input_identity_map_path,
                                            output_identity_map_path,
                                            transitions):
    """Verify the reviewed v04→v05 credit stage and v05→v11 title chain.

    The older generic credit-chain verifier expects a source-report schema that
    predates the approved v05 receipt. Here the v05 candidate audit itself is
    validated as the proposal's source report, including every cached input.
    """
    if len(transitions) != 6:
        raise ValueError('Expected one reviewed credit stage and five title stages')
    snapshot_path, snapshot_bytes, _ = _read(track_snapshot)
    tracks, library_hash, _ = load_library(track_snapshot=snapshot_path)
    start_path, start_bytes, start_map = _read(input_identity_map_path)
    final_path, final_bytes, final_map = _read(output_identity_map_path)
    credit = transitions[0]
    if credit.get('operation') != 'apply_reviewed_whole_artist_credit_aliases':
        raise ValueError('First alias-chain transition must be the approved credit delta')
    middle_path, middle_bytes, middle_map = _read(credit['output_identity_map_path'])
    receipt_path, receipt_bytes, receipt = _read(credit['receipt_path'])
    proposal_path, proposal_bytes, proposals = _read(credit['proposals_path'])
    review_path, review_bytes, review = _read(credit['review_decisions_path'])
    source_ref = proposals.get('source_report') or {}
    source_path, source_bytes, source = _read(source_ref.get('path', ''))
    if (start_map.get('library_sha256') != library_hash
            or final_map.get('library_sha256') != library_hash
            or receipt.get('operation') != credit['operation']
            or receipt.get('library_sha256') != library_hash
            or receipt.get('input_identity_map_sha256') != sha(start_bytes)
            or receipt.get('output_identity_map_sha256') != sha(middle_bytes)
            or receipt.get('proposal_sha256') != sha(proposal_bytes)
            or receipt.get('source_report_sha256') != sha(source_bytes)
            or receipt.get('review_decisions_sha256') != sha(review_bytes)
            or receipt.get('recording_identity_verified') is not False
            or source_ref.get('sha256') != sha(source_bytes)
            or source.get('library', {}).get('original_library_sha256') != library_hash
            or source.get('library', {}).get('track_snapshot_sha256') != sha(snapshot_bytes)
            or source.get('library', {}).get('identity_map_sha256') != sha(start_bytes)
            or Path(source.get('library', {}).get('track_snapshot_path', '')).resolve() != snapshot_path
            or Path(source.get('library', {}).get('identity_map_path', '')).resolve() != start_path
            or source.get('summary', {}).get('recording_identity_verified') != 0
            or source.get('summary', {}).get('edges_created') != 0
            or source.get('summary', {}).get('proposal_count') != len(proposals.get('proposals', []))
            or review.get('proposal_sha256') != sha(proposal_bytes)
            or review.get('source_report_sha256') != sha(source_bytes)
            or review.get('approval', {}).get('reviewed_by') != 'root'
            or review.get('approval', {}).get('recording_identity_verified') is not False
            or review.get('approval', {}).get('new_recording_edges_authorized') is not False
            or len(review.get('decisions', [])) != len(proposals.get('proposals', []))):
        raise ValueError('Reviewed whole-credit stage is not bound to its exact source and maps')
    input_rows = source.get('input_files')
    if (not isinstance(input_rows, list) or not input_rows
            or source.get('summary', {}).get('cache_input_count') != len(input_rows)):
        raise ValueError('Whole-credit candidate audit omits its cached input receipts')
    proof = {(str(snapshot_path), sha(snapshot_bytes)),
             (str(start_path), sha(start_bytes)), (str(final_path), sha(final_bytes)),
             (str(middle_path), sha(middle_bytes)), (str(receipt_path), sha(receipt_bytes)),
             (str(proposal_path), sha(proposal_bytes)), (str(review_path), sha(review_bytes)),
             (str(source_path), sha(source_bytes))}
    for ref in input_rows:
        path, data, _ = _read(ref['path'])
        if sha(data) != ref.get('sha256'):
            raise ValueError('A whole-credit candidate-audit input changed')
        proof.add((str(path), sha(data)))
    for proposal in proposals.get('proposals', []):
        if (proposal.get('status') != 'verified'
                or proposal.get('recording_identity_verified') is not False
                or proposal.get('new_edges_authorized') is not False):
            raise ValueError('Whole-credit proposal is not metadata-only reviewed input')
    expected_middle = apply_artist_credit_alias_review(
        start_map, review, proposals, tracks, library_hash, sha(start_bytes), sha(proposal_bytes))
    if expected_middle != middle_map:
        raise ValueError('Reviewed whole-credit application does not reproduce the staged map')
    title_chain = verify_reviewed_identity_map_alias_chain(
        track_snapshot=snapshot_path,
        input_identity_map_path=middle_path,
        output_identity_map_path=final_path,
        transitions=transitions[1:])
    if (title_chain.get('recording_identity_verified') is not False
            or title_chain.get('recording_edges_added') != 0
            or title_chain.get('input_identity_map', {}).get('sha256') != sha(middle_bytes)
            or title_chain.get('output_identity_map', {}).get('sha256') != sha(final_bytes)):
        raise ValueError('Reviewed title chain differs from the exact credit-stage output')
    proof.update((row['path'], row['sha256']) for row in title_chain['proof_input_receipts'])
    return {
        'input_identity_map': {'path': str(start_path), 'sha256': sha(start_bytes)},
        'output_identity_map': {'path': str(final_path), 'sha256': sha(final_bytes)},
        'library_sha256': library_hash,
        'recording_identity_verified': False,
        'recording_edges_added': 0,
        'transition_count': len(transitions),
        'transitions': [
            {'operation': row['operation'],
             'output_identity_map': {'path': str(Path(row['output_identity_map_path']).resolve()),
                                     'sha256': sha(Path(row['output_identity_map_path']).read_bytes())},
             'source_receipts': [
                 {'kind': kind, 'path': str(Path(path).resolve()),
                  'sha256': sha(Path(path).read_bytes())}
                 for kind, path in _transition_receipts(row)]}
            for row in transitions],
        'proof_input_receipts': [{'path': path, 'sha256': digest}
                                 for path, digest in sorted(proof)],
    }


def _transition_receipts(row):
    if row['operation'] == 'apply_reviewed_whole_artist_credit_aliases':
        return [('receipt', row['receipt_path']), ('proposal', row['proposals_path']),
                ('review', row['review_decisions_path'])]
    if row['operation'] == 'apply_reviewed_exact_title_spelling_aliases':
        _, _, receipt = _read(row['receipt_path'])
        return [('receipt', row['receipt_path']), ('proposal', receipt['proposals_path']),
                ('review', receipt['review_decisions_path']),
                ('source_report', receipt['source_report_path'])]
    return []


def rebind_growth_alias_chain(*, track_snapshot, input_identity_map,
                              output_identity_map, transitions,
                              baseline_state, baseline_graph_export,
                              rebase_report, growth_dir, output_dir):
    """Replay growth under the old map and write an append-only chain rebind."""
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError('Output directory exists; choose a new directory')
    snapshot_path, snapshot_bytes, _ = _read(track_snapshot)
    input_path, input_bytes, _ = _read(input_identity_map)
    output_path, output_bytes, _ = _read(output_identity_map)
    tracks, library_hash, _ = load_library(track_snapshot=snapshot_path)
    old_hash, new_hash = sha(input_bytes), sha(output_bytes)
    if not transitions:
        raise ValueError('A reviewed alias-chain transition is required')
    chain = verify_v04_v11_identity_map_alias_chain(
        track_snapshot=snapshot_path,
        input_identity_map_path=input_path,
        output_identity_map_path=output_path,
        transitions=transitions)
    if (chain.get('recording_identity_verified') is not False
            or chain.get('recording_edges_added') != 0
            or chain.get('input_identity_map', {}).get('sha256') != old_hash
            or chain.get('output_identity_map', {}).get('sha256') != new_hash
            or library_hash != chain.get('library_sha256')):
        raise ValueError('Reviewed alias-chain proof differs from its declared endpoints')

    old_matcher = IdentityMap(json.loads(input_bytes), tracks, library_hash)
    new_matcher = IdentityMap(json.loads(output_bytes), tracks, library_hash)
    _, old_labels = identity_source(old_matcher, library_hash, old_hash)
    _, new_labels = identity_source(new_matcher, library_hash, new_hash)
    if old_labels != new_labels:
        raise ValueError('Reviewed map chain changed local identity labels')

    base_graph, base_report, base_state, base_provenance = _verify_rebase_receipt(
        baseline_graph_export, rebase_report, baseline_state, library_hash, old_hash)
    base_roots = base_graph.get('accepted_roots')
    if _relation_map(base_roots, 'Typed baseline graph') != _relation_map(
            _state_graph_rows(base_state), 'Typed baseline state'):
        raise ValueError('Typed baseline graph differs from its state')
    if plan_genius_update(base_state, base_roots, old_matcher, library_hash,
                          old_hash)['next_state']['state_sha256'] != base_state['state_sha256']:
        raise ValueError('Typed baseline does not reproduce under the old map')

    # Candidate construction consumes the frozen, previously replay-validated
    # parent package. The consumer verifier below still performs a fresh full
    # replay under the old map before this candidate can be accepted.
    parent = _load_saved_parent_growth(growth_dir, library_hash, old_hash)
    growth_path = parent['path']
    source_graph = parent['graph']
    source_state = parent['state']
    source_state_path = parent['state_path']
    source_state_bytes = parent['state_bytes']
    roots = source_graph['accepted_roots']
    normalized_runs = parent['runs']
    accepted_hash = parent['accepted_graphs_sha256']
    inherited_source_policy = source_graph.get('source_policy')
    if inherited_source_policy not in (None, 'authenticated-watch-radio-only-v1'):
        raise ValueError('Parent growth has an unsupported source policy')
    old_state = source_state

    # State carries every assigned PID, including 56 with no accepted graph root.
    # Preserve the accepted graph's 691-row shape; validate all 747 state rows
    # separately and compare only edge-bearing state relations to the replay.
    state_roots = parent['state_roots']
    rebound_state = _rebind_state_map_hash(old_state, new_hash)
    if plan_genius_update(rebound_state, state_roots, new_matcher, library_hash,
                          new_hash)['next_state']['state_sha256'] != rebound_state['state_sha256']:
        raise ValueError('Rebound state does not reproduce under the reviewed final map')
    old_ids, _, old_relations = state_rows(old_state)
    new_ids, _, new_relations = state_rows(rebound_state)
    if (old_ids != new_ids or old_state['rows'] != rebound_state['rows']
            or old_relations != new_relations
            or {pid: edges for pid, edges in _relation_map(
                state_roots, 'Source state graph').items() if edges}
               != {pid: edges for pid, edges in _relation_map(
                   _state_graph_rows(rebound_state), 'Rebound state graph').items() if edges}):
        raise ValueError('Alias-chain rebind changed PID/GID assignments, metadata, or relations')

    baseline_audit = base_report['relation_revalidation']
    baseline_manifest = baseline_audit.get('accepted_input_manifest') or {}
    verified_raw = {(str(Path(row['path']).resolve()), row['sha256'])
                    for row in baseline_audit.get('raw_input_provenance', [])}
    authenticated = set()
    for pair in baseline_manifest.get('anchored_observation_inputs', []):
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError('Malformed baseline accepted-input manifest')
        item = (str(Path(pair[0]).resolve()), pair[1])
        if item not in verified_raw:
            raise ValueError('Baseline accepted observation lacks a verified raw receipt')
        authenticated.add(item)
    for run in normalized_runs:
        for ref in run.get('snapshot_inputs', []):
            authenticated.add((str(Path(ref['path']).resolve()), ref['sha256']))
    authenticated_receipts = [
        {'path': path, 'sha256': digest} for path, digest in sorted(authenticated)]

    output_dir.mkdir(parents=True)
    state_path = output_dir / 'state.json'
    report_path = output_dir / 'identity-map-rebinding-report.json'
    graph_path = output_dir / 'accepted-baseline-graphs.json'
    write_json(state_path, rebound_state)
    chain_receipts = _proof_input_receipts(chain['proof_input_receipts'])
    growth_receipts = parent['receipts']
    binding = {
        'schema_version': 1,
        'old_identity_map': {'path': str(input_path), 'sha256': old_hash},
        'new_identity_map': {'path': str(output_path), 'sha256': new_hash},
        'track_snapshot': {'path': str(snapshot_path), 'sha256': sha(snapshot_bytes)},
        'reviewed_alias_chain': chain,
        'source_rebase': {
            'graph_export_path': base_provenance['graph_export_path'],
            'graph_export_sha256': base_provenance['graph_export_sha256'],
            'rebase_report_path': base_provenance['rebase_report_path'],
            'rebase_report_sha256': base_provenance['rebase_report_sha256'],
            'state_path': base_provenance['state_path'],
            'state_file_sha256': base_provenance['state_file_sha256'],
        },
        'source_growth': {'path': str(growth_path), 'accepted_graphs_sha256': accepted_hash,
                          'state_path': str(source_state_path),
                          'state_file_sha256': sha(source_state_bytes),
                          'state_sha256': parent['state_sha256'],
                          'virtual_update_audit': parent['virtual_update_audit'],
                          'source_package_receipt_count': len(growth_receipts)},
        'old_state_sha256': old_state['state_sha256'],
        'new_state_sha256': rebound_state['state_sha256'],
        'growth_run_count_replayed_under_old_map': len(normalized_runs),
        'local_track_metadata_unchanged': True,
        'local_equivalence_labels_unchanged': True,
        'pid_gid_assignments_unchanged': True,
        'metadata_bytes_unchanged': True,
        'relation_bytes_unchanged': True,
        'old_growth_replayed_with_old_map': True,
        'activated_whole_credit_alias_count': sum(
            1 for row in json.loads(output_bytes).get('artist_credit_aliases', [])
            if row.get('enabled')) - sum(
            1 for row in json.loads(input_bytes).get('artist_credit_aliases', [])
            if row.get('enabled')),
        'activated_title_alias_count': sum(
            sum(bool(alias.get('enabled')) for alias in row.get('aliases', []))
            for row in json.loads(output_bytes).get('track_title_aliases', [])) - sum(
            sum(bool(alias.get('enabled')) for alias in row.get('aliases', []))
            for row in json.loads(input_bytes).get('track_title_aliases', [])),
        'recording_identity_verified': False,
        'recording_edges_added_by_rebinding': 0,
        'proof_input_receipts': _proof_input_receipts(chain_receipts + growth_receipts),
        'authenticated_observation_inputs': authenticated_receipts,
        'authenticated_observation_input_count': len(authenticated_receipts),
        'accepted_input_manifest_sha256': base_provenance['accepted_input_manifest_sha256'],
        **({'source_policy': inherited_source_policy}
           if inherited_source_policy is not None else {}),
    }
    relation_audit = deepcopy(baseline_audit)
    relation_audit['output_directed_edge_count'] = sum(
        len(row['ordered_target_pids']) for row in roots)
    report = {
        'schema_version': 1,
        'producer': CHAIN_PRODUCER,
        'zero_id_library_sha256': library_hash,
        'identity_map_sha256': new_hash,
        'next_state_sha256': rebound_state['state_sha256'],
        'indexed_tracks': len(new_ids),
        'source_state_path': str(state_path.resolve()),
        'identity_map_rebinding': binding,
        'relation_revalidation': relation_audit,
        'raw_input_provenance': relation_audit['raw_input_provenance'],
        'accepted_root_count': sum(bool(row['ordered_target_pids']) for row in roots),
        'directed_edge_count': sum(len(row['ordered_target_pids']) for row in roots),
        **({'source_policy': inherited_source_policy}
           if inherited_source_policy is not None else {}),
    }
    write_json(report_path, report)
    output_graph = {
        'schema_version': 1,
        'producer': GRAPH_PRODUCER,
        'source_state_path': str(state_path.resolve()),
        'source_state_sha256': sha(state_path.read_bytes()),
        'source_identity_map_rebinding_report_sha256': sha(report_path.read_bytes()),
        'accepted_roots': roots,
        'identity_status': 'unverified_metadata_candidate',
    }
    if inherited_source_policy is not None:
        output_graph['source_policy'] = inherited_source_policy
    write_json(graph_path, output_graph)
    return {'output_dir': str(output_dir), 'report_path': str(report_path),
            'graph_path': str(graph_path), 'state_path': str(state_path),
            'identity_map_sha256': new_hash,
            'state_sha256': rebound_state['state_sha256'],
            'preserved_tracks': len(new_ids),
            'preserved_directed_edges': report['directed_edge_count'],
            'accepted_root_count': report['accepted_root_count'],
            'recording_identity_verified': False, 'recording_edges_added': 0}


def verify_alias_chain_growth_rebinding_report(*, graph_doc, report, state,
                                               library_hash, identity_hash,
                                               state_path):
    """Reproduce a chain rebind's receipts, old growth replay, and invariants."""
    binding = report.get('identity_map_rebinding')
    has_policy = 'source_policy' in report
    policy = report.get('source_policy')
    if (has_policy != ('source_policy' in graph_doc)
            or not isinstance(binding, dict)
            or has_policy != ('source_policy' in binding)
            or policy not in (None, 'authenticated-watch-radio-only-v1')
            or (has_policy and (graph_doc.get('source_policy') != policy
                                or binding.get('source_policy') != policy))):
        raise ValueError('Alias-chain report/graph source policy is missing or inconsistent')
    if (report.get('producer') != CHAIN_PRODUCER or not isinstance(binding, dict)
            or binding.get('schema_version') != 1
            or binding.get('new_identity_map', {}).get('sha256') != identity_hash
            or report.get('identity_map_sha256') != identity_hash
            or report.get('zero_id_library_sha256') != library_hash
            or any(binding.get(key) is not True for key in (
                'local_track_metadata_unchanged', 'local_equivalence_labels_unchanged',
                'pid_gid_assignments_unchanged', 'metadata_bytes_unchanged',
                'relation_bytes_unchanged', 'old_growth_replayed_with_old_map'))
            or binding.get('recording_identity_verified') is not False
            or binding.get('recording_edges_added_by_rebinding') != 0):
        raise ValueError('Alias-chain growth rebind proof is incomplete')

    def checked(ref, label):
        if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
            raise ValueError('Malformed ' + label + ' receipt')
        path, data, _ = _read(ref['path'])
        if sha(data) != ref['sha256']:
            raise ValueError(label + ' receipt changed')
        return path, data

    snapshot_path, snapshot_bytes = checked(binding['track_snapshot'], 'track snapshot')
    old_map_path, old_map_bytes = checked(binding['old_identity_map'], 'old map')
    new_map_path, new_map_bytes = checked(binding['new_identity_map'], 'new map')
    if (new_map_path != Path(binding['reviewed_alias_chain']['output_identity_map']['path']).resolve()
            or sha(new_map_bytes) != identity_hash):
        raise ValueError('Alias chain does not terminate at the current identity map')
    descriptors = binding['reviewed_alias_chain'].get('transitions')
    if not isinstance(descriptors, list):
        raise ValueError('Alias-chain proof omits ordered transitions')
    # The chain report contains source receipts by transition. Rebuild descriptors
    # from those exact receipts so verifier inputs cannot be swapped silently.
    transitions = []
    for row in descriptors:
        refs = {item.get('kind'): item.get('path') for item in row.get('source_receipts', [])}
        operation = row.get('operation')
        output_ref = row.get('output_identity_map') or {}
        if operation == 'apply_reviewed_whole_artist_credit_aliases':
            proposal = refs.get('proposal')
            receipt = refs.get('receipt')
            review = refs.get('review')
            transitions.append({'operation': operation,
                                'output_identity_map_path': output_ref.get('path'),
                                'receipt_path': receipt, 'proposals_path': proposal,
                                'review_decisions_path': review})
        elif operation == 'apply_reviewed_exact_title_spelling_aliases':
            transitions.append({'operation': operation,
                                'output_identity_map_path': output_ref.get('path'),
                                'receipt_path': refs.get('receipt'),
                                'proposals_path': refs.get('proposal'),
                                'review_decisions_path': refs.get('review')})
        elif operation == 'apply_root_reviewed_exact_whole_album_metadata_alias':
            transitions.append({'operation': operation,
                                'output_identity_map_path': output_ref.get('path'),
                                'receipt_path': refs.get('receipt'),
                                'proposals_path': refs.get('proposal'),
                                'source_evidence_path': refs.get('source_evidence'),
                                'candidate_audit_path': refs.get('candidate_audit'),
                                'review_decisions_path': refs.get('review')})
        else:
            raise ValueError('Unsupported operation in reviewed alias chain')
    chain = verify_v04_v11_identity_map_alias_chain(
        track_snapshot=snapshot_path, input_identity_map_path=old_map_path,
        output_identity_map_path=new_map_path, transitions=transitions)
    if chain != binding['reviewed_alias_chain']:
        raise ValueError('Reviewed alias chain differs from its full source replay')
    tracks, loaded_hash, _ = load_library(track_snapshot=snapshot_path)
    if loaded_hash != library_hash:
        raise ValueError('Alias-chain track snapshot belongs to another Library')
    old_matcher = IdentityMap(json.loads(old_map_bytes), tracks, library_hash)
    new_matcher = IdentityMap(json.loads(new_map_bytes), tracks, library_hash)
    _, old_labels = identity_source(old_matcher, library_hash, sha(old_map_bytes))
    _, new_labels = identity_source(new_matcher, library_hash, identity_hash)
    if old_labels != new_labels:
        raise ValueError('Alias-chain rebind changed local identity labels')

    baseline = binding.get('source_rebase')
    baseline_graph, base_report, base_state, provenance = _verify_rebase_receipt(
        baseline['graph_export_path'], baseline['rebase_report_path'],
        baseline['state_path'], library_hash, sha(old_map_bytes))
    for key, path_key in (('graph_export_sha256', 'graph_export_path'),
                          ('rebase_report_sha256', 'rebase_report_path'),
                          ('state_file_sha256', 'state_path')):
        if sha(Path(baseline[path_key]).read_bytes()) != baseline[key]:
            raise ValueError('Alias-chain original typed baseline changed')
    if _relation_map(baseline_graph['accepted_roots'], 'Baseline graph') != _relation_map(
            _state_graph_rows(base_state), 'Baseline state'):
        raise ValueError('Alias-chain typed baseline graph/state mismatch')
    growth = binding.get('source_growth')
    growth_dir = Path(growth.get('path', '')).resolve()
    saved_parent = _load_saved_parent_growth(growth_dir, library_hash, sha(old_map_bytes))
    if (saved_parent['accepted_graphs_sha256'] != growth.get('accepted_graphs_sha256')
            or str(saved_parent['state_path']) != str(Path(growth.get('state_path', '')).resolve())
            or sha(saved_parent['state_bytes']) != growth.get('state_file_sha256')
            or saved_parent['state_sha256'] != growth.get('state_sha256')
            or saved_parent['virtual_update_audit'] != growth.get('virtual_update_audit')
            or len(saved_parent['receipts']) != growth.get('source_package_receipt_count')):
        raise ValueError('Alias-chain parent saved-state/audit receipt differs from its binding')
    old_state, replay_roots, normalized, graph_hash = _replay_previous_growth(
        growth_dir, base_state, baseline_graph['accepted_roots'], provenance,
        old_matcher, library_hash, sha(old_map_bytes))
    if (graph_hash != growth.get('accepted_graphs_sha256')
            or old_state.get('state_sha256') != binding.get('old_state_sha256')
            or old_state.get('state_sha256') != saved_parent['state_sha256']):
        raise ValueError('Alias-chain parent growth does not replay under its old map')
    inherited_source_policy = saved_parent['graph'].get('source_policy')
    if (inherited_source_policy not in (None, 'authenticated-watch-radio-only-v1')
            or has_policy != (inherited_source_policy is not None)
            or (has_policy and policy != inherited_source_policy)):
        raise ValueError('Alias-chain source policy does not inherit exact parent policy')
    roots = saved_parent['roots']
    # Preserve the complete, receipt-bound saved parent state. The independent
    # replay above verifies its state seal and ordered relations; use that
    # authoritative 747-row document for the exact map-hash-only transition,
    # rather than rebuilding state from graph-shaped intermediate rows.
    source_state = saved_parent['state']
    if old_state.get('state_sha256') != source_state.get('state_sha256'):
        raise ValueError('Alias-chain replay state seal differs from complete saved state')
    state_roots = _state_graph_rows(source_state)
    _validate_state_graph_relation_projection(roots, state_roots, 'Replayed alias parent')
    expected = _rebind_state_map_hash(source_state, identity_hash)
    if (state != expected
            or report.get('next_state_sha256') != state.get('state_sha256')
            or len(state.get('rows', [])) != len(source_state.get('rows', []))):
        raise ValueError('Alias-chain state changed complete assignments, metadata, relations, or map hash')
    report_file = Path(report.get('source_state_path', '')).resolve().parent / 'identity-map-rebinding-report.json'
    if (Path(report.get('source_state_path', '')).resolve() != Path(state_path).resolve()
            or graph_doc.get('source_state_sha256') != sha(Path(state_path).read_bytes())
            or not report_file.is_file()
            or graph_doc.get('source_identity_map_rebinding_report_sha256') != sha(report_file.read_bytes())
            or json.loads(report_file.read_bytes()) != report):
        raise ValueError('Alias-chain graph does not bind its exact state and report files')
    if plan_genius_update(expected, state_roots, new_matcher, library_hash,
                          identity_hash)['next_state']['state_sha256'] != state['state_sha256']:
        raise ValueError('Alias-chain state does not reproduce under the final map')
    graph_roots = _relation_map(graph_doc.get('accepted_roots'), 'Rebound graph')
    replay_relation_map = _relation_map(replay_roots, 'Replayed graph')
    source_roots = saved_parent['roots']
    expected_active_roots = [row for row in source_roots
                              if row.get('ordered_target_pids')]
    if (graph_doc.get('accepted_roots') != source_roots
            or report.get('accepted_root_count') != len(expected_active_roots)
            or report.get('directed_edge_count') != sum(
                len(row['ordered_target_pids']) for row in expected_active_roots)):
        raise ValueError('Alias-chain rebound graph root rows/order/counts changed')
    if ({pid: edges for pid, edges in graph_roots.items() if edges}
            != {pid: edges for pid, edges in replay_relation_map.items() if edges}):
        raise ValueError('Alias-chain rebound graph relations changed')

    # Compare the exact complete closure expected from the independently
    # replayed alias chain and saved growth package, not just a count or the
    # subset of receipts that happened to remain in the report.
    expected_receipts = _proof_input_receipts(
        chain['proof_input_receipts'] + saved_parent['receipts'])
    receipt_set = _canonical_receipt_rows(binding.get('proof_input_receipts'))
    if receipt_set != expected_receipts:
        raise ValueError('Alias-chain report omits or changes required source receipts')
    return receipt_set


def rebind_album_alias_child(*, parent_graph_path, parent_report_path,
                             parent_state_path, input_identity_map,
                             output_identity_map, track_snapshot,
                             proposals_path, source_evidence_path,
                             candidate_audit_path, review_decisions_path,
                             application_receipt_path, output_dir):
    """Append the exact reviewed album alias as a separately typed child."""
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError('Output directory exists; choose a new directory')
    graph_path, graph_bytes, graph = _read(parent_graph_path)
    report_path, report_bytes, report = _read(parent_report_path)
    parent_state_path, parent_state_bytes, parent_state = _read(parent_state_path)
    binding = report.get('identity_map_rebinding') or {}
    old_ref = binding.get('old_identity_map') or {}
    new_ref = binding.get('new_identity_map') or {}
    old_path, old_bytes, old_map = _read(old_ref.get('path'))
    input_path, input_bytes, input_map = _read(input_identity_map)
    output_path, output_bytes, output_map = _read(output_identity_map)
    if (input_path != Path(new_ref.get('path', '')).resolve()
            or sha(input_bytes) != new_ref.get('sha256')
            or graph.get('source_state_sha256') != sha(parent_state_bytes)
            or graph.get('source_identity_map_rebinding_report_sha256') != sha(report_bytes)):
        raise ValueError('Album child does not continue the exact typed chain-rebind parent')
    album_proof = verify_reviewed_album_alias_map_delta(
        track_snapshot=track_snapshot, input_identity_map_path=input_path,
        output_identity_map_path=output_path, proposals_path=proposals_path,
        source_evidence_path=source_evidence_path,
        candidate_audit_path=candidate_audit_path,
        review_decisions_path=review_decisions_path,
        receipt_path=application_receipt_path)
    if (album_proof['input_identity_map']['sha256'] != sha(input_bytes)
            or album_proof['output_identity_map']['sha256'] != sha(output_bytes)
            or album_proof['recording_identity_verified'] is not False
            or album_proof['recording_edges_added'] != 0):
        raise ValueError('Album alias child proof does not bind its exact parent/final maps')
    tracks, library_hash, _ = load_library(track_snapshot=Path(track_snapshot))
    _, old_labels = identity_source(IdentityMap(old_map, tracks, library_hash), library_hash,
                                    sha(old_bytes))
    _, input_labels = identity_source(IdentityMap(input_map, tracks, library_hash), library_hash,
                                      sha(input_bytes))
    _, output_labels = identity_source(IdentityMap(output_map, tracks, library_hash), library_hash,
                                       sha(output_bytes))
    if old_labels != input_labels or input_labels != output_labels:
        raise ValueError('Album alias child changed local identity labels')
    parent_roots = graph.get('accepted_roots')
    parent_state_roots = _state_graph_rows(parent_state)
    graph_edges = {pid: edges for pid, edges in
                   _relation_map(parent_roots, 'Parent graph').items() if edges}
    state_edges = {pid: edges for pid, edges in
                   _relation_map(parent_state_roots, 'Parent state').items() if edges}
    if graph_edges != state_edges:
        raise ValueError('Album alias parent graph differs from parent state')
    child_state = _rebind_state_map_hash(parent_state, sha(output_bytes))
    if (parent_state['rows'] != child_state['rows']
            or _relation_map(_state_graph_rows(child_state), 'Album child state')
            != _relation_map(parent_state_roots, 'Parent roots')
            or plan_genius_update(child_state, parent_state_roots,
                                  IdentityMap(output_map, tracks, library_hash),
                                  library_hash, sha(output_bytes))['next_state']['state_sha256']
            != child_state['state_sha256']):
        raise ValueError('Album alias child changed rows/relations or fails state reproduction')

    output_dir.mkdir(parents=True)
    state_path = output_dir / 'state.json'
    report_out = output_dir / 'identity-map-rebinding-report.json'
    graph_out = output_dir / 'accepted-baseline-graphs.json'
    write_json(state_path, child_state)
    parent_binding = report['identity_map_rebinding']
    child_binding = {
        'schema_version': 1,
        'parent_typed_growth': {
            'graph_export': _receipt(graph_path),
            'rebase_report': _receipt(report_path),
            'state': _receipt(parent_state_path),
            'old_identity_map': _receipt(old_path),
            'new_identity_map': _receipt(input_path),
        },
        'album_alias_transition': album_proof,
        'output_identity_map': album_proof['output_identity_map'],
        'track_snapshot': album_proof['track_snapshot'],
        'old_state_sha256': parent_state['state_sha256'],
        'new_state_sha256': child_state['state_sha256'],
        'accepted_input_manifest_sha256': parent_binding.get('accepted_input_manifest_sha256'),
        'authenticated_observation_inputs': parent_binding.get('authenticated_observation_inputs'),
        'authenticated_observation_input_count': parent_binding.get('authenticated_observation_input_count'),
        'local_track_metadata_unchanged': True,
        'local_equivalence_labels_unchanged': True,
        'pid_gid_assignments_unchanged': True,
        'metadata_bytes_unchanged': True,
        'relation_bytes_unchanged': True,
        'old_growth_replayed_with_parent_map': True,
        'recording_identity_verified': False,
        'recording_edges_added_by_rebinding': 0,
    }
    child_report = {
        'schema_version': 1,
        'producer': ALBUM_CHILD_PRODUCER,
        'zero_id_library_sha256': library_hash,
        'identity_map_sha256': sha(output_bytes),
        'next_state_sha256': child_state['state_sha256'],
        'indexed_tracks': len(state_rows(child_state)[0]),
        'source_state_path': str(state_path.resolve()),
        'album_alias_growth_rebinding': child_binding,
        'relation_revalidation': deepcopy(report['relation_revalidation']),
        'raw_input_provenance': deepcopy(report['raw_input_provenance']),
        'accepted_root_count': report.get('accepted_root_count'),
        'directed_edge_count': report.get('directed_edge_count'),
    }
    write_json(report_out, child_report)
    write_json(graph_out, {
        'schema_version': 1,
        'producer': GRAPH_PRODUCER,
        'source_state_path': str(state_path.resolve()),
        'source_state_sha256': sha(state_path.read_bytes()),
        'source_identity_map_rebinding_report_sha256': sha(report_out.read_bytes()),
        'accepted_roots': parent_roots,
        'identity_status': 'unverified_metadata_candidate',
    })
    return {'output_dir': str(output_dir), 'report_path': str(report_out),
            'graph_path': str(graph_out), 'state_path': str(state_path),
            'identity_map_sha256': sha(output_bytes), 'state_sha256': child_state['state_sha256'],
            'preserved_tracks': len(state_rows(child_state)[0]),
            'preserved_directed_edges': child_report['directed_edge_count'],
            'accepted_root_count': child_report['accepted_root_count'],
            'recording_identity_verified': False, 'recording_edges_added': 0}



ALBUM_CHAIN_CHILD_PRODUCER = 'opengenius-typed-album-alias-chain-growth-rebinding-v1'


def _verify_album_delta_chain(*, track_snapshot, start_map_path, final_map_path,
                              transitions):
    """Replay an ordered series of separately reviewed album-only map deltas."""
    if not isinstance(transitions, list) or len(transitions) < 1:
        raise ValueError('Album delta chain requires at least one reviewed transition')
    current = Path(start_map_path).resolve()
    proof_rows = []
    for descriptor in transitions:
        if not isinstance(descriptor, dict) or set(descriptor) != {
                'input_identity_map_path', 'output_identity_map_path',
                'proposals_path', 'source_evidence_path', 'candidate_audit_path',
                'review_decisions_path', 'receipt_path'}:
            raise ValueError('Malformed album delta chain descriptor')
        input_path = Path(descriptor['input_identity_map_path']).resolve()
        output_path = Path(descriptor['output_identity_map_path']).resolve()
        if input_path != current or output_path == current:
            raise ValueError('Album delta chain is reordered, skipped, or cyclic')
        proof = verify_reviewed_album_alias_map_delta(
            track_snapshot=track_snapshot,
            input_identity_map_path=input_path,
            output_identity_map_path=output_path,
            proposals_path=descriptor['proposals_path'],
            source_evidence_path=descriptor['source_evidence_path'],
            candidate_audit_path=descriptor['candidate_audit_path'],
            review_decisions_path=descriptor['review_decisions_path'],
            receipt_path=descriptor['receipt_path'])
        if (proof.get('input_identity_map', {}).get('path') != str(input_path)
                or proof.get('output_identity_map', {}).get('path') != str(output_path)
                or proof.get('recording_identity_verified') is not False
                or proof.get('recording_edges_added') != 0):
            raise ValueError('Album delta proof does not bind its exact ordered maps')
        proof_rows.append(proof)
        current = output_path
    if current != Path(final_map_path).resolve():
        raise ValueError('Album delta chain does not terminate at its declared output map')
    return proof_rows


def rebind_album_alias_chain_child(*, parent_graph_path, parent_report_path,
                                   parent_state_path, input_identity_map,
                                   output_identity_map, track_snapshot,
                                   transitions, output_dir):
    """Rebind one frozen typed growth graph through ordered album-only deltas."""
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError('Output directory exists; choose a new directory')
    graph_path, graph_bytes, graph = _read(parent_graph_path)
    report_path, report_bytes, report = _read(parent_report_path)
    state_path, state_bytes, state = _read(parent_state_path)
    binding = report.get('identity_map_rebinding') or {}
    parent_map = binding.get('new_identity_map') or {}
    parent_map_path, parent_map_bytes, _ = _read(parent_map.get('path'))
    start_path, start_bytes, _ = _read(input_identity_map)
    final_path, final_bytes, final_map = _read(output_identity_map)
    snapshot_path, snapshot_bytes, _ = _read(track_snapshot)
    if (report.get('producer') != CHAIN_PRODUCER
            or parent_map_path != start_path
            or parent_map.get('sha256') != sha(start_map_bytes := start_bytes)
            or graph.get('source_state_sha256') != sha(state_bytes)
            or graph.get('source_identity_map_rebinding_report_sha256') != sha(report_bytes)):
        raise ValueError('Album chain child does not continue the exact alias-chain parent')
    inherited_source_policy = graph.get('source_policy')
    if (inherited_source_policy not in (None, 'authenticated-watch-radio-only-v1')
            or ('source_policy' in report) != (inherited_source_policy is not None)
            or ('source_policy' in report
                and report.get('source_policy') != inherited_source_policy)):
        raise ValueError('Album chain parent source policy is missing or inconsistent')
    parent_proof = verify_alias_chain_growth_rebinding_report(
        graph_doc=graph, report=report, state=state,
        library_hash=report.get('zero_id_library_sha256'),
        identity_hash=sha(start_bytes), state_path=state_path)
    tracks, library_hash, _ = load_library(track_snapshot=snapshot_path)
    if library_hash != report.get('zero_id_library_sha256'):
        raise ValueError('Album chain child snapshot belongs to a different Library')
    delta_proofs = _verify_album_delta_chain(
        track_snapshot=snapshot_path, start_map_path=start_path,
        final_map_path=final_path, transitions=transitions)
    parent_roots = graph.get('accepted_roots')
    if not isinstance(parent_roots, list):
        raise ValueError('Album chain parent graph has no ordered root rows')
    parent_edges, _ = _validate_state_graph_relation_projection(
        parent_roots, _state_graph_rows(state), 'Album chain parent')
    child_state = _rebind_state_map_hash(state, sha(final_bytes))
    output_matcher = IdentityMap(final_map, tracks, library_hash)
    child_edges, _ = _validate_state_graph_relation_projection(
        parent_roots, _state_graph_rows(child_state), 'Album chain child')
    if (state['rows'] != child_state['rows']
            or child_edges != parent_edges
            or plan_genius_update(child_state, parent_roots, output_matcher,
                                  library_hash, sha(final_bytes))['next_state']['state_sha256']
            != child_state['state_sha256']):
        raise ValueError('Album chain changed local metadata, assignments, or graph relations')

    output_dir.mkdir(parents=True)
    child_state_path = output_dir / 'state.json'
    child_report_path = output_dir / 'identity-map-rebinding-report.json'
    child_graph_path = output_dir / 'accepted-baseline-graphs.json'
    write_json(child_state_path, child_state)
    parent_binding = {
        'graph_export': _receipt(graph_path), 'rebase_report': _receipt(report_path),
        'state': _receipt(state_path),
        'old_identity_map': _receipt(parent_map_path),
        'new_identity_map': _receipt(start_path),
    }
    transition_inputs = []
    for proof in delta_proofs:
        transition_inputs.extend(proof[key] for key in (
            'track_snapshot', 'input_identity_map', 'output_identity_map',
            'application_receipt', 'proposals', 'source_evidence',
            'candidate_audit', 'review_decisions'))
    source_receipts = _proof_input_receipts(
        parent_proof + transition_inputs + [
            _receipt(graph_path), _receipt(report_path), _receipt(state_path),
            _receipt(parent_map_path), _receipt(start_path),
            _receipt(snapshot_path), _receipt(final_path), _receipt(child_state_path)])
    child_binding = {
        'schema_version': 1, 'parent_typed_growth': parent_binding,
        **({'source_policy': inherited_source_policy}
           if inherited_source_policy is not None else {}),
        'track_snapshot': _receipt(snapshot_path),
        'input_identity_map': _receipt(start_path),
        'output_identity_map': _receipt(final_path),
        'album_alias_transitions': delta_proofs,
        'old_state_sha256': state.get('state_sha256'),
        'new_state_sha256': child_state['state_sha256'],
        'local_track_metadata_unchanged': True,
        'local_equivalence_labels_unchanged': True,
        'pid_gid_assignments_unchanged': True,
        'metadata_bytes_unchanged': True,
        'relation_bytes_unchanged': True,
        'old_growth_replayed_with_parent_map': True,
        'recording_identity_verified': False,
        'recording_edges_added_by_rebinding': 0,
        'proof_input_receipts': source_receipts,
    }
    child_report = {
        'schema_version': 1, 'producer': ALBUM_CHAIN_CHILD_PRODUCER,
        'zero_id_library_sha256': library_hash,
        'identity_map_sha256': sha(final_bytes),
        'next_state_sha256': child_state['state_sha256'],
        'indexed_tracks': len(state_rows(child_state)[0]),
        'source_state_path': str(child_state_path.resolve()),
        'album_alias_chain_growth_rebinding': child_binding,
        'relation_revalidation': deepcopy(report['relation_revalidation']),
        'raw_input_provenance': deepcopy(report['raw_input_provenance']),
        'accepted_root_count': report.get('accepted_root_count'),
        'directed_edge_count': report.get('directed_edge_count'),
        **({'source_policy': inherited_source_policy}
           if inherited_source_policy is not None else {}),
    }
    write_json(child_report_path, child_report)
    child_graph = {
        'schema_version': 1, 'producer': GRAPH_PRODUCER,
        'source_state_path': str(child_state_path.resolve()),
        'source_state_sha256': sha(child_state_path.read_bytes()),
        'source_identity_map_rebinding_report_sha256': sha(child_report_path.read_bytes()),
        'accepted_roots': parent_roots,
        'identity_status': 'unverified_metadata_candidate',
    }
    if inherited_source_policy is not None:
        child_graph['source_policy'] = inherited_source_policy
    write_json(child_graph_path, child_graph)
    return {'output_dir': str(output_dir), 'report_path': str(child_report_path),
            'graph_path': str(child_graph_path), 'state_path': str(child_state_path),
            'identity_map_sha256': sha(final_bytes),
            'state_sha256': child_state['state_sha256'],
            'preserved_tracks': len(state_rows(child_state)[0]),
            'preserved_directed_edges': child_report['directed_edge_count'],
            'accepted_root_count': child_report['accepted_root_count'],
            'recording_identity_verified': False, 'recording_edges_added': 0}


def verify_album_alias_chain_growth_rebinding_report(*, graph_doc, report, state,
                                                      library_hash, identity_hash,
                                                      state_path):
    """Replay parent alias chain and every ordered album delta in a map child."""
    binding = report.get('album_alias_chain_growth_rebinding')
    required_true = ('local_track_metadata_unchanged', 'local_equivalence_labels_unchanged',
                     'pid_gid_assignments_unchanged', 'metadata_bytes_unchanged',
                     'relation_bytes_unchanged', 'old_growth_replayed_with_parent_map')
    if (report.get('producer') != ALBUM_CHAIN_CHILD_PRODUCER
            or not isinstance(binding, dict) or binding.get('schema_version') != 1
            or any(binding.get(key) is not True for key in required_true)
            or binding.get('recording_identity_verified') is not False
            or binding.get('recording_edges_added_by_rebinding') != 0
            or report.get('identity_map_sha256') != identity_hash
            or report.get('zero_id_library_sha256') != library_hash):
        raise ValueError('Album alias chain child proof is incomplete')
    parent = binding.get('parent_typed_growth')
    expected_keys = {'graph_export', 'rebase_report', 'state', 'old_identity_map', 'new_identity_map'}
    if not isinstance(parent, dict) or set(parent) != expected_keys:
        raise ValueError('Album alias chain child omits parent source receipts')

    def checked(ref, label):
        if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
            raise ValueError('Malformed ' + label + ' receipt')
        path, data, doc = _read(ref['path'])
        if sha(data) != ref['sha256']:
            raise ValueError(label + ' changed')
        return path, data, doc

    pgraph_path, pgraph_bytes, pgraph = checked(parent['graph_export'], 'parent graph')
    prep_path, prep_bytes, prep = checked(parent['rebase_report'], 'parent report')
    pstate_path, pstate_bytes, pstate = checked(parent['state'], 'parent state')
    old_path, old_bytes, _ = checked(parent['old_identity_map'], 'parent old map')
    input_path, input_bytes, _ = checked(parent['new_identity_map'], 'parent output map')
    inherited_source_policy = pgraph.get('source_policy')
    if (inherited_source_policy not in (None, 'authenticated-watch-radio-only-v1')
            or ('source_policy' in prep) != (inherited_source_policy is not None)
            or ('source_policy' in prep
                and prep.get('source_policy') != inherited_source_policy)
            or ('source_policy' in pgraph) != ('source_policy' in prep)
            or prep.get('producer') != CHAIN_PRODUCER
            or (prep.get('identity_map_rebinding') or {}).get('new_identity_map') != parent['new_identity_map']
            or pgraph.get('source_identity_map_rebinding_report_sha256') != sha(prep_bytes)
            or pgraph.get('source_state_sha256') != sha(pstate_bytes)):
        raise ValueError('Album chain child does not bind the exact parent rebind')
    parent_receipts = verify_alias_chain_growth_rebinding_report(
        graph_doc=pgraph, report=prep, state=pstate,
        library_hash=library_hash,
        identity_hash=sha(input_bytes), state_path=pstate_path)
    if (input_path != Path(binding.get('input_identity_map', {}).get('path', '')).resolve()
            or binding.get('input_identity_map') != parent['new_identity_map']):
        raise ValueError('Album alias chain child starts from a different parent map')
    if (('source_policy' in binding) != (inherited_source_policy is not None)
            or ('source_policy' in report) != (inherited_source_policy is not None)
            or ('source_policy' in graph_doc) != (inherited_source_policy is not None)
            or (inherited_source_policy is not None
                and (binding.get('source_policy') != inherited_source_policy
                     or report.get('source_policy') != inherited_source_policy
                     or graph_doc.get('source_policy') != inherited_source_policy))):
        raise ValueError('Album alias child did not inherit exact parent source policy')
    snapshot_path, snapshot_bytes, _ = checked(binding.get('track_snapshot'), 'track snapshot')
    final_path, final_bytes, final_map = checked(binding.get('output_identity_map'), 'final map')
    tracks, actual_library_hash, _ = load_library(track_snapshot=snapshot_path)
    if (actual_library_hash != library_hash or sha(final_bytes) != identity_hash
            or _resolve_source_state_path(report.get('source_state_path')) != Path(state_path).resolve()):
        raise ValueError('Album alias chain child is bound to a different map, Library, or state')
    start_path = input_path
    transition_proofs = []
    descriptors = binding.get('album_alias_transitions')
    if not isinstance(descriptors, list) or len(descriptors) < 1:
        raise ValueError('Album alias chain child requires at least one ordered delta proof')
    current_path = start_path
    for proof in descriptors:
        if not isinstance(proof, dict):
            raise ValueError('Malformed album alias transition proof')
        if proof.get('input_identity_map', {}).get('path') != str(current_path):
            raise ValueError('Album alias transition chain is reordered or skips an intermediate map')
        reproduced = verify_reviewed_album_alias_map_delta(
            track_snapshot=snapshot_path,
            input_identity_map_path=current_path,
            output_identity_map_path=proof.get('output_identity_map', {}).get('path'),
            proposals_path=proof.get('proposals', {}).get('path'),
            source_evidence_path=proof.get('source_evidence', {}).get('path'),
            candidate_audit_path=proof.get('candidate_audit', {}).get('path'),
            review_decisions_path=proof.get('review_decisions', {}).get('path'),
            receipt_path=proof.get('application_receipt', {}).get('path'))
        if reproduced != proof:
            raise ValueError('Album alias transition differs from source-bound replay')
        transition_proofs.append(reproduced)
        current_path = Path(reproduced['output_identity_map']['path']).resolve()
    if current_path != final_path:
        raise ValueError('Album alias transition chain does not reach the final map')
    expected_state = _rebind_state_map_hash(pstate, identity_hash)
    if (state != expected_state or binding.get('old_state_sha256') != pstate.get('state_sha256')
            or binding.get('new_state_sha256') != state.get('state_sha256')
            or report.get('next_state_sha256') != state.get('state_sha256')
            or Path(report.get('source_state_path', '')).resolve() != Path(state_path).resolve()):
        raise ValueError('Album alias chain child changed state beyond the map hash')
    parent_roots = pgraph.get('accepted_roots')
    if graph_doc.get('accepted_roots') != parent_roots:
        raise ValueError('Album alias chain child changed accepted roots or order')
    _validate_state_graph_relation_projection(
        parent_roots, _state_graph_rows(pstate), 'Verified album-chain parent')
    _validate_state_graph_relation_projection(
        parent_roots, _state_graph_rows(state), 'Verified album-chain child')
    final_matcher = IdentityMap(final_map, tracks, library_hash)
    if plan_genius_update(state, _state_graph_rows(state), final_matcher,
                          library_hash, identity_hash)['next_state']['state_sha256'] != state['state_sha256']:
        raise ValueError('Album alias chain child state does not reproduce under final map')
    if report.get('relation_revalidation') != prep.get('relation_revalidation'):
        raise ValueError('Album alias chain child changed source relation audit')
    expected_inputs = list(parent_receipts) + [
        _receipt(pgraph_path), _receipt(prep_path), _receipt(pstate_path),
        _receipt(old_path), _receipt(input_path)]
    for proof in transition_proofs:
        expected_inputs.extend(proof[key] for key in (
            'track_snapshot', 'input_identity_map', 'output_identity_map',
            'application_receipt', 'proposals', 'source_evidence',
            'candidate_audit', 'review_decisions'))
    expected_inputs.extend([_receipt(snapshot_path), _receipt(final_path),
                            _receipt(Path(state_path).resolve())])
    expected = _proof_input_receipts(expected_inputs)
    saved = _canonical_receipt_rows(binding.get('proof_input_receipts'))
    if saved != expected:
        raise ValueError('Album alias child omits or changes its transitive source closure')
    return saved


def _resolve_source_state_path(value):
    if not isinstance(value, str) or not value:
        raise ValueError('Typed report has no source state path')
    return Path(value).resolve()
