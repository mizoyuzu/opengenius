"""Rebind a verified YT Music state to an alias-only identity-map revision."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from merge_rebased_radio_growth import (
    _relation_map, _replay_previous_growth, _state_graph_rows,
    _verify_rebase_receipt, sha, write_json,
)
from music_identity_map import IdentityMap, load_library
from plan_genius_update import _hash as state_hash, _source as identity_source, plan_genius_update
from update_genius_bundle import state_rows
from apply_artist_title_aliases import verify_title_alias_transition
from apply_reviewed_album_title_aliases import verify_album_alias_transition


def _read(path):
    path = Path(path).resolve()
    data = path.read_bytes()
    return path, data, json.loads(data)


def verify_reviewed_identity_map_alias_chain(*, track_snapshot, input_identity_map_path,
                                             output_identity_map_path, transitions):
    """Recompute an ordered chain of reviewed identity-map metadata deltas."""
    from music_identity_map import apply_artist_credit_alias_review

    snapshot_path, snapshot_bytes, _ = _read(track_snapshot)
    tracks, library_hash, _ = load_library(track_snapshot=snapshot_path)
    start_path, start_bytes, start_map = _read(input_identity_map_path)
    final_path, final_bytes, final_map = _read(output_identity_map_path)
    if start_map.get('library_sha256') != library_hash or final_map.get('library_sha256') != library_hash:
        raise ValueError('Alias transition chain maps differ from their Library snapshot')
    if not isinstance(transitions, list) or not transitions:
        raise ValueError('Typed alias chain must contain ordered reviewed transitions')
    current_path, current_bytes, current_map = start_path, start_bytes, start_map
    proof_inputs = {
        (str(snapshot_path), sha(snapshot_bytes)),
        (str(start_path), sha(start_bytes)), (str(final_path), sha(final_bytes)),
    }
    proof_transitions = []
    for descriptor in transitions:
        if not isinstance(descriptor, dict) or descriptor.get('operation') not in {
                'apply_reviewed_whole_artist_credit_aliases',
                'apply_reviewed_exact_title_spelling_aliases',
                'apply_root_reviewed_exact_whole_album_metadata_alias'}:
            raise ValueError('Alias chain contains an unsupported transition operation')
        output_map_path = Path(descriptor.get('output_identity_map_path', '')).resolve()
        output_map_bytes = output_map_path.read_bytes()
        output_map = json.loads(output_map_bytes)
        if output_map.get('library_sha256') != library_hash:
            raise ValueError('Alias chain stage belongs to a different Library')
        operation = descriptor['operation']
        if operation == 'apply_reviewed_whole_artist_credit_aliases':
            receipt_path, receipt_bytes, receipt = _read(descriptor['receipt_path'])
            proposal_path, proposal_bytes, proposals = _read(descriptor['proposals_path'])
            review_path, review_bytes, review = _read(descriptor['review_decisions_path'])
            source_ref = proposals.get('source_report') or {}
            source_path = Path(source_ref.get('path') or '').resolve()
            source_bytes = source_path.read_bytes()
            source = json.loads(source_bytes)
            if (receipt.get('operation') != operation
                    or receipt.get('input_identity_map_sha256') != sha(current_bytes)
                    or receipt.get('output_identity_map_sha256') != sha(output_map_bytes)
                    or receipt.get('proposal_sha256') != sha(proposal_bytes)
                    or receipt.get('source_report_sha256') != sha(source_bytes)
                    or receipt.get('review_decisions_sha256') != sha(review_bytes)
                    or receipt.get('recording_identity_verified') is not False
                    or review.get('proposal_sha256') != sha(proposal_bytes)
                    or review.get('source_report_sha256') != sha(source_bytes)
                    or review.get('approval', {}).get('reviewed_by') != 'root'
                    or review.get('approval', {}).get('recording_identity_verified') is not False
                    or review.get('approval', {}).get('new_recording_edges_authorized') is not False
                    or source_ref.get('sha256') != sha(source_bytes)):
                raise ValueError('Whole-credit transition receipt differs from its exact map/source chain')
            source_audits = source.get('source_audits', [])
            prior_reviews = source.get('prior_root_reviews', [])
            if not source_audits or not prior_reviews:
                raise ValueError('Whole-credit source report omits candidate audits or prior reviews')
            audit_receipts = set()
            for row in source_audits:
                path = Path(row.get('path', '')).resolve()
                data = path.read_bytes()
                if sha(data) != row.get('sha256'):
                    raise ValueError('Whole-credit candidate audit changed')
                proof_inputs.add((str(path), sha(data)))
                audit_receipts.add((str(path), sha(data)))
            for row in prior_reviews:
                path = Path(row.get('path', '')).resolve()
                data = path.read_bytes()
                if sha(data) != row.get('sha256'):
                    raise ValueError('Earlier whole-credit root review changed')
                proof_inputs.add((str(path), sha(data)))
            candidate_refs = {(str(Path(row['candidate_audit']['path']).resolve()),
                               row['candidate_audit']['sha256'])
                              for row in proposals.get('proposals', [])
                              if isinstance(row.get('candidate_audit'), dict)}
            if not candidate_refs or not candidate_refs.issubset(audit_receipts):
                raise ValueError('Whole-credit proposals are not bound to verified audits')
            expected = apply_artist_credit_alias_review(
                current_map, review, proposals, tracks, library_hash,
                sha(current_bytes), sha(proposal_bytes))
            if expected != output_map:
                raise ValueError('Whole-credit stage output is not reproduced by its review')
            refs = {'receipt': (receipt_path, receipt_bytes), 'proposal': (proposal_path, proposal_bytes),
                    'review': (review_path, review_bytes), 'source_report': (source_path, source_bytes)}
        elif operation == 'apply_reviewed_exact_title_spelling_aliases':
            receipt_path, receipt_bytes, receipt = _read(descriptor['receipt_path'])
            input_ref = Path(receipt.get('input_identity_map_path', '')).resolve()
            output_ref = Path(receipt.get('output_identity_map_path', '')).resolve()
            if input_ref != current_path or output_ref != output_map_path:
                raise ValueError('Title alias stage paths do not form the ordered transition chain')
            title_proposal = Path(receipt.get('proposals_path', '')).resolve()
            title_review = Path(receipt.get('review_decisions_path', '')).resolve()
            source_audit = Path(receipt.get('source_report_path', '')).resolve()
            verify_title_alias_transition(
                track_snapshot=snapshot_path, input_identity_map_path=current_path,
                output_identity_map_path=output_map_path, proposals_path=title_proposal,
                review_decisions_path=title_review, receipt_path=receipt_path)
            refs = {'receipt': (receipt_path, receipt_bytes),
                    'proposal': (title_proposal, title_proposal.read_bytes()),
                    'review': (title_review, title_review.read_bytes()),
                    'source_report': (source_audit, source_audit.read_bytes())}
        else:
            receipt_path = Path(descriptor['receipt_path']).resolve()
            proposal_path = Path(descriptor['proposals_path']).resolve()
            source_path = Path(descriptor['source_evidence_path']).resolve()
            audit_path = Path(descriptor['candidate_audit_path']).resolve()
            review_path = Path(descriptor['review_decisions_path']).resolve()
            album_proof = verify_reviewed_album_alias_map_delta(
                track_snapshot=snapshot_path, input_identity_map_path=current_path,
                output_identity_map_path=output_map_path,
                proposals_path=descriptor['proposals_path'],
                source_evidence_path=descriptor['source_evidence_path'],
                candidate_audit_path=descriptor['candidate_audit_path'],
                review_decisions_path=descriptor['review_decisions_path'],
                receipt_path=receipt_path)
            if album_proof['input_identity_map']['sha256'] != sha(current_bytes):
                raise ValueError('Album alias stage input differs from its chain predecessor')
            refs = {'receipt': (receipt_path, receipt_path.read_bytes()),
                    'proposal': (proposal_path, proposal_path.read_bytes()),
                    'source_evidence': (source_path, source_path.read_bytes()),
                    'candidate_audit': (audit_path, audit_path.read_bytes()),
                    'review': (review_path, review_path.read_bytes())}
        for path, data in refs.values():
            proof_inputs.add((str(path.resolve()), sha(data)))
        if operation != 'apply_root_reviewed_exact_whole_album_metadata_alias':
            receipt = json.loads(refs['receipt'][1])
            if receipt.get('input_identity_map_sha256') != sha(current_bytes):
                raise ValueError('Alias stage input hash differs from its chain predecessor')
        proof_transitions.append({
            'operation': operation,
            'input_identity_map': {'path': str(current_path), 'sha256': sha(current_bytes)},
            'output_identity_map': {'path': str(output_map_path), 'sha256': sha(output_map_bytes)},
            'source_receipts': [
                {'kind': key, 'path': str(path.resolve()), 'sha256': sha(data)}
                for key, (path, data) in sorted(refs.items())
            ],
        })
        current_path, current_bytes, current_map = output_map_path, output_map_bytes, output_map
    if current_path != final_path or current_bytes != final_bytes:
        raise ValueError('Reviewed alias chain does not terminate at the declared final map')
    return {
        'schema_version': 1,
        'producer': 'opengenius-reviewed-identity-map-alias-chain-v1',
        'library_sha256': library_hash,
        'input_identity_map': {'path': str(start_path), 'sha256': sha(start_bytes)},
        'output_identity_map': {'path': str(final_path), 'sha256': sha(final_bytes)},
        'track_snapshot': {'path': str(snapshot_path), 'sha256': sha(snapshot_bytes)},
        'transitions': proof_transitions,
        'proof_input_receipts': [
            {'path': path, 'sha256': digest} for path, digest in sorted(proof_inputs)
        ],
        'recording_identity_verified': False,
        'recording_edges_added': 0,
    }


def _stable_map_delta(old_map, new_map):
    old_base, new_base = deepcopy(old_map), deepcopy(new_map)
    old_aliases = old_base.pop('artist_credit_aliases', [])
    new_aliases = new_base.pop('artist_credit_aliases', [])
    old_titles = old_base.pop('track_title_aliases', [])
    new_titles = new_base.pop('track_title_aliases', [])
    if (old_base != new_base or not isinstance(new_aliases, list)
            or not isinstance(new_titles, list)
            or new_aliases[:len(old_aliases)] != old_aliases
            or new_titles[:len(old_titles)] != old_titles):
        raise ValueError('Map revisions differ beyond append-only whole-credit/title aliases')
    if len(new_aliases) <= len(old_aliases):
        raise ValueError('New identity map adds no whole-credit aliases')
    return old_aliases, new_aliases, new_titles[len(old_titles):]


def verify_reviewed_album_alias_map_delta(*, track_snapshot, input_identity_map_path,
                                          output_identity_map_path,
                                          proposals_path, source_evidence_path,
                                          candidate_audit_path, review_decisions_path,
                                          receipt_path):
    """Verify an exact, reviewed album-alias-only identity-map transition.

    Artist-credit and title aliases are treated as immutable carried state here:
    the input and output maps must be byte-structure identical after removing
    only ``album_title_aliases``. The album applier then rebuilds the output
    from its source evidence, review, and receipt, so a caller can bind this
    proof into a later typed growth-source rebind without relabeling a map.
    """
    # The reviewed proposal stores source paths as written at review time, so
    # pass those original strings to its verifier even though this proof records
    # normalized absolute receipts below.
    proposal_argument = proposals_path
    evidence_argument = source_evidence_path
    audit_argument = candidate_audit_path
    review_argument = review_decisions_path
    snapshot_path, snapshot_bytes, _ = _read(track_snapshot)
    input_path, input_bytes, input_map = _read(input_identity_map_path)
    output_path, output_bytes, output_map = _read(output_identity_map_path)
    receipt_source, receipt_bytes, receipt = _read(receipt_path)
    proposal_source, proposal_bytes, _ = _read(proposals_path)
    evidence_source, evidence_bytes, _ = _read(source_evidence_path)
    audit_source, audit_bytes, _ = _read(candidate_audit_path)
    review_source, review_bytes, _ = _read(review_decisions_path)

    tracks, library_hash, _ = load_library(track_snapshot=snapshot_path)
    input_hash, output_hash = sha(input_bytes), sha(output_bytes)
    if (input_map.get('library_sha256') != library_hash
            or output_map.get('library_sha256') != library_hash):
        raise ValueError('Album alias maps do not match the bound Library snapshot')
    input_base, output_base = deepcopy(input_map), deepcopy(output_map)
    input_aliases = input_base.pop('album_title_aliases', [])
    output_aliases = output_base.pop('album_title_aliases', [])
    if (input_base != output_base or not isinstance(input_aliases, list)
            or not isinstance(output_aliases, list)
            or output_aliases[:len(input_aliases)] != input_aliases
            or len(output_aliases) <= len(input_aliases)):
        raise ValueError('Album alias map changed fields beyond an append-only album alias delta')
    if (not isinstance(receipt, dict)
            or receipt.get('operation') != 'apply_root_reviewed_exact_whole_album_metadata_alias'
            or receipt.get('library_sha256') != library_hash
            or receipt.get('track_snapshot_sha256') != sha(snapshot_bytes)
            or Path(receipt.get('track_snapshot_path', '')).resolve() != snapshot_path
            or receipt.get('input_identity_map_sha256') != input_hash
            or Path(receipt.get('input_identity_map_path', '')).resolve() != input_path
            or receipt.get('output_identity_map_sha256') != output_hash
            or Path(receipt.get('output_identity_map_path', '')).resolve() != output_path
            or receipt.get('proposals_path') != str(proposal_source)
            or receipt.get('proposal_sha256') != sha(proposal_bytes)
            or receipt.get('source_evidence_path') != str(evidence_source)
            or receipt.get('source_evidence_sha256') != sha(evidence_bytes)
            or receipt.get('candidate_audit_path') != str(audit_source)
            or receipt.get('candidate_audit_sha256') != sha(audit_bytes)
            or receipt.get('review_decisions_path') != str(review_source)
            or receipt.get('review_decisions_sha256') != sha(review_bytes)
            or receipt.get('canonical_library_fields_changed') is not False
            or receipt.get('recording_identity_verified') is not False
            or receipt.get('recording_edges_added') != 0):
        raise ValueError('Album alias receipt does not bind the exact reviewed map transition')

    rebuilt = verify_album_alias_transition(
        track_snapshot=snapshot_path, identity_map_path=input_path,
        proposals_path=proposal_argument, source_evidence_path=evidence_argument,
        candidate_audit_path=audit_argument, review_decisions_path=review_argument,
        output_path=output_path, receipt_path=receipt_source)
    if (rebuilt.get('input_identity_map_sha256') != input_hash
            or rebuilt.get('output_identity_map_sha256') != output_hash
            or rebuilt.get('recording_identity_verified') is not False
            or rebuilt.get('recording_edges_added') != 0):
        raise ValueError('Recomputed album alias transition differs from the source-bound delta')
    # The matcher validates canonical targets and alias ambiguity against all
    # tracks. Local artist/title/album metadata remains sourced from the snapshot.
    IdentityMap(output_map, tracks, library_hash)
    return {
        'schema_version': 1,
        'producer': 'opengenius-typed-reviewed-album-alias-map-transition-v1',
        'library_sha256': library_hash,
        'track_snapshot': {'path': str(snapshot_path), 'sha256': sha(snapshot_bytes)},
        'input_identity_map': {'path': str(input_path), 'sha256': input_hash},
        'output_identity_map': {'path': str(output_path), 'sha256': output_hash},
        'application_receipt': {'path': str(receipt_source), 'sha256': sha(receipt_bytes)},
        'proposals': {'path': str(proposal_source), 'sha256': sha(proposal_bytes)},
        'source_evidence': {'path': str(evidence_source), 'sha256': sha(evidence_bytes)},
        'candidate_audit': {'path': str(audit_source), 'sha256': sha(audit_bytes)},
        'review_decisions': {'path': str(review_source), 'sha256': sha(review_bytes)},
        'appended_album_alias_count': len(output_aliases) - len(input_aliases),
        'recording_identity_verified': False,
        'recording_edges_added': 0,
    }


def _rebind_state_map_hash(state, new_identity_map_sha256):
    """Change only the state source map hash and reseal its document."""
    rebound = deepcopy(state)
    rebound['source']['identity_map_sha256'] = new_identity_map_sha256
    rebound['state_sha256'] = state_hash({key: value for key, value in rebound.items()
                                          if key != 'state_sha256'})
    return rebound


def rebind_album_alias_growth_source(*, track_snapshot, parent_graph_export_path,
                                    parent_rebase_report_path, parent_state_path,
                                    input_identity_map_path, output_identity_map_path,
                                    proposals_path, source_evidence_path,
                                    candidate_audit_path, review_decisions_path,
                                    album_alias_receipt_path, output_dir):
    """Append an exact reviewed album alias to an already typed growth source.

    The parent typed rebind must validate its complete prior growth chain and
    terminate at the input map. This child transition preserves that accepted
    graph and state, changing only the state source-map hash after the reviewed
    v11→v12-style album delta has been independently reproduced.
    """
    import hashlib

    from merge_rebased_radio_growth import verify_typed_identity_map_rebinding

    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError('Output directory exists; choose a new directory')
    parent_graph_path, parent_graph_bytes, parent_graph = _read(parent_graph_export_path)
    parent_report_path, parent_report_bytes, parent_report = _read(parent_rebase_report_path)
    parent_state_source, parent_state_bytes, parent_state = _read(parent_state_path)
    parent_binding = parent_report.get('identity_map_rebinding') or {}
    old_map_ref = parent_binding.get('old_identity_map') or {}
    parent_new_ref = parent_binding.get('new_identity_map') or {}
    old_map_path = Path(old_map_ref.get('path', '')).resolve()
    parent_new_path = Path(parent_new_ref.get('path', '')).resolve()
    if (not old_map_path.is_file() or not parent_new_path.is_file()
            or old_map_ref.get('sha256') != sha(old_map_path.read_bytes())
            or parent_new_ref.get('sha256') != sha(parent_new_path.read_bytes())):
        raise ValueError('Parent typed growth proof has missing identity-map receipts')
    input_path = Path(input_identity_map_path).resolve()
    output_path = Path(output_identity_map_path).resolve()
    if input_path != parent_new_path:
        raise ValueError('Album alias base map differs from the parent typed growth source')
    tracks, library_hash, _ = load_library(track_snapshot=Path(track_snapshot).resolve())
    parent_proof = verify_typed_identity_map_rebinding(
        graph_export_path=parent_graph_path, rebase_report_path=parent_report_path,
        state_path=parent_state_source, old_identity_map_path=old_map_path,
        new_identity_map_path=input_path, library_sha256=library_hash)
    album_proof = verify_reviewed_album_alias_map_delta(
        track_snapshot=track_snapshot, input_identity_map_path=input_path,
        output_identity_map_path=output_path, proposals_path=proposals_path,
        source_evidence_path=source_evidence_path, candidate_audit_path=candidate_audit_path,
        review_decisions_path=review_decisions_path, receipt_path=album_alias_receipt_path)
    if (parent_proof.get('verified') is not True
            or album_proof['input_identity_map']['sha256'] != parent_proof['new_identity_map_sha256']
            or album_proof['input_identity_map']['path'] != parent_proof['new_identity_map_path']):
        raise ValueError('Album alias transition is not chained to the verified typed growth map')

    old_map = json.loads(old_map_path.read_bytes())
    input_map = json.loads(input_path.read_bytes())
    output_map = json.loads(output_path.read_bytes())
    _, old_labels = identity_source(IdentityMap(old_map, tracks, library_hash), library_hash,
                                    parent_proof['old_identity_map_sha256'])
    _, input_labels = identity_source(IdentityMap(input_map, tracks, library_hash), library_hash,
                                      parent_proof['new_identity_map_sha256'])
    _, output_labels = identity_source(IdentityMap(output_map, tracks, library_hash), library_hash,
                                       album_proof['output_identity_map']['sha256'])
    if old_labels != input_labels or input_labels != output_labels:
        raise ValueError('Album alias rebind changed local equivalence labels')

    old_state_ids, _, old_relations = state_rows(parent_state)
    roots = parent_graph.get('accepted_roots')
    if _relation_map(roots, 'Parent typed growth graph') != _relation_map(
            _state_graph_rows(parent_state), 'Parent typed growth state'):
        raise ValueError('Parent typed growth graph differs from its state')
    rebound_state = _rebind_state_map_hash(
        parent_state, album_proof['output_identity_map']['sha256'])
    replay = plan_genius_update(
        rebound_state, roots, IdentityMap(output_map, tracks, library_hash),
        library_hash, album_proof['output_identity_map']['sha256'])['next_state']
    if replay['state_sha256'] != rebound_state['state_sha256']:
        raise ValueError('Album-rebound state does not reproduce with the reviewed output map')
    new_state_ids, _, new_relations = state_rows(rebound_state)
    if (old_state_ids != new_state_ids or parent_state['rows'] != rebound_state['rows']
            or old_relations != new_relations
            or _relation_map(roots, 'Parent typed graph') != _relation_map(
                _state_graph_rows(rebound_state), 'Rebound typed graph')):
        raise ValueError('Album alias rebind changed PID/GID assignments, metadata, or graph edges')

    output_dir.mkdir(parents=True)
    state_path = output_dir / 'state.json'
    report_path = output_dir / 'identity-map-rebinding-report.json'
    graph_path = output_dir / 'accepted-baseline-graphs.json'
    write_json(state_path, rebound_state)
    binding = {
        'schema_version': 1,
        'parent_typed_growth': {
            'graph_export': {'path': str(parent_graph_path), 'sha256': sha(parent_graph_bytes)},
            'rebase_report': {'path': str(parent_report_path), 'sha256': sha(parent_report_bytes)},
            'state': {'path': str(parent_state_source), 'sha256': sha(parent_state_bytes)},
            'old_identity_map': {'path': str(old_map_path),
                                 'sha256': parent_proof['old_identity_map_sha256']},
            'new_identity_map': {'path': str(input_path),
                                 'sha256': parent_proof['new_identity_map_sha256']},
        },
        'album_alias_transition': album_proof,
        'output_identity_map': album_proof['output_identity_map'],
        'track_snapshot': album_proof['track_snapshot'],
        'old_state_sha256': parent_state['state_sha256'],
        'new_state_sha256': rebound_state['state_sha256'],
        'accepted_input_manifest_sha256': parent_binding.get('accepted_input_manifest_sha256'),
        'authenticated_observation_inputs': parent_binding.get('authenticated_observation_inputs'),
        'authenticated_observation_input_count': parent_binding.get(
            'authenticated_observation_input_count'),
        'local_track_metadata_unchanged': True,
        'local_equivalence_labels_unchanged': True,
        'pid_gid_assignments_unchanged': True,
        'metadata_bytes_unchanged': True,
        'relation_bytes_unchanged': True,
        'old_growth_replayed_with_parent_map': True,
        'recording_identity_verified': False,
        'recording_edges_added_by_rebinding': 0,
    }
    parent_audit = parent_report['relation_revalidation']
    report = {
        'schema_version': 1,
        'producer': 'opengenius-typed-album-alias-growth-rebinding-v1',
        'zero_id_library_sha256': library_hash,
        'identity_map_sha256': album_proof['output_identity_map']['sha256'],
        'next_state_sha256': rebound_state['state_sha256'],
        'indexed_tracks': len(new_state_ids),
        'source_state_path': str(state_path.resolve()),
        'album_alias_growth_rebinding': binding,
        'relation_revalidation': deepcopy(parent_audit),
        'raw_input_provenance': deepcopy(parent_report['raw_input_provenance']),
    }
    write_json(report_path, report)
    report_hash = sha(report_path.read_bytes())
    write_json(graph_path, {
        'schema_version': 1,
        'producer': 'verified-identity-map-rebound-state-graph-export',
        'source_state_path': str(state_path.resolve()),
        'source_state_sha256': sha(state_path.read_bytes()),
        'source_identity_map_rebinding_report_sha256': report_hash,
        'accepted_roots': roots,
        'identity_status': 'unverified_metadata_candidate',
    })
    return {
        'output_dir': str(output_dir),
        'identity_map_sha256': album_proof['output_identity_map']['sha256'],
        'state_sha256': rebound_state['state_sha256'],
        'preserved_tracks': len(new_state_ids),
        'preserved_directed_edges': sum(len(row['ordered_target_pids']) for row in roots),
        'appended_album_aliases': album_proof['appended_album_alias_count'],
        'recording_identity_verified': False,
        'recording_edges_added': 0,
    }


def verify_album_alias_growth_rebinding_report(*, graph_doc, report, state,
                                               library_hash, identity_hash,
                                               state_path):
    """Independently validate a typed child growth rebind for album aliases."""
    from merge_rebased_radio_growth import _resolve_input, _relation_map
    from merge_rebased_radio_growth import verify_typed_identity_map_rebinding

    binding = report.get('album_alias_growth_rebinding')
    if (report.get('producer') != 'opengenius-typed-album-alias-growth-rebinding-v1'
            or not isinstance(binding, dict)
            or binding.get('schema_version') != 1
            or any(binding.get(key) is not True for key in (
                'local_track_metadata_unchanged', 'local_equivalence_labels_unchanged',
                'pid_gid_assignments_unchanged', 'metadata_bytes_unchanged',
                'relation_bytes_unchanged', 'old_growth_replayed_with_parent_map'))
            or binding.get('recording_identity_verified') is not False
            or binding.get('recording_edges_added_by_rebinding') != 0):
        raise ValueError('Album alias growth rebind proof is incomplete')
    parent = binding.get('parent_typed_growth')
    expected_parent_keys = {'graph_export', 'rebase_report', 'state',
                            'old_identity_map', 'new_identity_map'}
    if not isinstance(parent, dict) or set(parent) != expected_parent_keys:
        raise ValueError('Album alias growth proof omits its parent typed-source receipts')

    def checked(ref, label):
        if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
            raise ValueError('Malformed ' + label + ' receipt in album alias growth proof')
        path = _resolve_input(ref['path'])
        data = path.read_bytes()
        if sha(data) != ref['sha256']:
            raise ValueError(label + ' changed after album alias growth rebinding')
        return path, data

    parent_graph_path, parent_graph_bytes = checked(parent['graph_export'], 'parent graph')
    parent_report_path, parent_report_bytes = checked(parent['rebase_report'], 'parent report')
    parent_state_path, parent_state_bytes = checked(parent['state'], 'parent state')
    parent_graph = json.loads(parent_graph_bytes)
    parent_report = json.loads(parent_report_bytes)
    parent_state = json.loads(parent_state_bytes)
    parent_binding = parent_report.get('identity_map_rebinding') or {}
    parent_old = parent_binding.get('old_identity_map') or {}
    parent_new = parent_binding.get('new_identity_map') or {}
    old_map_path, old_map_bytes = checked(parent['old_identity_map'], 'parent old map')
    input_map_path, input_map_bytes = checked(parent['new_identity_map'], 'parent new map')
    if (parent_old.get('sha256') != sha(old_map_bytes)
            or _resolve_input(parent_old.get('path')) != old_map_path
            or parent_new.get('sha256') != sha(input_map_bytes)
            or _resolve_input(parent_new.get('path')) != input_map_path
            or input_map_path != _resolve_input(
                binding.get('album_alias_transition', {}).get('input_identity_map', {}).get('path'))):
        raise ValueError('Album alias child map does not continue the exact parent typed map')
    old_map = json.loads(old_map_bytes)
    input_map = json.loads(input_map_bytes)
    output_ref = binding.get('output_identity_map') or {}
    output_map_path, output_map_bytes = checked(output_ref, 'album alias output map')
    output_map = json.loads(output_map_bytes)
    snapshot_ref = binding.get('track_snapshot') or {}
    snapshot_path, snapshot_bytes = checked(snapshot_ref, 'track snapshot')
    tracks, snapshot_library_hash, _ = load_library(track_snapshot=snapshot_path)
    if (snapshot_library_hash != library_hash
            or report.get('zero_id_library_sha256') != library_hash
            or report.get('identity_map_sha256') != identity_hash
            or output_ref.get('sha256') != identity_hash
            or _resolve_input(report.get('source_state_path')) != Path(state_path).resolve()):
        raise ValueError('Album alias growth report is bound to a different map, Library, or state')

    parent_result = verify_typed_identity_map_rebinding(
        graph_export_path=parent_graph_path, rebase_report_path=parent_report_path,
        state_path=parent_state_path, old_identity_map_path=old_map_path,
        new_identity_map_path=input_map_path, library_sha256=library_hash)
    transition = binding.get('album_alias_transition')
    if not isinstance(transition, dict):
        raise ValueError('Album alias growth proof omits its typed alias transition')
    recomputed = verify_reviewed_album_alias_map_delta(
        track_snapshot=snapshot_path, input_identity_map_path=input_map_path,
        output_identity_map_path=output_map_path,
        proposals_path=transition.get('proposals', {}).get('path'),
        source_evidence_path=transition.get('source_evidence', {}).get('path'),
        candidate_audit_path=transition.get('candidate_audit', {}).get('path'),
        review_decisions_path=transition.get('review_decisions', {}).get('path'),
        receipt_path=transition.get('application_receipt', {}).get('path'))
    if recomputed != transition:
        raise ValueError('Album alias transition proof differs from its source-bound review receipt')

    parent_state_digest = parent_state.get('state_sha256')
    if binding.get('old_state_sha256') != parent_state_digest:
        raise ValueError('Album alias growth proof names the wrong parent state')
    expected_state = deepcopy(parent_state)
    expected_state['source']['identity_map_sha256'] = identity_hash
    expected_state['state_sha256'] = state_hash(
        {key: value for key, value in expected_state.items() if key != 'state_sha256'})
    if (expected_state != state or state.get('state_sha256') != binding.get('new_state_sha256')
            or report.get('next_state_sha256') != state.get('state_sha256')):
        raise ValueError('Album alias child state changed fields beyond its source map hash')
    parent_roots = parent_graph.get('accepted_roots')
    child_roots = graph_doc.get('accepted_roots')
    if _relation_map(parent_roots, 'Parent typed graph') != _relation_map(
            child_roots, 'Album alias child graph'):
        raise ValueError('Album alias child graph differs from the parent typed growth graph')
    old_matcher = IdentityMap(old_map, tracks, library_hash)
    input_matcher = IdentityMap(input_map, tracks, library_hash)
    output_matcher = IdentityMap(output_map, tracks, library_hash)
    _, old_labels = identity_source(old_matcher, library_hash, parent_result['old_identity_map_sha256'])
    _, input_labels = identity_source(input_matcher, library_hash, parent_result['new_identity_map_sha256'])
    _, output_labels = identity_source(output_matcher, library_hash, identity_hash)
    if old_labels != input_labels or input_labels != output_labels:
        raise ValueError('Album alias child transition changed local equivalence labels')
    if plan_genius_update(expected_state, child_roots, output_matcher,
                          library_hash, identity_hash)['next_state']['state_sha256'] != state['state_sha256']:
        raise ValueError('Album alias child state does not reproduce under its output map')

    parent_audit = parent_report.get('relation_revalidation')
    if report.get('relation_revalidation') != parent_audit:
        raise ValueError('Album alias child changed the verified growth source audit')
    proof_inputs = {(row['path'], row['sha256'])
                    for row in parent_result.get('proof_input_receipts', [])}
    proof_inputs.update((receipt['path'], receipt['sha256']) for receipt in (
        binding['track_snapshot'], binding['output_identity_map'],
        transition['input_identity_map'], transition['output_identity_map'],
        transition['application_receipt'], transition['proposals'],
        transition['source_evidence'], transition['candidate_audit'],
        transition['review_decisions'], parent['graph_export'],
        parent['rebase_report'], parent['state']))
    proof_inputs.add((str(Path(state_path).resolve()), sha(Path(state_path).read_bytes())))
    return [{'path': path, 'sha256': digest} for path, digest in sorted(proof_inputs)]


def rebind_radio_growth(*, track_snapshot, old_identity_map_path, new_identity_map_path,
                        alias_application_receipt_path, review_decisions_path,
                        proposals_path, title_alias_application_receipt_path,
                        title_review_decisions_path, title_proposals_path,
                        baseline_state_path, baseline_graph_export_path,
                        rebase_report_path, growth_dir, output_dir):
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError('Output directory exists; choose a new directory')
    tracks, library_hash, _ = load_library(track_snapshot=Path(track_snapshot))
    snapshot_path = Path(track_snapshot).resolve()
    snapshot_hash = sha(snapshot_path.read_bytes())
    old_path, old_bytes, old_map = _read(old_identity_map_path)
    new_path, new_bytes, new_map = _read(new_identity_map_path)
    old_hash, new_hash = sha(old_bytes), sha(new_bytes)
    if old_map.get('library_sha256') != library_hash or new_map.get('library_sha256') != library_hash:
        raise ValueError('Identity maps do not match the current zero-ID Library')
    old_aliases, new_aliases, title_alias_delta = _stable_map_delta(old_map, new_map)
    old_matcher = IdentityMap(old_map, tracks, library_hash)
    new_matcher = IdentityMap(new_map, tracks, library_hash)

    app_path, app_bytes, application = _read(alias_application_receipt_path)
    review_path, review_bytes, review = _read(review_decisions_path)
    proposal_path, proposal_bytes, proposals = _read(proposals_path)
    if (application.get('operation') != 'apply_reviewed_whole_artist_credit_aliases'
            or application.get('input_identity_map_sha256') != old_hash
            or application.get('review_decisions_sha256') != sha(review_bytes)
            or application.get('proposal_sha256') != sha(proposal_bytes)
            or application.get('recording_identity_verified') is not False
            or review.get('approval', {}).get('reviewed_by') != 'root'
            or review.get('approval', {}).get('recording_identity_verified') is not False
            or review.get('approval', {}).get('new_recording_edges_authorized') is not False
            or len(review.get('decisions', [])) != len(new_aliases) - len(old_aliases)):
        raise ValueError('Alias review/application receipts do not authorize this exact map revision')
    proposal_report = proposals.get('source_report') or {}
    report_source = Path(proposal_report.get('path') or '').resolve()
    if (not report_source.is_file() or sha(report_source.read_bytes()) != proposal_report.get('sha256')
            or review.get('proposal_sha256') != sha(proposal_bytes)
            or review.get('source_report_sha256') != proposal_report.get('sha256')):
        raise ValueError('Reviewed credit proposals are not bound to their unchanged source report')

    title_receipt_path, title_receipt_bytes, title_receipt = _read(
        title_alias_application_receipt_path)
    title_review_path, title_review_bytes, title_review = _read(title_review_decisions_path)
    title_proposal_path, title_proposal_bytes, title_proposals = _read(title_proposals_path)
    intermediate_hash = application.get('output_identity_map_sha256')
    if (title_receipt.get('operation') != 'apply_reviewed_exact_title_spelling_aliases'
            or title_receipt.get('input_identity_map_sha256') != intermediate_hash
            or title_receipt.get('output_identity_map_sha256') != new_hash
            or title_receipt.get('review_decisions_sha256') != sha(title_review_bytes)
            or title_receipt.get('proposal_sha256') != sha(title_proposal_bytes)
            or title_review.get('identity_map_sha256') != intermediate_hash
            or title_review.get('proposal_sha256') != sha(title_proposal_bytes)
            or title_review.get('approval', {}).get('reviewed_by') != 'root'
            or title_review.get('approval', {}).get('recording_identity_verified') is not False
            or title_review.get('approval', {}).get('new_recording_edges_authorized') is not False
            or len(title_review.get('decisions', [])) != len(title_alias_delta)
            or len(title_alias_delta) != title_receipt.get('activated_title_alias_count')
            or title_receipt.get('recording_identity_verified') is not False
            or title_receipt.get('recording_edges_added') != 0):
        raise ValueError('Title review/application receipts do not authorize this exact map revision')
    title_intermediate_path = Path(title_receipt.get('input_identity_map_path') or '').resolve()
    if sha(title_intermediate_path.read_bytes()) != intermediate_hash:
        raise ValueError('Title transition intermediate identity map changed')
    if Path(title_receipt.get('output_identity_map_path') or '').resolve() != new_path:
        raise ValueError('Title transition receipt points to a different final map')
    verified_title_receipt = verify_title_alias_transition(
        track_snapshot=snapshot_path,
        input_identity_map_path=title_intermediate_path,
        output_identity_map_path=new_path,
        proposals_path=title_proposal_path,
        review_decisions_path=title_review_path,
        receipt_path=title_receipt_path)
    if verified_title_receipt.get('output_identity_map_sha256') != new_hash:
        raise ValueError('Reviewed title transition does not reproduce the final map')
    title_pairs = {(row.get('persistent_id'), row.get('original_title'), row.get('alias'))
                   for row in title_review.get('decisions', [])}
    delta_pairs = set()
    for row in title_alias_delta:
        for alias in row.get('aliases', []):
            if alias.get('enabled') and alias.get('status') == 'reviewed_spelling_alias':
                delta_pairs.add((row.get('persistent_id'), row.get('original_title'), alias.get('value')))
    if title_pairs != delta_pairs:
        raise ValueError('Final map title aliases differ from exact reviewed title decisions')

    old_graph, old_report, baseline_state, base_provenance = _verify_rebase_receipt(
        baseline_graph_export_path, rebase_report_path, baseline_state_path,
        library_hash, old_hash)
    base_roots = old_graph.get('accepted_roots')
    if _relation_map(base_roots, 'Source baseline graph') != _relation_map(
            _state_graph_rows(baseline_state), 'Source baseline state'):
        raise ValueError('Original baseline graph differs from its state')
    check = plan_genius_update(baseline_state, base_roots, old_matcher, library_hash, old_hash)
    if check['next_state']['state_sha256'] != baseline_state['state_sha256']:
        raise ValueError('Original baseline does not reproduce under the old identity map')

    growth_path = Path(growth_dir).resolve()
    old_state, replayed_roots, normalized_runs, accepted_hash = _replay_previous_growth(
        growth_path, baseline_state, base_roots, base_provenance,
        old_matcher, library_hash, old_hash)
    baseline_audit = old_report['relation_revalidation']
    baseline_manifest = baseline_audit.get('accepted_input_manifest') or {}
    baseline_raw = {(str(Path(row['path']).resolve()), row['sha256'])
                    for row in baseline_audit.get('raw_input_provenance', [])}
    authenticated_inputs = set()
    for pair in baseline_manifest.get('anchored_observation_inputs', []):
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError('Original rebase has malformed accepted-observation manifest')
        receipt = (str(Path(pair[0]).resolve()), pair[1])
        if receipt not in baseline_raw:
            raise ValueError('Original accepted observation lacks its verified raw receipt')
        authenticated_inputs.add(receipt)
    for run in normalized_runs:
        for source in run.get('snapshot_inputs', []):
            authenticated_inputs.add((str(Path(source['path']).resolve()), source['sha256']))
    authenticated_observation_inputs = [
        {'path': path, 'sha256': digest} for path, digest in sorted(authenticated_inputs)]
    roots = _state_graph_rows(old_state)
    full_edge_map = _relation_map(roots, 'All rebound state roots')
    replay_edge_map = _relation_map(replayed_roots, 'Replayed growth roots')
    if ({pid: edges for pid, edges in full_edge_map.items() if edges}
            != {pid: edges for pid, edges in replay_edge_map.items() if edges}):
        raise ValueError('Old-map state relations differ from the replayed genuine-growth graph')
    old_ids, _, old_relations = state_rows(old_state)
    _, new_labels = identity_source(new_matcher, library_hash, new_hash)
    _, old_labels = identity_source(old_matcher, library_hash, old_hash)
    if old_labels != new_labels:
        raise ValueError('Local artist, album, or title equivalence labels changed')

    rebound_state = _rebind_state_map_hash(old_state, new_hash)
    reproduced = plan_genius_update(rebound_state, roots, new_matcher, library_hash, new_hash)['next_state']
    if reproduced['state_sha256'] != rebound_state['state_sha256']:
        raise ValueError('Rebound state cannot be reproduced with the new identity map')
    new_ids, _, new_relations = state_rows(rebound_state)
    if (old_ids != new_ids or old_state['rows'] != rebound_state['rows']
            or old_relations != new_relations):
        raise ValueError('Rebinding changed PID/GID assignments, metadata, or relation bytes')
    if _relation_map(roots, 'All rebound graph roots') != _relation_map(
            _state_graph_rows(rebound_state), 'Rebound state graph'):
        raise ValueError('Rebound state does not preserve replayed genuine-growth edges')

    output_dir.mkdir(parents=True)
    state_path = output_dir / 'state.json'
    relations_path = output_dir / 'relations.json'
    graph_path = output_dir / 'accepted-baseline-graphs.json'
    report_path = output_dir / 'identity-map-rebinding-report.json'
    write_json(state_path, rebound_state)
    write_json(relations_path, {'schema_version': 1, 'source_library_sha256': library_hash,
                                'source_state_sha256': rebound_state['state_sha256'],
                                'relation_rows': roots})
    source_audit = old_report['relation_revalidation']
    binding = {
        'schema_version': 2, 'library_sha256': library_hash,
        'old_identity_map_sha256': old_hash, 'new_identity_map_sha256': new_hash,
        'old_identity_map': {'path': str(old_path), 'sha256': old_hash},
        'new_identity_map': {'path': str(new_path), 'sha256': new_hash},
        'track_snapshot': {'path': str(snapshot_path), 'sha256': snapshot_hash},
        'alias_application_receipt': {'path': str(app_path), 'sha256': sha(app_bytes)},
        'review_decisions': {'path': str(review_path), 'sha256': sha(review_bytes)},
        'proposals': {'path': str(proposal_path), 'sha256': sha(proposal_bytes)},
        'title_alias_application_receipt': {'path': str(title_receipt_path),
                                            'sha256': sha(title_receipt_bytes)},
        'title_review_decisions': {'path': str(title_review_path),
                                   'sha256': sha(title_review_bytes)},
        'title_proposals': {'path': str(title_proposal_path),
                            'sha256': sha(title_proposal_bytes)},
        'source_rebase': {
            'graph_export_path': str(Path(baseline_graph_export_path).resolve()),
            'graph_export_sha256': base_provenance['graph_export_sha256'],
            'rebase_report_path': str(Path(rebase_report_path).resolve()),
            'rebase_report_sha256': base_provenance['rebase_report_sha256'],
            'state_path': str(Path(baseline_state_path).resolve()),
            'state_file_sha256': base_provenance['state_file_sha256'],
        },
        'source_growth': {'path': str(growth_path), 'accepted_graphs_sha256': accepted_hash},
        'authenticated_observation_inputs': authenticated_observation_inputs,
        'authenticated_observation_input_count': len(authenticated_observation_inputs),
        'accepted_input_manifest_sha256': base_provenance['accepted_input_manifest_sha256'],
        'old_state_sha256': old_state['state_sha256'],
        'new_state_sha256': rebound_state['state_sha256'],
        'growth_run_count_replayed_under_old_map': len(normalized_runs),
        'local_track_metadata_unchanged': True,
        'local_equivalence_labels_unchanged': True,
        'pid_gid_assignments_unchanged': True,
        'metadata_bytes_unchanged': True,
        'relation_bytes_unchanged': True,
        'old_growth_replayed_with_old_map': True,
        'activated_whole_credit_alias_count': len(new_aliases) - len(old_aliases),
        'activated_title_alias_count': len(title_alias_delta),
        'recording_identity_verified': False,
        'recording_edges_added_by_rebinding': 0,
    }
    relation_audit = deepcopy(source_audit)
    relation_audit['output_directed_edge_count'] = sum(
        len(row['ordered_target_pids']) for row in roots)
    report = {
        'schema_version': 1,
        'producer': 'opengenius-typed-identity-map-rebinding-v2',
        'zero_id_library_sha256': library_hash,
        'identity_map_sha256': new_hash,
        'next_state_sha256': rebound_state['state_sha256'],
        'indexed_tracks': len(new_ids),
        'source_state_path': str(state_path.resolve()),
        'identity_map_rebinding': binding,
        'relation_revalidation': relation_audit,
        'raw_input_provenance': relation_audit['raw_input_provenance'],
    }
    write_json(report_path, report)
    report_hash = sha(report_path.read_bytes())
    graph_doc = {
        'schema_version': 1,
        'producer': 'verified-identity-map-rebound-state-graph-export',
        'source_state_path': str(state_path.resolve()),
        'source_state_sha256': sha(state_path.read_bytes()),
        'source_identity_map_rebinding_report_sha256': report_hash,
        'accepted_roots': roots,
        'identity_status': 'unverified_metadata_candidate',
    }
    write_json(graph_path, graph_doc)
    return {
        'output_dir': str(output_dir), 'identity_map_sha256': new_hash,
        'old_state_sha256': old_state['state_sha256'],
        'new_state_sha256': rebound_state['state_sha256'],
        'replayed_old_growth_runs': len(normalized_runs),
        'preserved_tracks': len(new_ids),
        'preserved_directed_edges': relation_audit['output_directed_edge_count'],
        'activated_whole_credit_aliases': binding['activated_whole_credit_alias_count'],
        'activated_title_aliases': binding['activated_title_alias_count'],
        'recording_identity_verified': False, 'recording_edges_added': 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', required=True, type=Path)
    parser.add_argument('--old-identity-map', required=True, type=Path)
    parser.add_argument('--new-identity-map', required=True, type=Path)
    parser.add_argument('--alias-application-receipt', required=True, type=Path)
    parser.add_argument('--review-decisions', required=True, type=Path)
    parser.add_argument('--proposals', required=True, type=Path)
    parser.add_argument('--title-alias-application-receipt', required=True, type=Path)
    parser.add_argument('--title-review-decisions', required=True, type=Path)
    parser.add_argument('--title-proposals', required=True, type=Path)
    parser.add_argument('--baseline-state', required=True, type=Path)
    parser.add_argument('--baseline-graph-export', required=True, type=Path)
    parser.add_argument('--rebase-report', required=True, type=Path)
    parser.add_argument('--growth-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = rebind_radio_growth(
            track_snapshot=args.track_snapshot,
            old_identity_map_path=args.old_identity_map,
            new_identity_map_path=args.new_identity_map,
            alias_application_receipt_path=args.alias_application_receipt,
            review_decisions_path=args.review_decisions,
            proposals_path=args.proposals,
            title_alias_application_receipt_path=args.title_alias_application_receipt,
            title_review_decisions_path=args.title_review_decisions,
            title_proposals_path=args.title_proposals,
            baseline_state_path=args.baseline_state,
            baseline_graph_export_path=args.baseline_graph_export,
            rebase_report_path=args.rebase_report,
            growth_dir=args.growth_dir, output_dir=args.output)
    except Exception as error:
        parser.exit(1, 'Stopped (' + type(error).__name__ + '): ' + str(error) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
