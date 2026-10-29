"""Merge authenticated YT Music radio/related graphs onto a verified rebase.

The baseline comes from a typed rebase graph export plus its rebase report. This
tool never treats that export as a raw recommendation snapshot and never reads a
catalog-derived graph. It writes a new virtual state and source receipts only.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

from evaluate_ytmusic_batch import collect_graphs, merge_graphs
from music_identity_map import IdentityMap, load_library
from plan_genius_update import _source as _identity_source, plan_genius_update
from seed_video_evidence import select_seed_video_evidence
from probe_ytmusic_batch import resolve_seed_search
from radio_snapshot_proof import radio_snapshot_proof_validation_pass
from update_genius_bundle import state_rows
from genius_format import parse_similarities


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def _resolve_input(path):
    value = Path(path)
    if value.is_absolute():
        return value.resolve()
    return (Path(__file__).resolve().parents[1] / value).resolve()


def _relation_map(rows, name):
    if not isinstance(rows, list):
        raise ValueError(name + ' must contain relation rows')
    out = {}
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('root_pid'), str)
                or not isinstance(row.get('ordered_target_pids'), list)
                or row['root_pid'] in out):
            raise ValueError(name + ' has malformed or duplicate roots')
        targets = row['ordered_target_pids']
        if any(not isinstance(pid, str) for pid in targets) or len(targets) != len(set(targets)):
            raise ValueError(name + ' has malformed or duplicate targets')
        out[row['root_pid']] = list(targets)
    return out


def _verify_rebase_receipt(graph_path, report_path, state_path,
                           library_hash, identity_hash):
    graph_bytes = Path(graph_path).read_bytes()
    report_bytes = Path(report_path).read_bytes()
    state_bytes = Path(state_path).read_bytes()
    graph_doc, report, state = (json.loads(graph_bytes), json.loads(report_bytes),
                                json.loads(state_bytes))
    state_digest = state.get('state_sha256')
    graph_producer = graph_doc.get('producer') if isinstance(graph_doc, dict) else None
    typed_rebind = graph_producer == 'verified-identity-map-rebound-state-graph-export'
    if (not isinstance(graph_doc, dict)
            or graph_producer not in ('verified-rebased-state-graph-export',
                                      'verified-identity-map-rebound-state-graph-export')
            or graph_doc.get('source_state_sha256') != sha(state_bytes)
            or (graph_doc.get('source_identity_map_rebinding_report_sha256' if typed_rebind
                              else 'source_rebase_report_sha256') != sha(report_bytes))
            or not isinstance(report, dict)
            or report.get('schema_version') != 1
            or report.get('next_state_sha256') != state_digest
            or report.get('zero_id_library_sha256') != library_hash
            or report.get('identity_map_sha256') != identity_hash
            or _resolve_input(graph_doc.get('source_state_path')) != Path(state_path).resolve()):
        raise ValueError('Rebase graph export, report, state, map, or Library hashes differ')
    audit = report.get('relation_revalidation')
    if (not isinstance(audit, dict)
            or audit.get('all_original_observation_hashes_verified') is not True
            or audit.get('all_retained_edges_same_pid_and_fresh_metadata_match') is not True
            or type(audit.get('output_directed_edge_count')) is not int
            or type(audit.get('dropped_old_directed_edge_count')) is not int
            or type(audit.get('new_matched_edge_count')) is not int):
        raise ValueError('Rebase report does not prove its verified recommendation graph transformation')
    manifest = audit.get('accepted_input_manifest')
    raw_inputs = audit.get('raw_input_provenance')
    if (not isinstance(manifest, dict)
            or not isinstance(raw_inputs, list)
            or len(raw_inputs) != audit.get('source_observation_count')
            or not isinstance(manifest.get('anchored_observation_inputs'), list)):
        raise ValueError('Rebase report lacks its accepted-snapshot source manifest')
    verified = set()
    for source in raw_inputs:
        if (not isinstance(source, dict) or not isinstance(source.get('path'), str)
                or not isinstance(source.get('sha256'), str)):
            raise ValueError('Malformed raw observation receipt in rebase report')
        path = _resolve_input(source['path'])
        if sha(path.read_bytes()) != source['sha256']:
            raise ValueError('A raw observation bound by the rebase report changed')
        verified.add((str(path), source['sha256']))
    for pair in manifest['anchored_observation_inputs']:
        if (not isinstance(pair, list) or len(pair) != 2
                or (str(_resolve_input(pair[0])), pair[1]) not in verified):
            raise ValueError('Accepted input manifest is not contained in verified raw receipts')
    if typed_rebind:
        if report.get('producer') == 'opengenius-typed-identity-map-rebinding-v2':
            proof_inputs = _verify_identity_map_rebinding(
                graph_doc, report, state, library_hash, identity_hash,
                Path(state_path).resolve())
        elif report.get('producer') == 'opengenius-typed-identity-map-alias-chain-growth-rebinding-v1':
            from rebind_radio_growth_alias_chain import verify_alias_chain_growth_rebinding_report
            proof_inputs = verify_alias_chain_growth_rebinding_report(
                graph_doc=graph_doc, report=report, state=state,
                library_hash=library_hash, identity_hash=identity_hash,
                state_path=Path(state_path).resolve())
        elif report.get('producer') == 'opengenius-typed-album-alias-growth-rebinding-v1':
            from rebind_radio_growth_identity import verify_album_alias_growth_rebinding_report
            proof_inputs = verify_album_alias_growth_rebinding_report(
                graph_doc=graph_doc, report=report, state=state,
                library_hash=library_hash, identity_hash=identity_hash,
                state_path=Path(state_path).resolve())
        elif report.get('producer') == 'opengenius-typed-album-alias-chain-growth-rebinding-v1':
            from rebind_radio_growth_alias_chain import verify_album_alias_chain_growth_rebinding_report
            proof_inputs = verify_album_alias_chain_growth_rebinding_report(
                graph_doc=graph_doc, report=report, state=state,
                library_hash=library_hash, identity_hash=identity_hash,
                state_path=Path(state_path).resolve())
        elif report.get('producer') == 'opengenius-typed-album-alias-append-chain-growth-rebinding-v1':
            from rebind_radio_growth_album_append_chain import verify_album_alias_append_chain_growth_rebinding_report
            proof_inputs = verify_album_alias_append_chain_growth_rebinding_report(
                graph_doc=graph_doc, report=report, state=state,
                library_hash=library_hash, identity_hash=identity_hash,
                state_path=Path(state_path).resolve())
        elif report.get('producer') == 'opengenius-typed-native-genius-baseline-alias-child-v1':
            from rebind_native_baseline_alias_chain import verify_native_alias_child_report
            native_proof = verify_native_alias_child_report(
                graph_doc=graph_doc, report=report, state=state,
                library_hash=library_hash, identity_hash=identity_hash,
                graph_path=Path(graph_path).resolve(),
                report_path=Path(report_path).resolve(),
                state_path=Path(state_path).resolve(),
                repo_root=Path(__file__).resolve().parents[1])
            if (not isinstance(native_proof, dict)
                    or native_proof.get('producer') != report.get('producer')
                    or native_proof.get('verified') is not True
                    or native_proof.get('library_sha256') != library_hash
                    or native_proof.get('identity_map_sha256') != identity_hash
                    or native_proof.get('track_count') != 3004
                    or native_proof.get('assignment_count') != 515
                    or native_proof.get('directed_edge_count') != 3341
                    or native_proof.get('recording_identity_verified') is not False
                    or native_proof.get('recording_edges_added') != 0
                    or not isinstance(native_proof.get('proof_input_receipts'), list)):
                raise ValueError('Native baseline alias child verifier returned an invalid proof')
            proof_inputs = native_proof['proof_input_receipts']
        else:
            raise ValueError('Unsupported typed identity-map rebind report producer')
    else:
        proof_inputs = []
    return graph_doc, report, state, {
        'graph_export_path': str(Path(graph_path).resolve()),
        'graph_export_sha256': sha(graph_bytes),
        'rebase_report_path': str(Path(report_path).resolve()),
        'rebase_report_sha256': sha(report_bytes),
        'state_path': str(Path(state_path).resolve()),
        'state_file_sha256': sha(state_bytes),
        'proof_input_receipts': proof_inputs,
        'raw_observation_receipt_count': len(raw_inputs),
        'anchored_observation_count': len(manifest['anchored_observation_inputs']),
        'accepted_input_manifest_sha256': sha(json.dumps(
            manifest, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode('utf-8')),
    }


def verify_typed_identity_map_rebinding(*, graph_export_path, rebase_report_path,
                                        state_path, old_identity_map_path,
                                        new_identity_map_path, library_sha256):
    """Public offline verifier for a typed identity-map transition.

    Replays the source graph and all map/title decision receipts, then checks the
    caller's exact old/new map paths and hashes. Returns authenticated raw
    observation inputs for consumers that need to rematch those saved sources.
    """
    old_path = Path(old_identity_map_path).resolve()
    new_path = Path(new_identity_map_path).resolve()
    old_hash, new_hash = sha(old_path.read_bytes()), sha(new_path.read_bytes())
    graph, report, state, provenance = _verify_rebase_receipt(
        graph_export_path, rebase_report_path, state_path, library_sha256, new_hash)
    if report.get('producer') == 'opengenius-typed-album-alias-growth-rebinding-v1':
        binding = report.get('album_alias_growth_rebinding') or {}
        parent = binding.get('parent_typed_growth') or {}
        old_ref = parent.get('old_identity_map') or {}
        new_ref = binding.get('output_identity_map') or {}
        snapshot_ref = binding.get('track_snapshot') or {}
        snapshot_path = _resolve_input(snapshot_ref.get('path'))
        if (not snapshot_path.is_file() or sha(snapshot_path.read_bytes()) != snapshot_ref.get('sha256')
                or old_ref.get('sha256') != old_hash
                or _resolve_input(old_ref.get('path')) != old_path
                or new_ref.get('sha256') != new_hash
                or _resolve_input(new_ref.get('path')) != new_path):
            raise ValueError('Typed album-alias growth transition does not bind expected maps')
        proof_inputs = {
            (Path(row['path']).resolve().as_posix(), row['sha256'])
            for row in provenance.get('proof_input_receipts', [])
        }
        proof_inputs.update({
            (str(Path(graph_export_path).resolve()), sha(Path(graph_export_path).read_bytes())),
            (str(Path(rebase_report_path).resolve()), sha(Path(rebase_report_path).read_bytes())),
            (str(Path(state_path).resolve()), sha(Path(state_path).read_bytes())),
            (str(snapshot_path), snapshot_ref['sha256']),
            (str(old_path), old_hash), (str(new_path), new_hash),
        })
        return {
            'verified': True, 'library_sha256': library_sha256,
            'old_identity_map_path': str(old_path), 'old_identity_map_sha256': old_hash,
            'new_identity_map_path': str(new_path), 'new_identity_map_sha256': new_hash,
            'report_path': str(Path(rebase_report_path).resolve()),
            'report_sha256': sha(Path(rebase_report_path).read_bytes()),
            'graph_export_path': str(Path(graph_export_path).resolve()),
            'graph_export_sha256': sha(Path(graph_export_path).read_bytes()),
            'state_path': str(Path(state_path).resolve()),
            'state_file_sha256': sha(Path(state_path).read_bytes()),
            'state_sha256': state.get('state_sha256'),
            'track_snapshot_receipt': snapshot_ref,
            'accepted_input_manifest_sha256': binding.get('accepted_input_manifest_sha256'),
            'authenticated_observation_inputs': binding.get('authenticated_observation_inputs'),
            'proof_input_receipts': [
                {'path': path, 'sha256': digest}
                for path, digest in sorted(proof_inputs)
            ],
            'recording_identity_verified': False,
            'recording_edges_added_by_rebinding': 0,
        }
    binding = report.get('identity_map_rebinding') or {}
    recorded_old = binding.get('old_identity_map') or {}
    recorded_new = binding.get('new_identity_map') or {}
    track_snapshot = binding.get('track_snapshot') or {}
    snapshot_path = _resolve_input(track_snapshot.get('path'))
    if not snapshot_path.is_file() or sha(snapshot_path.read_bytes()) != track_snapshot.get('sha256'):
        raise ValueError('Typed map transition track-snapshot receipt is missing or changed')
    if (recorded_old.get('sha256') != old_hash
            or _resolve_input(recorded_old.get('path')) != old_path
            or recorded_new.get('sha256') != new_hash
            or _resolve_input(recorded_new.get('path')) != new_path
            or binding.get('old_identity_map_sha256') != old_hash
            or binding.get('new_identity_map_sha256') != new_hash):
        raise ValueError('Typed map transition does not bind the expected old/new map inputs')
    if report.get('producer') != 'opengenius-typed-identity-map-rebinding-v2':
        raise ValueError('Unsupported typed identity-map transition proof version')
    proof_inputs = {
        (str(Path(row['path']).resolve()), row['sha256'])
        for row in provenance.get('proof_input_receipts', [])
    }
    proof_inputs.update({
        (str(Path(graph_export_path).resolve()), sha(Path(graph_export_path).read_bytes())),
        (str(Path(rebase_report_path).resolve()), sha(Path(rebase_report_path).read_bytes())),
        (str(Path(state_path).resolve()), sha(Path(state_path).read_bytes())),
        (str(snapshot_path), track_snapshot['sha256']),
        (str(old_path), old_hash), (str(new_path), new_hash),
    })
    return {
        'verified': True,
        'library_sha256': library_sha256,
        'old_identity_map_path': str(old_path),
        'old_identity_map_sha256': old_hash,
        'new_identity_map_path': str(new_path),
        'new_identity_map_sha256': new_hash,
        'report_path': str(Path(rebase_report_path).resolve()),
        'report_sha256': sha(Path(rebase_report_path).read_bytes()),
        'graph_export_path': str(Path(graph_export_path).resolve()),
        'graph_export_sha256': sha(Path(graph_export_path).read_bytes()),
        'state_path': str(Path(state_path).resolve()),
        'state_file_sha256': sha(Path(state_path).read_bytes()),
        'state_sha256': state.get('state_sha256'),
        'track_snapshot_receipt': {'path': str(snapshot_path),
                                   'sha256': track_snapshot['sha256']},
        'accepted_input_manifest_sha256': binding.get('accepted_input_manifest_sha256'),
        'authenticated_observation_inputs': binding.get('authenticated_observation_inputs'),
        'proof_input_receipts': [
            {'path': path, 'sha256': digest}
            for path, digest in sorted(proof_inputs)
        ],
        'recording_identity_verified': False,
        'recording_edges_added_by_rebinding': 0,
    }


def _verify_identity_map_rebinding(graph_doc, report, state, library_hash,
                                   identity_hash, rebound_state_path):
    """Replay old-map growth and prove a metadata-only map transition."""
    from plan_genius_update import _hash as state_hash
    from music_identity_map import IdentityMap

    binding = report.get('identity_map_rebinding')
    if (not isinstance(binding, dict)
            or binding.get('schema_version') != 2
            or binding.get('new_identity_map_sha256') != identity_hash
            or binding.get('library_sha256') != library_hash
            or any(binding.get(key) is not True for key in (
                'local_track_metadata_unchanged', 'local_equivalence_labels_unchanged',
                'pid_gid_assignments_unchanged', 'metadata_bytes_unchanged',
                'relation_bytes_unchanged', 'old_growth_replayed_with_old_map'))):
        raise ValueError('Typed identity-map rebinding proof is incomplete')
    if (report.get('producer') != 'opengenius-typed-identity-map-rebinding-v2'
            or binding.get('activated_title_alias_count', 0) < 1):
        raise ValueError('Typed identity-map rebind proof lacks reviewed title aliases')
    proof_inputs = {}

    def record_proof_input(path, data):
        resolved = str(path.resolve())
        proof_inputs[(resolved, sha(data))] = {'path': resolved, 'sha256': sha(data)}

    def checked_file(record, description):
        if not isinstance(record, dict) or not isinstance(record.get('path'), str) or not isinstance(record.get('sha256'), str):
            raise ValueError('Malformed ' + description + ' receipt in identity-map rebinding')
        path = _resolve_input(record['path'])
        data = path.read_bytes()
        if sha(data) != record['sha256']:
            raise ValueError(description + ' changed after identity-map rebinding')
        record_proof_input(path, data)
        return path, data

    old_map_path, old_map_bytes = checked_file(binding.get('old_identity_map'), 'old identity map')
    new_map_path, new_map_bytes = checked_file(binding.get('new_identity_map'), 'new identity map')
    if sha(new_map_bytes) != identity_hash:
        raise ValueError('Rebinding receipt points to a different current identity map')
    old_map, new_map = json.loads(old_map_bytes), json.loads(new_map_bytes)
    if old_map.get('library_sha256') != library_hash or new_map.get('library_sha256') != library_hash:
        raise ValueError('Rebound identity maps do not share this Library')
    old_aliases = old_map.pop('artist_credit_aliases', [])
    new_aliases = new_map.pop('artist_credit_aliases', [])
    old_titles = old_map.pop('track_title_aliases', [])
    new_titles = new_map.pop('track_title_aliases', [])
    if (old_map != new_map or new_aliases[:len(old_aliases)] != old_aliases
            or new_titles[:len(old_titles)] != old_titles):
        raise ValueError('Identity-map rebind changed fields beyond append-only reviewed aliases')
    if len(new_aliases) <= len(old_aliases):
        raise ValueError('Identity-map rebind did not add any reviewed credit aliases')
    if len(new_titles) <= len(old_titles):
        raise ValueError('Identity-map rebind did not add any reviewed title aliases')

    snapshot_path, snapshot_bytes = checked_file(binding.get('track_snapshot'), 'track snapshot')
    tracks, snapshot_library_hash, _ = load_library(track_snapshot=snapshot_path)
    if snapshot_library_hash != library_hash:
        raise ValueError('Rebinding snapshot belongs to a different Library')
    old_matcher = IdentityMap(json.loads(old_map_bytes), tracks, library_hash)
    new_matcher = IdentityMap(json.loads(new_map_bytes), tracks, library_hash)
    _, old_labels = _identity_source(old_matcher, library_hash, binding['old_identity_map_sha256'])
    _, new_labels = _identity_source(new_matcher, library_hash, identity_hash)
    if old_labels != new_labels:
        raise ValueError('Local equivalence labels changed during identity-map rebind')

    # Recreate the intermediate 29-credit map from the original root review,
    # then independently reproduce the title-only stage from its strict receipt.
    from music_identity_map import apply_artist_credit_alias_review
    credit_receipt_path, credit_receipt_bytes = checked_file(
        binding.get('alias_application_receipt'), 'credit alias application receipt')
    credit_review_path, credit_review_bytes = checked_file(
        binding.get('review_decisions'), 'credit alias review decisions')
    credit_proposal_path, credit_proposal_bytes = checked_file(
        binding.get('proposals'), 'credit alias proposals')
    credit_receipt = json.loads(credit_receipt_bytes)
    credit_review = json.loads(credit_review_bytes)
    credit_proposals = json.loads(credit_proposal_bytes)
    credit_report_ref = credit_proposals.get('source_report') or {}
    credit_report_path = _resolve_input(credit_report_ref.get('path'))
    credit_report_bytes = credit_report_path.read_bytes()
    record_proof_input(credit_report_path, credit_report_bytes)
    if (sha(credit_report_bytes) != credit_report_ref.get('sha256')
            or credit_review.get('source_report_sha256') != credit_report_ref.get('sha256')
            or credit_receipt.get('operation') != 'apply_reviewed_whole_artist_credit_aliases'
            or credit_receipt.get('input_identity_map_sha256') != binding.get('old_identity_map_sha256')
            or credit_receipt.get('review_decisions_sha256') != sha(credit_review_bytes)
            or credit_receipt.get('proposal_sha256') != sha(credit_proposal_bytes)
            or credit_receipt.get('recording_identity_verified') is not False):
        raise ValueError('Whole-credit review/application receipt chain is incomplete')
    composite = json.loads(credit_report_bytes)
    audit_refs = {row.get('path'): row.get('sha256') for row in composite.get('source_audits', [])}
    review_refs = {row.get('path'): row.get('sha256') for row in composite.get('prior_root_reviews', [])}
    if not audit_refs or not review_refs:
        raise ValueError('Composite whole-credit provenance omits candidate audits or prior reviews')
    for refs in (audit_refs, review_refs):
        for path_value, expected_hash in refs.items():
            if not isinstance(path_value, str) or not isinstance(expected_hash, str):
                raise ValueError('Malformed composite whole-credit provenance reference')
            path = _resolve_input(path_value)
            data = path.read_bytes()
            if sha(data) != expected_hash:
                raise ValueError('A source audit or earlier root review changed')
            record_proof_input(path, data)
    candidate_refs = {(row['candidate_audit'].get('path'), row['candidate_audit'].get('sha256'))
                      for row in credit_proposals.get('proposals', [])
                      if isinstance(row.get('candidate_audit'), dict)}
    valid_audits = {(path, digest) for path, digest in audit_refs.items()}
    if not candidate_refs or not candidate_refs.issubset(valid_audits):
        raise ValueError('Whole-credit proposals are not bound to the composite candidate audits')
    expected_credit_map = apply_artist_credit_alias_review(
        json.loads(old_map_bytes), credit_review, credit_proposals, tracks,
        library_hash, binding['old_identity_map_sha256'], sha(credit_proposal_bytes))
    title_receipt_path, title_receipt_bytes = checked_file(
        binding.get('title_alias_application_receipt'), 'title alias application receipt')
    title_review_path, title_review_bytes = checked_file(
        binding.get('title_review_decisions'), 'title review decisions')
    title_proposal_path, title_proposal_bytes = checked_file(
        binding.get('title_proposals'), 'title alias proposals')
    title_receipt = json.loads(title_receipt_bytes)
    title_intermediate = _resolve_input(title_receipt.get('input_identity_map_path'))
    intermediate_bytes = title_intermediate.read_bytes()
    record_proof_input(title_intermediate, intermediate_bytes)
    if (sha(intermediate_bytes) != credit_receipt.get('output_identity_map_sha256')
            or json.loads(intermediate_bytes) != expected_credit_map
            or title_receipt.get('output_identity_map_sha256') != identity_hash
            or title_receipt.get('review_decisions_sha256') != sha(title_review_bytes)
            or title_receipt.get('proposal_sha256') != sha(title_proposal_bytes)):
        raise ValueError('Intermediate credit map does not follow exact reviewed credit aliases')
    from apply_artist_title_aliases import verify_title_alias_transition
    verify_title_alias_transition(
        track_snapshot=snapshot_path,
        input_identity_map_path=title_intermediate,
        output_identity_map_path=new_map_path,
        proposals_path=title_proposal_path,
        review_decisions_path=title_review_path,
        receipt_path=title_receipt_path)
    title_proposals_doc = json.loads(title_proposal_bytes)
    title_report_ref = title_proposals_doc.get('source_report') or {}
    title_report_path = _resolve_input(title_report_ref.get('path'))
    title_report_bytes = title_report_path.read_bytes()
    if sha(title_report_bytes) != title_report_ref.get('sha256'):
        raise ValueError('Title candidate source report changed')
    record_proof_input(title_report_path, title_report_bytes)
    title_report_doc = json.loads(title_report_bytes)
    for source in title_report_doc.get('input_files', []):
        path = _resolve_input(source.get('path'))
        data = path.read_bytes()
        if sha(data) != source.get('sha256'):
            raise ValueError('A cached title-candidate observation changed')
        record_proof_input(path, data)

    baseline = binding.get('source_rebase')
    if not isinstance(baseline, dict):
        raise ValueError('Identity-map rebinding omits its original verified rebase inputs')
    for path_key, hash_key in (('graph_export_path', 'graph_export_sha256'),
                               ('rebase_report_path', 'rebase_report_sha256'),
                               ('state_path', 'state_file_sha256')):
        path = _resolve_input(baseline.get(path_key))
        if sha(path.read_bytes()) != baseline.get(hash_key):
            raise ValueError('An original rebase input changed after identity-map rebinding')
    base_graph, base_report, base_state, base_provenance = _verify_rebase_receipt(
        baseline.get('graph_export_path'), baseline.get('rebase_report_path'),
        baseline.get('state_path'), library_hash, binding['old_identity_map_sha256'])
    for key, path_key in (('graph_export_sha256', 'graph_export_path'),
                          ('rebase_report_sha256', 'rebase_report_path'),
                          ('state_file_sha256', 'state_path')):
        path = _resolve_input(baseline[path_key])
        record_proof_input(path, path.read_bytes())
    for row in base_report['relation_revalidation'].get('raw_input_provenance', []):
        path = _resolve_input(row['path'])
        data = path.read_bytes()
        if sha(data) != row['sha256']:
            raise ValueError('An authenticated baseline observation changed')
        record_proof_input(path, data)
    base_roots = base_graph.get('accepted_roots')
    if _relation_map(base_roots, 'Rebinding source baseline') != _relation_map(
            _state_graph_rows(base_state), 'Rebinding source state'):
        raise ValueError('Rebinding source baseline graph differs from its state')
    source_state, source_roots = base_state, base_roots
    normalized_runs = []
    growth = binding.get('source_growth')
    if growth is not None:
        if not isinstance(growth, dict) or not isinstance(growth.get('path'), str):
            raise ValueError('Malformed old-map growth receipt in identity-map rebinding')
        growth_dir = _resolve_input(growth['path'])
        accepted_path = growth_dir / 'accepted-graphs.json'
        accepted_bytes = accepted_path.read_bytes()
        if sha(accepted_bytes) != growth.get('accepted_graphs_sha256'):
            raise ValueError('Old-map growth receipt changed after identity-map rebinding')
        source_state, source_roots, normalized_runs, accepted_hash = _replay_previous_growth(
            growth_dir, base_state, base_roots, base_provenance, old_matcher,
            library_hash, binding['old_identity_map_sha256'])
        if accepted_hash != growth.get('accepted_graphs_sha256'):
            raise ValueError('Old-map growth replay used a different receipt')
        record_proof_input(accepted_path, accepted_bytes)
        for run in normalized_runs:
            for key in ('collector_summary', 'seed_manifest'):
                receipt = run.get(key) or {}
                if receipt.get('path'):
                    path = _resolve_input(receipt['path'])
                    record_proof_input(path, path.read_bytes())
            sidecar = (run.get('seed_manifest') or {}).get('seed_video_evidence')
            if sidecar and sidecar.get('path'):
                path = _resolve_input(sidecar['path'])
                record_proof_input(path, path.read_bytes())
            for receipt in run.get('snapshot_inputs', []):
                path = _resolve_input(receipt['path'])
                record_proof_input(path, path.read_bytes())
    if source_state['state_sha256'] != binding.get('old_state_sha256'):
        raise ValueError('Old-map state does not match the replayed state in rebinding receipt')
    graph_roots = _relation_map(graph_doc.get('accepted_roots'), 'Rebound accepted roots')
    replay_roots = _relation_map(source_roots, 'Replayed old-map roots')
    source_state_roots = _relation_map(_state_graph_rows(source_state), 'Replayed old-map state roots')
    if (graph_roots != source_state_roots
            or {pid: edges for pid, edges in graph_roots.items() if edges}
               != {pid: edges for pid, edges in replay_roots.items() if edges}):
        raise ValueError('Rebound graph differs from old-map graph replay')
    baseline_audit = base_report['relation_revalidation']
    baseline_manifest = baseline_audit.get('accepted_input_manifest') or {}
    verified_baseline_raw = {(str(_resolve_input(row['path'])), row['sha256'])
                             for row in baseline_audit.get('raw_input_provenance', [])}
    authenticated_inputs = set()
    for pair in baseline_manifest.get('anchored_observation_inputs', []):
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError('Original rebase has malformed accepted-observation manifest')
        receipt = (str(_resolve_input(pair[0])), pair[1])
        if receipt not in verified_baseline_raw:
            raise ValueError('Original accepted observation lacks its verified raw receipt')
        authenticated_inputs.add(receipt)
    if growth is not None:
        for run in normalized_runs:
            for source in run.get('snapshot_inputs', []):
                authenticated_inputs.add((str(_resolve_input(source['path'])), source['sha256']))
    unique_inputs = [{'path': path, 'sha256': digest}
                     for path, digest in sorted(authenticated_inputs)]
    if (binding.get('authenticated_observation_inputs') != unique_inputs
            or binding.get('authenticated_observation_input_count') != len(unique_inputs)):
        raise ValueError('Typed transition authenticated-input set differs from accepted replay receipts')
    expected = json.loads(json.dumps(source_state))
    expected['source']['identity_map_sha256'] = identity_hash
    unsigned = {key: value for key, value in expected.items() if key != 'state_sha256'}
    expected['state_sha256'] = state_hash(unsigned)
    if expected != state or state.get('state_sha256') != binding.get('new_state_sha256'):
        raise ValueError('Rebound state changed old state data beyond its source map hash')
    update = plan_genius_update(expected, source_roots, new_matcher, library_hash, identity_hash)
    if update['next_state']['state_sha256'] != expected['state_sha256']:
        raise ValueError('Rebound state does not reproduce under the new matcher')
    if ({pid: edges for pid, edges in _relation_map(
            _state_graph_rows(state), 'Rebound state relation rows').items() if edges}
            != {pid: edges for pid, edges in replay_roots.items() if edges}):
        raise ValueError('Rebound state relation bytes do not preserve the old graph')
    return [proof_inputs[key] for key in sorted(proof_inputs)]


def _state_graph_rows(state):
    ids, _, relations = state_rows(state)
    reverse = {gid: pid for pid, gid in ids.items()}
    roots = []
    for row in state['rows']:
        gid = row['genius_id']
        _, target_ids = parse_similarities(relations[gid])
        roots.append({'root_pid': row['persistent_id'],
                      'ordered_target_pids': [reverse[target] for target in target_ids]})
    return roots


def _append_graphs(old_roots, new_roots):
    by_root = {row['root_pid']: {
        'root_pid': row['root_pid'],
        'ordered_target_pids': list(row['ordered_target_pids'])}
        for row in old_roots}
    old_edges = {root: list(row['ordered_target_pids']) for root, row in by_root.items()}
    for row in new_roots:
        merged = by_root.setdefault(row['root_pid'], {
            'root_pid': row['root_pid'], 'ordered_target_pids': []})
        seen = set(merged['ordered_target_pids'])
        for pid in row['ordered_target_pids']:
            if pid not in seen:
                merged['ordered_target_pids'].append(pid)
                seen.add(pid)
    roots = [by_root[pid] for pid in sorted(by_root)]
    if any(by_root[pid]['ordered_target_pids'][:len(edges)] != edges
           for pid, edges in old_edges.items()):
        raise ValueError('An original recommendation-edge prefix changed during merge')
    return roots, old_edges


def _validate_run_receipt_inner(run, tracks, matcher, library_hash, identity_hash):
    """Recheck one completed/authenticated collector run and its saved inputs."""
    summary_info = run.get('collector_summary')
    seed_info = run.get('seed_manifest')
    snapshot_inputs = run.get('snapshot_inputs')
    if (not isinstance(summary_info, dict) or not isinstance(summary_info.get('path'), str)
            or not isinstance(summary_info.get('sha256'), str)
            or not isinstance(seed_info, dict) or not isinstance(seed_info.get('path'), str)
            or not isinstance(seed_info.get('sha256'), str)
            or not isinstance(snapshot_inputs, list) or not snapshot_inputs):
        raise ValueError('Growth run lacks summary, seed manifest, or snapshot receipts')
    summary_path = Path(summary_info['path']).resolve()
    summary_bytes = summary_path.read_bytes()
    if sha(summary_bytes) != summary_info['sha256']:
        raise ValueError('A previous collector summary changed')
    collector = json.loads(summary_bytes)
    completed = (collector.get('status') == 'completed'
                 and collector.get('account_authentication_verified') is True)
    boundary_stopped = (
        collector.get('status') == 'stopped'
        and collector.get('account_authentication_verified') is True
        and type(collector.get('http_requests')) is int
        and type(collector.get('request_budget')) is int
        and collector['http_requests'] == collector['request_budget']
        and collector.get('error_type') == 'RuntimeError'
        and isinstance(collector.get('error_location'), dict)
        and collector['error_location'].get('function') == 'request'
        and 'http_error_status' not in collector)
    if (type(collector.get('schema_version')) is not int or collector['schema_version'] != 1
            or not (completed or boundary_stopped)
            or collector.get('library_sha256') != library_hash
            or type(collector.get('http_requests')) is not int
            or type(collector.get('request_budget')) is not int
            or (summary_info.get('http_requests') is not None
                and summary_info.get('http_requests') != collector['http_requests'])
            or (summary_info.get('request_budget') is not None
                and summary_info.get('request_budget') != collector['request_budget'])
            or not 0 < collector['http_requests'] <= collector['request_budget']):
        raise ValueError('A previous collector run is not authenticated for this Library')
    manifest_path = Path(seed_info['path']).resolve()
    manifest_bytes = manifest_path.read_bytes()
    if sha(manifest_bytes) != seed_info['sha256']:
        raise ValueError('A previous collector seed manifest changed')
    manifest = json.loads(manifest_bytes)
    if (manifest.get('library_sha256') != library_hash
            or manifest.get('identity_map_sha256') != identity_hash
            or not isinstance(manifest.get('seeds'), list)
            or not manifest['seeds']
            or (seed_info.get('seed_count') is not None
                and seed_info.get('seed_count') != len(manifest['seeds']))):
        raise ValueError('A previous collector seed manifest has foreign hashes')
    manifest_pids = [row.get('persistent_id') for row in manifest['seeds']
                     if isinstance(row, dict)]
    if len(manifest_pids) != len(manifest['seeds']) or len(set(manifest_pids)) != len(manifest_pids):
        raise ValueError('A previous collector seed manifest has duplicate or malformed PIDs')
    source_policy = run.get('source_policy')
    if source_policy not in (None, 'authenticated-watch-radio-only-v1'):
        raise ValueError('A previous run uses an unsupported recommendation source policy')
    evidence_info = seed_info.get('seed_video_evidence')
    if evidence_info is not None:
        if (not isinstance(evidence_info, dict) or not isinstance(evidence_info.get('path'), str)
                or not isinstance(evidence_info.get('sha256'), str)):
            raise ValueError('Malformed previous strict seed-video evidence receipt')
        evidence_path = Path(evidence_info['path']).resolve()
        evidence_bytes = evidence_path.read_bytes()
        if (sha(evidence_bytes) != evidence_info['sha256']
                or manifest.get('seed_video_evidence_sha256') != evidence_info['sha256']):
            raise ValueError('A previous strict seed-video sidecar changed')
        hints = select_seed_video_evidence(
            json.loads(evidence_bytes), evidence_path,
            [{'persistent_id': pid} for pid in manifest_pids], tracks, matcher,
            library_hash, identity_hash)
        unresolved_dispositions = []
        collected_ineligible_dispositions = []
        batch_dir = manifest_path.parent.parent
        intent_config = None
        for seed in manifest['seeds']:
            hint = hints.get(seed['persistent_id'])
            disposition = None
            if ('unresolved_seed_dispositions' in run
                    and source_policy == 'authenticated-watch-radio-only-v1'):
                disposition = _unresolved_seed_disposition(
                    seed, hints, source_policy=source_policy,
                    collector_summary=collector,
                    collector_summary_path=summary_path,
                    seed_manifest=manifest,
                    seed_manifest_path=manifest_path,
                    seed_video_evidence_path=evidence_path,
                    snapshot_paths=[Path(row['path']) for row in snapshot_inputs
                                    if isinstance(row, dict) and isinstance(row.get('path'), str)],
                    library_hash=library_hash, identity_hash=identity_hash,
                    tracks=tracks, matcher=matcher)
            if disposition is not None:
                unresolved_dispositions.append(disposition)
                continue
            expected_receipt_paths = {}
            if (source_policy == 'authenticated-watch-radio-only-v1'
                    and hint is None):
                if intent_config is None:
                    intent_doc = json.loads((batch_dir / 'intent.json').read_bytes())
                    intent_config = intent_doc.get('runner_config', {})
                track_ref = intent_config.get('track_snapshot', {})
                map_ref = intent_config.get('identity_map', {})
                if (not isinstance(track_ref, dict) or not isinstance(track_ref.get('path'), str)
                        or not isinstance(map_ref, dict) or not isinstance(map_ref.get('path'), str)):
                    raise ValueError('Previous radio-only run intent lacks track/map paths')
                expected_receipt_paths = {
                    'track_snapshot': Path(track_ref['path']),
                    'identity_map': Path(map_ref['path']),
                    'collector_snapshot': manifest_path.parent /
                        (seed['persistent_id'] + '.json'),
                    'collector_summary': summary_path,
                    'collector_seed_manifest': manifest_path,
                    'batch_intent': batch_dir / 'intent.json',
                    'seed_plan': batch_dir / 'seed-plan.json',
                    'seed_video_evidence': evidence_path,
                }
                if 'collected_seed_ineligible_dispositions' in run:
                    ineligible = _collected_seed_ineligible_disposition(
                        seed, hints, source_policy=source_policy,
                        collector_summary=collector,
                        collector_summary_path=summary_path,
                        seed_manifest=manifest,
                        seed_manifest_path=manifest_path,
                        seed_video_evidence_path=evidence_path,
                        snapshot_paths=[Path(row['path']) for row in snapshot_inputs
                                        if isinstance(row, dict)
                                        and isinstance(row.get('path'), str)],
                        track_snapshot_path=expected_receipt_paths['track_snapshot'],
                        identity_map_path=expected_receipt_paths['identity_map'],
                        library_hash=library_hash, identity_hash=identity_hash)
                    if ineligible is not None:
                        collected_ineligible_dispositions.append(ineligible)
                        continue
            try:
                _validate_collector_seed_video_binding(
                    seed, hints,
                    [Path(row['path']) for row in snapshot_inputs
                     if isinstance(row, dict) and isinstance(row.get('path'), str)],
                    source_policy, library_hash, identity_hash,
                    expected_receipt_paths)
            except ValueError:
                raise ValueError('A previous seed differs from its strict saved-video evidence')
        if ('unresolved_seed_dispositions' in run
                and run.get('unresolved_seed_dispositions') != unresolved_dispositions):
            raise ValueError('Previous unresolved seed dispositions differ from current receipts')
        if ('collected_seed_ineligible_dispositions' in run
                and run.get('collected_seed_ineligible_dispositions')
                != collected_ineligible_dispositions):
            raise ValueError('Previous strict-ineligible seed dispositions differ from current receipts')
    elif manifest.get('seed_video_evidence_sha256') is not None:
        raise ValueError('Previous strict seed-video sidecar receipt is missing')
    collected = {row.get('persistent_id') for row in collector.get('results', [])
                 if isinstance(row, dict) and row.get('status') == 'collected'}
    paths = []
    for source in snapshot_inputs:
        if (not isinstance(source, dict) or not isinstance(source.get('path'), str)
                or not isinstance(source.get('sha256'), str)):
            raise ValueError('Malformed previous collector snapshot receipt')
        path = Path(source['path']).resolve()
        if sha(path.read_bytes()) != source['sha256']:
            raise ValueError('A previous collector snapshot changed')
        paths.append(path)
    if source_policy not in (None, 'authenticated-watch-radio-only-v1'):
        raise ValueError('A previous run uses an unsupported recommendation source policy')
    if source_policy is None:
        for path in paths:
            snapshot = json.loads(path.read_bytes())
            endpoints = {row.get('endpoint') for row in snapshot.get('requests', [])
                         if isinstance(row, dict)}
            if (snapshot.get('account_authentication_verified') is not True
                    or snapshot.get('related_status') != 'collected'
                    or not {'watch_radio', 'watch_seed', 'song_related'} <= endpoints):
                raise ValueError('A legacy run snapshot fails its original related-required policy')
    graphs, skipped, inputs = collect_graphs(
        paths, matcher, library_hash, source_policy=source_policy,
        identity_map_sha256=identity_hash)
    if (not graphs or skipped != run.get('skipped_snapshots', [])
            or any(graph['root_pid'] not in collected for graph in graphs)):
        raise ValueError('A previous snapshot fails authenticated collector or graph validation')
    incoming_roots = merge_graphs(graphs)
    expected = _relation_map(run.get('new_graphs'), 'Previous new graph receipts')
    if expected != _relation_map(incoming_roots, 'Revalidated previous graphs'):
        raise ValueError('Previous graph receipts differ from their raw snapshots')
    normalized_run = {
        'collector_summary': {'path': str(summary_path), 'sha256': sha(summary_bytes),
                              'http_requests': collector['http_requests'],
                              'request_budget': collector['request_budget']},
        'seed_manifest': {'path': str(manifest_path), 'sha256': sha(manifest_bytes),
                          'seed_count': len(manifest['seeds'])},
        'snapshot_inputs': inputs,
        'skipped_snapshots': skipped,
        'new_graphs': [{'root_pid': row['root_pid'],
                        'ordered_target_pids': row['ordered_target_pids']}
                       for row in incoming_roots],
    }
    if source_policy is not None:
        normalized_run['source_policy'] = source_policy
    if 'source_policy_transition' in run:
        # Preserve the typed boundary receipt when a later radio-only run is
        # appended. The boundary is validated above against the exact
        # historical parent; dropping it here would make the next replay
        # unable to prove where legacy runs ended.
        normalized_run['source_policy_transition'] = run['source_policy_transition']
    if evidence_info is not None:
        normalized_run['seed_manifest']['seed_video_evidence'] = {
            'path': str(evidence_path), 'sha256': evidence_info['sha256']}
    if 'unresolved_seed_dispositions' in run:
        normalized_run['unresolved_seed_dispositions'] = unresolved_dispositions
    if 'collected_seed_ineligible_dispositions' in run:
        normalized_run['collected_seed_ineligible_dispositions'] = collected_ineligible_dispositions
    if run.get('resulting_state_sha256'):
        normalized_run['resulting_state_sha256'] = run['resulting_state_sha256']
    return incoming_roots, normalized_run


def _validate_run_receipt(run, tracks, matcher, library_hash, identity_hash):
    """Replay one receipt and seal shared proof sources before returning it."""
    with radio_snapshot_proof_validation_pass():
        return _validate_run_receipt_inner(
            run, tracks, matcher, library_hash, identity_hash)


def _validate_source_policy_history(accepted_policy, policy_transition, runs):
    """Validate the one-way boundary from legacy to proof-backed radio runs."""
    radio_policy = 'authenticated-watch-radio-only-v1'
    if accepted_policy not in (None, radio_policy):
        raise ValueError('Previous growth output has an unsupported source policy')
    if accepted_policy is None:
        if policy_transition is not None or any(run.get('source_policy') is not None for run in runs):
            raise ValueError('Legacy growth output contains an unreviewed policy transition')
        return None
    required = {
        'schema_version', 'producer', 'from_policy', 'to_policy',
        'legacy_parent_growth', 'legacy_run_count',
        'promotion_http_requests', 'promotion_attempt_delta',
    }
    if (not isinstance(policy_transition, dict)
            or set(policy_transition) != required
            or type(policy_transition.get('schema_version')) is not int
            or policy_transition.get('schema_version') != 1
            or policy_transition.get('producer') != 'opengenius-radio-source-policy-transition-v1'
            or policy_transition.get('from_policy') != 'legacy-related-required-v1'
            or policy_transition.get('to_policy') != radio_policy
            or type(policy_transition.get('legacy_run_count')) is not int
            or type(policy_transition.get('promotion_http_requests')) is not int
            or type(policy_transition.get('promotion_attempt_delta')) is not int
            or policy_transition.get('promotion_http_requests') != 0
            or policy_transition.get('promotion_attempt_delta') != 0):
        raise ValueError('Radio-only growth lacks its exact zero-call historical-policy transition receipt')
    run_policies = [run.get('source_policy') for run in runs]
    first_radio_run = next((index for index, value in enumerate(run_policies)
                            if value == radio_policy), None)
    if (first_radio_run is None
            or policy_transition['legacy_run_count'] != first_radio_run
            or any(value is not None for value in run_policies[:first_radio_run])
            or any(value != radio_policy for value in run_policies[first_radio_run:])
            or runs[first_radio_run].get('source_policy_transition') != policy_transition
            or any('source_policy_transition' in run
                   for run in runs[first_radio_run + 1:])):
        raise ValueError('Historical and radio-only runs are not separated by one typed policy boundary')
    parent = policy_transition.get('legacy_parent_growth')
    if (not isinstance(parent, dict) or set(parent) != {'path', 'sha256'}
            or not isinstance(parent.get('path'), str)
            or not isinstance(parent.get('sha256'), str)):
        raise ValueError('Radio-only policy transition lacks its exact legacy parent graph receipt')
    return first_radio_run


def _require_legacy_source_policy(paths):
    """Keep direct/default growth merges under the original related-required gate.

    ``collect_graphs`` is also used for exploratory review, so its permissive
    default remains useful there. A growth merge, however, creates durable
    accepted roots and must not admit a sanitized radio-only snapshot whose
    typed proof receipt was stripped.
    """
    for path in paths:
        snapshot = json.loads(Path(path).read_bytes())
        endpoints = {row.get('endpoint') for row in snapshot.get('requests', [])
                     if isinstance(row, dict)}
        if (snapshot.get('account_authentication_verified') is not True
                or snapshot.get('related_status') != 'collected'
                or not {'watch_radio', 'watch_seed', 'song_related'} <= endpoints):
            raise ValueError('A legacy growth merge snapshot fails its related-required source policy')


def _verified_search_derived_seed_hint(seed, snapshot_paths, library_hash,
                                       identity_hash, expected_receipt_paths):
    """Resolve one selector-missing seed only through its exact typed proof.

    Search-derived fallback is intentionally narrower than ordinary saved
    hints: a proof must bind the manifest PID to one authenticated search
    result and the same selected radio video, and the sanitized snapshot must
    be exactly reproducible from that proof. The normal graph consumer still
    validates the proof and all outgoing targets independently.
    """
    pid = seed.get('persistent_id') if isinstance(seed, dict) else None
    if (not isinstance(pid, str) or seed.get('seed_video_id') is not None
            or seed.get('seed_video_source') is not None):
        return None
    required_sources = {'track_snapshot', 'identity_map', 'collector_snapshot',
                        'collector_summary', 'collector_seed_manifest',
                        'batch_intent', 'seed_plan', 'seed_video_evidence'}
    if (not isinstance(expected_receipt_paths, dict)
            or set(expected_receipt_paths) != required_sources):
        raise ValueError('Search-derived seed requires complete current collector source receipts')
    expected_receipts = {}
    for name, value in expected_receipt_paths.items():
        path = Path(value).resolve()
        try:
            data = path.read_bytes()
        except OSError:
            raise ValueError('Current collector source is unreadable: ' + name) from None
        expected_receipts[name] = {'path': str(path), 'sha256': sha(data)}
    matches = []
    for path in snapshot_paths:
        snapshot_path = Path(path).resolve()
        try:
            snapshot_bytes = snapshot_path.read_bytes()
            snapshot = json.loads(snapshot_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        local_seed = snapshot.get('local_seed')
        if not isinstance(local_seed, dict) or local_seed.get('persistent_id') != pid:
            continue
        receipt = snapshot.get('radio_only_source_proof')
        if (not isinstance(receipt, dict) or set(receipt) != {'path', 'sha256'}
                or not isinstance(receipt.get('path'), str)
                or not isinstance(receipt.get('sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', receipt['sha256'])):
            continue
        from radio_snapshot_proof import (radio_only_snapshot_from_proof,
                                          verify_radio_snapshot_proof)
        proof_path = Path(receipt['path']).resolve()
        try:
            proof_bytes = proof_path.read_bytes()
        except OSError:
            raise ValueError('Search-derived seed proof is unreadable') from None
        if sha(proof_bytes) != receipt['sha256']:
            raise ValueError('Search-derived seed proof receipt changed')
        try:
            proof = json.loads(proof_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Search-derived seed proof is malformed') from None
        for name, expected in expected_receipts.items():
            receipt_source = proof.get(name)
            if (not isinstance(receipt_source, dict)
                    or not isinstance(receipt_source.get('path'), str)
                    or receipt_source.get('sha256') != expected['sha256']
                    or Path(receipt_source['path']).resolve() != Path(expected['path'])):
                raise ValueError('Search-derived proof differs from current collector source: ' + name)
        verified = verify_radio_snapshot_proof(proof_path)
        selected = proof.get('selected_seed')
        binding = proof.get('seed_video_search_binding')
        video_id = selected.get('video_id') if isinstance(selected, dict) else None
        if (verified.get('verified') is not True
                or verified.get('persistent_id') != pid
                or proof.get('library_sha256') != library_hash
                or proof.get('identity_map', {}).get('sha256') != identity_hash
                or not isinstance(selected, dict)
                or selected.get('persistent_id') != pid
                or local_seed.get('persistent_id') != pid
                or snapshot.get('selected_video_id') != video_id
                or not isinstance(binding, dict)
                or binding.get('kind') != 'authenticated-search-strict-unique-seed-v1'
                or binding.get('persistent_id') != pid
                or binding.get('resolver', {}).get('video_id') != video_id
                or binding.get('strict_unique_video_ids') != [video_id]):
            raise ValueError('Search-derived seed proof does not bind the manifest PID and radio video')
        expected_snapshot = radio_only_snapshot_from_proof(proof_path)
        if snapshot != expected_snapshot:
            raise ValueError('Search-derived sanitized snapshot differs from its proof')
        matches.append(video_id)
    if len(matches) > 1:
        raise ValueError('Manifest seed has multiple search-derived proof snapshots')
    return ({'video_id': matches[0], 'source': 'authenticated_search_seed_proof'}
            if matches else None)


def _validate_collector_seed_video_binding(seed, selected_hints, snapshot_paths,
                                           source_policy, library_hash,
                                           identity_hash,
                                           expected_receipt_paths=None):
    """Require the normal selector hint, or its narrowly typed search proof."""
    hint = selected_hints.get(seed.get('persistent_id'))
    used_search_proof = False
    if hint is None and source_policy == 'authenticated-watch-radio-only-v1':
        hint = _verified_search_derived_seed_hint(
            seed, snapshot_paths, library_hash, identity_hash,
            expected_receipt_paths or {})
        used_search_proof = hint is not None
    if hint is None:
        raise ValueError('Collector seed differs from the strict saved-video evidence')
    if used_search_proof:
        if (source_policy != 'authenticated-watch-radio-only-v1'
                or seed.get('seed_video_id') is not None
                or seed.get('seed_video_source') is not None):
            raise ValueError('Collector seed differs from the strict saved-video evidence')
    elif (hint.get('video_id') != seed.get('seed_video_id')
          or hint.get('source') != seed.get('seed_video_source')):
        raise ValueError('Collector seed differs from the strict saved-video evidence')
    return hint


def _unresolved_seed_disposition(seed, selected_hints, *, source_policy,
                                 collector_summary, collector_summary_path,
                                 seed_manifest, seed_manifest_path,
                                 seed_video_evidence_path, snapshot_paths,
                                 library_hash, identity_hash,
                                 expected_source_paths=None, tracks=None, matcher=None):
    """Return a typed receipt for a genuinely unresolved, uncollected seed.

    This is deliberately narrower than a missing hint. Only an authenticated
    summary result marked search_unresolved, tied to the exact current intent,
    seed plan, manifest, evidence sidecar, and source policy can be represented
    as a zero-graph disposition. Collected/malformed/missing-source cases still
    fall through to the ordinary strict hint/proof requirement and fail.
    """
    pid = seed.get('persistent_id') if isinstance(seed, dict) else None
    if (not isinstance(pid, str) or source_policy != 'authenticated-watch-radio-only-v1'
            or selected_hints.get(pid) is not None):
        return None
    results = collector_summary.get('results')
    rows = [row for row in results if isinstance(row, dict)] if isinstance(results, list) else []
    result_rows = [row for row in rows if row.get('persistent_id') == pid]
    if not result_rows or result_rows[0].get('status') != 'search_unresolved':
        return None
    if len(result_rows) != 1:
        raise ValueError('Unresolved collector seed has duplicate summary results')
    result = result_rows[0]
    manifest_path = Path(seed_manifest_path).resolve()
    summary_path = Path(collector_summary_path).resolve()
    batch_dir = manifest_path.parent.parent
    intent_path = batch_dir / 'intent.json'
    plan_path = batch_dir / 'seed-plan.json'
    candidate_count = result.get('candidate_video_ids')
    if (seed.get('seed_video_id') is not None or seed.get('seed_video_source') is not None
            or type(candidate_count) is not int or candidate_count < 0
            or type(result.get('title_fallback_attempted')) is not bool):
        raise ValueError('Unresolved collector seed has inconsistent search disposition')
    search_receipts = []
    finish_at = None
    start_at = None
    if candidate_count > 0:
        # A nonzero candidate disposition is only valid for a batch-local
        # response observed inside the authenticated collector run window.
        # Current summaries always carry finished_at; older/alternate sources
        # may also carry started_at, which is enforced when present.
        for label, value in (('finished_at', collector_summary.get('finished_at')),
                             ('started_at', collector_summary.get('started_at'))):
            if value is None and label == 'started_at':
                continue
            try:
                parsed = datetime.fromisoformat(value)
            except (TypeError, ValueError):
                raise ValueError('Ambiguous unresolved summary has an invalid run timestamp') from None
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError('Ambiguous unresolved summary run timestamp must include a timezone')
            if label == 'finished_at':
                finish_at = parsed
            else:
                start_at = parsed
        if start_at is not None and finish_at < start_at:
            raise ValueError('Ambiguous unresolved collector run window is reversed')
    def replay_search_attempt(query, expected_count, label, *, require_positive):
        request_arguments = {'query': query, 'filter': 'songs', 'limit': 5}
        cache_key = json.dumps(['search', request_arguments], sort_keys=True,
                               ensure_ascii=False).encode('utf-8')
        collector_dir = manifest_path.parent.resolve()
        request_dir = manifest_path.parent / 'requests'
        if (request_dir.is_symlink()
                or request_dir.resolve().parent != collector_dir):
            raise ValueError('Ambiguous unresolved search directory escapes its collector batch')
        request_candidate = request_dir / (sha(cache_key) + '.json')
        if request_candidate.is_symlink():
            raise ValueError('Ambiguous unresolved search cache must not be a symlink')
        request_path = request_candidate.resolve()
        if request_path.parent != request_dir.resolve():
            raise ValueError('Ambiguous unresolved search cache path escapes its collector batch')
        try:
            request_bytes = request_path.read_bytes()
            request_document = json.loads(request_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Ambiguous unresolved seed lacks its exact batch-local search response') from None
        observed_at = request_document.get('observed_at') if isinstance(request_document, dict) else None
        try:
            parsed_at = datetime.fromisoformat(observed_at)
        except (TypeError, ValueError):
            raise ValueError('Ambiguous unresolved search receipt has an invalid timestamp') from None
        if (parsed_at.tzinfo is None or parsed_at.utcoffset() is None
                or set(request_document) != {'endpoint', 'arguments', 'observed_at', 'response'}
                or request_document.get('endpoint') != 'search'
                or request_document.get('arguments') != request_arguments
                or not isinstance(request_document.get('response'), list)):
            raise ValueError('Ambiguous unresolved search receipt differs from its exact request')
        if (finish_at is None or parsed_at > finish_at
                or (start_at is not None and parsed_at < start_at)):
            raise ValueError('Ambiguous unresolved search timestamp is outside the collector run window')
        selected_video, replayed_count, replay_status = resolve_seed_search(
            request_document['response'], seed, tracks, matcher)
        if (selected_video is not None or replay_status != 'unresolved'
                or replayed_count != expected_count
                or (require_positive and replayed_count <= 0)):
            raise ValueError('Ambiguous unresolved search response does not replay the reported result')
        try:
            if request_path.read_bytes() != request_bytes:
                raise ValueError('Ambiguous unresolved search response changed during replay')
        except OSError:
            raise ValueError('Ambiguous unresolved search response disappeared during replay') from None
        return {'path': str(request_path), 'sha256': sha(request_bytes),
                'query_sha256': sha(query.encode('utf-8')),
                'observed_at': observed_at, 'raw_result_count': len(request_document['response']),
                'candidate_video_ids': replayed_count,
                'resolver_status': replay_status, 'selected_video_id': selected_video,
                'label': label}

    if candidate_count == 0:
        if (set(result) != {'persistent_id', 'status', 'candidate_video_ids',
                            'title_fallback_attempted'}
                or result.get('title_fallback_attempted') is not False):
            raise ValueError('Unresolved collector seed has inconsistent search disposition')
    else:
        if (tracks is None or not isinstance(seed.get('title'), str)
                or not isinstance(seed.get('artist'), str)):
            raise ValueError('Ambiguous unresolved seed lacks current Library search context')
        fallback_used = result['title_fallback_attempted']
        expected_result_keys = {'persistent_id', 'status', 'candidate_video_ids',
                                'title_fallback_attempted'}
        if (('seed_query_fallback' in seed_manifest
             and seed_manifest.get('seed_query_fallback') is not False)
                or set(result) != expected_result_keys or fallback_used is not False):
            raise ValueError('Ambiguous unresolved search fallback differs from the current seed manifest')
        search_receipts.append(replay_search_attempt(
            seed['title'] + ' ' + seed['artist'], candidate_count, 'primary',
            require_positive=True))
    if (type(collector_summary.get('schema_version')) is not int
            or collector_summary.get('schema_version') != 1
            or collector_summary.get('status') != 'completed'
            or collector_summary.get('account_authentication_verified') is not True
            or collector_summary.get('library_sha256') != library_hash
            or collector_summary.get('collector_collection_mode') != 'radio-only-v1'
            or type(collector_summary.get('request_budget')) is not int
            or type(collector_summary.get('http_requests')) is not int
            or not 0 < collector_summary['http_requests'] <= collector_summary['request_budget']):
        raise ValueError('Unresolved disposition lacks an authenticated current collector summary')

    if summary_path.parent != manifest_path.parent:
        raise ValueError('Unresolved summary is outside its manifest collector directory')
    try:
        summary_bytes = summary_path.read_bytes()
        manifest_bytes = manifest_path.read_bytes()
        intent_bytes = intent_path.read_bytes()
        plan_bytes = plan_path.read_bytes()
        evidence_path = Path(seed_video_evidence_path).resolve()
        evidence_bytes = evidence_path.read_bytes()
    except (OSError, TypeError):
        raise ValueError('Unresolved seed is missing a current intent, plan, or evidence source') from None
    if (json.loads(summary_bytes) != collector_summary
            or json.loads(manifest_bytes) != seed_manifest):
        raise ValueError('Unresolved summary/manifest bytes differ from the validated documents')
    intent = json.loads(intent_bytes)
    plan = json.loads(plan_bytes)
    manifest_seeds = seed_manifest.get('seeds')
    if not isinstance(manifest_seeds, list):
        raise ValueError('Unresolved seed manifest is malformed')
    manifest_pids = [row.get('persistent_id') for row in manifest_seeds
                     if isinstance(row, dict)]
    runner_config = intent.get('runner_config')
    if not isinstance(runner_config, dict):
        raise ValueError('Unresolved seed intent lacks its runner configuration')
    if (len(manifest_pids) != len(manifest_seeds)
            or len(set(manifest_pids)) != len(manifest_pids)
            or intent.get('source_policy') != source_policy
            or intent.get('library_sha256') != library_hash
            or intent.get('identity_map_sha256') != identity_hash
            or intent.get('seed_pids') != manifest_pids
            or intent.get('request_budget') != collector_summary['request_budget']
            or intent.get('request_budget') > runner_config.get('batch_request_budget', 0)
            or runner_config.get('source_policy') != source_policy
            or runner_config.get('collector_collection_mode') != 'radio-only-v1'
            or ('seed_query_fallback' in runner_config
                and runner_config.get('seed_query_fallback') is not False)
            or type(intent.get('runner_config_sha256')) is not str
            or sha(json.dumps(runner_config, ensure_ascii=False,
                              sort_keys=True, separators=(',', ':')).encode('utf-8'))
               != intent.get('runner_config_sha256')):
        raise ValueError('Unresolved seed intent does not bind the current collector batch')
    expected_source_inputs = []
    for name in ('track_snapshot', 'identity_map', 'baseline_state',
                 'baseline_graph_export', 'rebase_report',
                 'external_seed_video_evidence', 'prior_attempt_ledger'):
        ref = runner_config.get(name)
        if ref is None and name in ('external_seed_video_evidence', 'prior_attempt_ledger'):
            continue
        if (not isinstance(ref, dict) or not isinstance(ref.get('path'), str)
                or not isinstance(ref.get('sha256'), str)):
            raise ValueError('Unresolved intent has malformed configured source: ' + name)
        source_path = Path(ref['path']).resolve()
        try:
            source_bytes = source_path.read_bytes()
        except OSError:
            raise ValueError('Unresolved intent source is unreadable: ' + name) from None
        if sha(source_bytes) != ref['sha256']:
            raise ValueError('Unresolved intent source hash is invalid: ' + name)
        expected_source_inputs.append({'path': str(source_path), 'sha256': ref['sha256']})
    if intent.get('source_inputs') != expected_source_inputs:
        raise ValueError('Unresolved intent source receipt list differs from its configuration')
    summary_pids = [row.get('persistent_id') for row in rows]
    if summary_pids != manifest_pids:
        raise ValueError('Unresolved summary results differ from the exact manifest seed order')
    plan_receipt = intent.get('seed_plan')
    evidence_receipt = intent.get('seed_video_evidence')
    if (not isinstance(plan_receipt, dict)
            or Path(plan_receipt.get('path', '')).resolve() != plan_path.resolve()
            or plan_receipt.get('sha256') != sha(plan_bytes)
            or seed_manifest.get('seed_plan_sha256') != plan_receipt.get('sha256')
            or not isinstance(evidence_receipt, dict)
            or Path(evidence_receipt.get('path', '')).resolve() != evidence_path
            or evidence_receipt.get('sha256') != sha(evidence_bytes)
            or seed_manifest.get('seed_video_evidence_sha256') != evidence_receipt.get('sha256')
            or plan.get('source_library_sha256') != library_hash
            or [row.get('persistent_id') for row in plan.get('seeds', [])] != manifest_pids):
        raise ValueError('Unresolved seed plan/evidence receipts differ from intent and manifest')
    radio_receipt = intent.get('current_radio_evidence')
    if (not isinstance(radio_receipt, dict)
            or not isinstance(radio_receipt.get('path'), str)
            or not isinstance(radio_receipt.get('sha256'), str)):
        raise ValueError('Unresolved intent lacks current-radio evidence receipt')
    radio_path = Path(radio_receipt['path']).resolve()
    try:
        radio_bytes = radio_path.read_bytes()
    except OSError:
        raise ValueError('Unresolved current-radio evidence is unreadable') from None
    if sha(radio_bytes) != radio_receipt['sha256']:
        raise ValueError('Unresolved current-radio evidence receipt changed')
    sidecar = json.loads(evidence_bytes)
    sidecar_radio = sidecar.get('current_radio_source')
    if (radio_path != (batch_dir / 'current-radio-evidence.json').resolve()
            or not isinstance(sidecar_radio, dict)
            or not isinstance(sidecar_radio.get('path'), str)
            or not isinstance(sidecar_radio.get('sha256'), str)
            or Path(sidecar_radio['path']).resolve() != radio_path
            or sidecar_radio.get('sha256') != radio_receipt['sha256']):
        raise ValueError('Unresolved radio evidence differs from the seed selector sidecar source')
    configured_sources = runner_config
    for name in ('track_snapshot', 'identity_map'):
        expected = (expected_source_paths or {}).get(name)
        ref = configured_sources.get(name)
        if (not isinstance(ref, dict) or not isinstance(ref.get('path'), str)
                or not isinstance(ref.get('sha256'), str)):
            raise ValueError('Unresolved intent lacks configured source receipt: ' + name)
        path = Path(ref['path']).resolve()
        if (expected is not None and path != Path(expected).resolve()):
            raise ValueError('Unresolved intent source path differs from this merge: ' + name)
        if not path.is_file() or sha(path.read_bytes()) != ref['sha256']:
            raise ValueError('Unresolved intent source hash is invalid: ' + name)
        if name == 'identity_map' and ref['sha256'] != identity_hash:
            raise ValueError('Unresolved intent uses a different identity map')
        if name == 'track_snapshot':
            snapshot_doc = json.loads(path.read_bytes())
            if snapshot_doc.get('source_sha256') != library_hash:
                raise ValueError('Unresolved intent uses a different track snapshot')

    if intent.get('batch') != _batch_number_from_path(batch_dir):
        raise ValueError('Unresolved intent batch number differs from its directory')

    collector_dir = manifest_path.parent
    radio_dir = batch_dir / 'radio-only-source'
    absent_paths = [collector_dir / (pid + '.json'),
                    radio_dir / (pid + '.snapshot.json'),
                    radio_dir / (pid + '.proof.json')]
    for path in absent_paths:
        if path.exists():
            raise ValueError('Unresolved seed unexpectedly has a snapshot or proof source')
    for snapshot_path in snapshot_paths:
        try:
            snapshot = json.loads(Path(snapshot_path).read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Current radio snapshot is unreadable') from None
        if (isinstance(snapshot.get('local_seed'), dict)
                and snapshot['local_seed'].get('persistent_id') == pid):
            raise ValueError('Unresolved seed unexpectedly has a supplied radio snapshot')

    digest = lambda path, data: {'path': str(path.resolve()), 'sha256': sha(data)}
    receipt = {
        'schema_version': 1,
        'producer': 'opengenius-authenticated-search-unresolved-seed-v1',
        'persistent_id': pid,
        'status': 'search_unresolved',
        'candidate_video_ids': candidate_count,
        'title_fallback_attempted': False,
        'source_policy': source_policy,
        'collector_summary': digest(summary_path, summary_bytes),
        'batch_intent': digest(intent_path, intent_bytes),
        'seed_manifest': digest(manifest_path, manifest_bytes),
        'seed_plan': digest(plan_path, plan_bytes),
        'seed_video_evidence': digest(evidence_path, evidence_bytes),
        'current_radio_evidence': digest(radio_path, radio_bytes),
        'absent_snapshot_paths': [
            {'path': str(path.resolve()), 'exists': False} for path in absent_paths],
    }
    # Preserve historical zero-candidate v1 receipt bytes exactly. The extra
    # source field exists only for the new nonzero typed disposition.
    if candidate_count > 0:
        receipt['search_request_receipts'] = search_receipts
    return receipt


def _collected_seed_ineligible_disposition(seed, selected_hints, *, source_policy,
                                            collector_summary, collector_summary_path,
                                            seed_manifest, seed_manifest_path,
                                            seed_video_evidence_path, snapshot_paths,
                                            track_snapshot_path, identity_map_path,
                                            library_hash, identity_hash):
    """Replay and type a collected seed whose exact search seed fails strict gates.

    This records no graph. Only the proof verifier's fully replayed
    ``seed_not_strictly_matched`` result with its authenticated search binding
    may enter this receipt; malformed or missing source evidence stays fatal.
    """
    from radio_snapshot_proof import RadioSnapshotIneligible, _proof_from_inputs, _request_cache_path

    pid = seed.get('persistent_id') if isinstance(seed, dict) else None
    if source_policy != 'authenticated-watch-radio-only-v1':
        return None
    if not isinstance(pid, str) or re.fullmatch(r'[0-9A-F]{16}', pid) is None:
        raise ValueError('Strict-ineligible seed has a malformed persistent ID')
    manifest_path = Path(seed_manifest_path).resolve()
    summary_path = Path(collector_summary_path).resolve()
    batch_dir = manifest_path.parent.parent
    collector_snapshot = manifest_path.parent / (pid + '.json')
    try:
        snapshot = _read_json(collector_snapshot)
    except (OSError, ValueError):
        raise ValueError('Strict-ineligible seed lacks its original collector snapshot') from None
    radio_requests = [row for row in snapshot.get('request_evidence', [])
                      if isinstance(row, dict)
                      and row.get('endpoint') == 'get_watch_playlist'
                      and isinstance(row.get('arguments'), dict)
                      and row.get('arguments', {}).get('radio') is True]
    radio_cache_hit = (len(radio_requests) == 1
                       and radio_requests[0].get('cache_hit') is True)
    if not radio_cache_hit and selected_hints.get(pid) is not None:
        return None
    if (not radio_cache_hit
            and (seed.get('seed_video_id') is not None
                 or seed.get('seed_video_source') is not None)):
        return None
    result_rows = [row for row in collector_summary.get('results', [])
                   if isinstance(row, dict) and row.get('persistent_id') == pid]
    if radio_cache_hit and (len(result_rows) != 1
                            or result_rows[0].get('status') != 'collected'):
        raise ValueError('Cached radio disposition lacks one authenticated collected result')
    if len(result_rows) != 1 or result_rows[0].get('status') != 'collected':
        return None

    proof_path = batch_dir / 'radio-only-source' / (pid + '.proof.json')
    derived_snapshot = batch_dir / 'radio-only-source' / (pid + '.snapshot.json')
    for path in snapshot_paths:
        row = _read_json(path)
        local = row.get('local_seed') if isinstance(row, dict) else None
        if isinstance(local, dict) and local.get('persistent_id') == pid:
            # The ordinary validator below owns any matching proof-backed
            # snapshot; it will verify the proof and exact sanitized bytes.
            # Returning no negative disposition also means a forged snapshot
            # cannot be skipped: the caller must still pass that strict gate.
            return None
    if proof_path.exists() or derived_snapshot.exists():
        raise ValueError('Strict-ineligible seed unexpectedly has a proof or derived snapshot')
    local_seed = snapshot.get('local_seed')
    if local_seed != seed:
        raise ValueError('Strict-ineligible seed differs from the collector snapshot seed')
    radio_requests = [row for row in snapshot.get('request_evidence', [])
                      if isinstance(row, dict)
                      and row.get('endpoint') == 'get_watch_playlist'
                      and isinstance(row.get('arguments'), dict)
                      and row['arguments'].get('radio') is True]
    if len(radio_requests) != 1:
        raise ValueError('Strict-ineligible seed lacks one exact radio request')
    radio_request = radio_requests[0]
    radio_cache_hit = radio_request.get('cache_hit') is True
    raw_radio_cache = _request_cache_path(
        collector_snapshot, 'get_watch_playlist', radio_request['arguments'])
    search_requests = [row for row in snapshot.get('request_evidence', [])
                       if isinstance(row, dict) and row.get('endpoint') == 'search'
                       and isinstance(row.get('arguments'), dict)]
    if len(search_requests) != 1:
        raise ValueError('Strict-ineligible seed lacks exactly one search request')
    raw_search_cache = _request_cache_path(
        collector_snapshot, 'search', search_requests[0]['arguments'])
    source_paths = [
        ('track_snapshot', Path(track_snapshot_path)),
        ('identity_map', Path(identity_map_path)),
        ('collector_snapshot', collector_snapshot),
        ('collector_summary', summary_path),
        ('collector_seed_manifest', manifest_path),
        ('batch_intent', batch_dir / 'intent.json'),
        ('seed_plan', batch_dir / 'seed-plan.json'),
        ('seed_video_evidence', Path(seed_video_evidence_path)),
        ('raw_radio_cache', raw_radio_cache),
        ('raw_search_cache', raw_search_cache),
    ]
    intent_path = batch_dir / 'intent.json'
    try:
        intent_bytes_before = intent_path.read_bytes()
        intent_doc = json.loads(intent_bytes_before)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('Strict-ineligible batch intent is unreadable') from None
    intent_sources = intent_doc.get('source_inputs')
    if not isinstance(intent_sources, list):
        raise ValueError('Strict-ineligible batch intent lacks source receipt rows')
    for index, source in enumerate(intent_sources):
        if (not isinstance(source, dict) or not isinstance(source.get('path'), str)
                or not isinstance(source.get('sha256'), str)):
            raise ValueError('Strict-ineligible batch intent has a malformed source receipt')
        source_paths.append((f'intent_source_{index}', Path(source['path'])))
    before_hashes = {}
    try:
        before_hashes = {label: sha(path.read_bytes()) for label, path in source_paths}
    except OSError:
        raise ValueError('Strict-ineligible proof source is unreadable before replay') from None
    if before_hashes['batch_intent'] != sha(intent_bytes_before):
        raise ValueError('Strict-ineligible batch intent changed before replay')
    try:
        _proof_from_inputs(
            track_snapshot_path=track_snapshot_path,
            identity_map_path=identity_map_path,
            snapshot_path=collector_snapshot,
            collector_summary_path=summary_path,
            collector_seed_manifest_path=manifest_path,
            batch_intent_path=batch_dir / 'intent.json',
            raw_radio_cache_path=raw_radio_cache,
            sealed_at_utc=radio_request.get('observed_at'),
            allow_cache_hit_disposition=radio_cache_hit)
    except RadioSnapshotIneligible as exc:
        binding = exc.details.get('seed_video_search_binding')
        seed_gate_failed = (
            exc.reason == 'seed_not_strictly_matched'
            and isinstance(binding, dict)
            and binding.get('kind') == 'authenticated-search-resolver-seed-ineligible-v1'
            and binding.get('persistent_id') == pid
            and binding.get('strict_unique') is False)
        no_targets = (
            exc.reason == 'no_strict_unique_outgoing_targets'
            and isinstance(binding, dict)
            and binding.get('kind') == 'authenticated-search-strict-unique-seed-v1'
            and binding.get('persistent_id') == pid
            and isinstance(binding.get('resolver'), dict)
            and binding['resolver'].get('video_id') == snapshot.get('selected_video_id')
            and binding.get('strict_unique_video_ids') == [snapshot.get('selected_video_id')]
            and exc.details.get('selected_video_id') == snapshot.get('selected_video_id')
            and exc.details.get('strict_unique_target_video_count') == 0
            and exc.details.get('outgoing_target_pids') == []
            and exc.details.get('accepted_edges') == [])
        cached_not_live = (
            radio_cache_hit is True
            and exc.reason == 'radio_cache_hit_not_live'
            and exc.details.get('cache_hit') is True
            and exc.details.get('selected_video_id') == snapshot.get('selected_video_id')
            and exc.details.get('accepted_edges') == []
            and exc.details.get('strict_gate_reason') in {
                'seed_not_strictly_matched', 'search_seed_binding_not_strict',
                'no_strict_unique_outgoing_targets', 'strict_candidate_graph_replayed'})
        if (exc.persistent_id != pid
                or not (seed_gate_failed or no_targets or cached_not_live)):
            raise ValueError('Collector seed has an unsupported ineligible proof outcome') from None
        receipts = []
        for label, path in source_paths:
            path = path.resolve()
            try:
                data = path.read_bytes()
            except OSError:
                raise ValueError('Strict-ineligible proof source is unreadable: ' + label) from None
            digest = sha(data)
            if digest != before_hashes[label]:
                raise ValueError('Strict-ineligible proof source changed during replay: ' + label)
            receipts.append({'name': label, 'path': str(path), 'sha256': digest})
        search_receipt = next(row for row in receipts if row['name'] == 'raw_search_cache')
        if (binding is not None
                and (binding.get('raw_search_cache', {}).get('path') != search_receipt['path']
                     or binding.get('raw_search_cache', {}).get('sha256')
                     != search_receipt['sha256'])):
            raise ValueError('Strict-ineligible search receipt differs from its raw response cache')
        if (sha(Path(identity_map_path).read_bytes()) != identity_hash
                or json.loads(Path(track_snapshot_path).read_bytes()).get('source_sha256') != library_hash):
            raise ValueError('Strict-ineligible proof is bound to a different map or Library')
        status = ('collected_seed_radio_cache_hit_not_live' if cached_not_live else
                  'collected_seed_strictly_ineligible' if seed_gate_failed else
                  'collected_seed_no_strict_outgoing_targets')
        return {
            'schema_version': 1,
            'producer': ('opengenius-authenticated-radio-cache-hit-zero-edge-v1'
                         if cached_not_live else
                         'opengenius-authenticated-search-strict-ineligible-seed-v1'),
            'persistent_id': pid,
            'status': status,
            'reason': exc.reason,
            'details': exc.details,
            'source_receipts': receipts,
            'accepted_edges': [],
        }
    # If full proof replay succeeds, the missing/invalid hint cannot be
    # converted into an ineligible disposition.
    return None


def _read_json(path):
    return json.loads(Path(path).read_bytes())


def _batch_number_from_path(batch_dir):
    match = re.fullmatch(r'batch-(\d{4})', Path(batch_dir).name)
    if not match:
        raise ValueError('Unresolved seed intent is outside a numbered batch directory')
    return int(match.group(1))


def _replay_previous_growth(previous_dir, baseline_state, baseline_roots,
                            baseline_provenance, matcher, library_hash, identity_hash):
    previous_dir = Path(previous_dir).resolve()
    accepted_path = previous_dir / 'accepted-graphs.json'
    accepted_bytes = accepted_path.read_bytes()
    accepted = json.loads(accepted_bytes)
    recorded_provenance = accepted.get('baseline_rebase_provenance')
    # `proof_input_receipts` was added after some accepted growth outputs had
    # already been written. The current verifier reconstructs those receipts
    # from the typed transition and validates their source hashes above, but
    # their absence in a historical output is not a changed baseline. Keep all
    # substantive provenance fields exact, and accept no other omission.
    legacy_provenance = (
        isinstance(recorded_provenance, dict)
        and isinstance(baseline_provenance, dict)
        and 'proof_input_receipts' not in recorded_provenance
        and set(recorded_provenance) == set(baseline_provenance) - {'proof_input_receipts'}
        and all(recorded_provenance[key] == value
                for key, value in baseline_provenance.items()
                if key != 'proof_input_receipts')
    )
    provenance_matches = (recorded_provenance == baseline_provenance
                          or legacy_provenance)
    if (accepted.get('producer') != 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1'
            or accepted.get('library_sha256') != library_hash
            or accepted.get('identity_map_sha256') != identity_hash
            or accepted.get('baseline_state_sha256') != baseline_state.get('state_sha256')
            or not provenance_matches
            or _relation_map(accepted.get('baseline_accepted_roots'), 'Previous baseline roots')
               != _relation_map(baseline_roots, 'Current baseline roots')):
        raise ValueError('Previous growth output does not share this exact verified YT Music baseline')
    runs = accepted.get('growth_runs')
    if not isinstance(runs, list):
        # Read the first format emitted before chained replay was added.
        runs = [{
            'collector_summary': accepted.get('collector_summary'),
            'seed_manifest': {
                **(accepted.get('seed_manifest') or {}),
                **({'seed_video_evidence': {
                    'path': accepted['seed_manifest']['seed_video_evidence_path'],
                    'sha256': accepted['seed_manifest']['seed_video_evidence_sha256']}}
                   if isinstance(accepted.get('seed_manifest'), dict)
                   and accepted['seed_manifest'].get('seed_video_evidence_path') else {}),
            },
            'snapshot_inputs': accepted.get('snapshot_inputs'),
            'new_graphs': accepted.get('new_graphs'),
        }]
    if not runs:
        raise ValueError('Previous growth output has no collector runs')
    accepted_policy = accepted.get('source_policy')
    policy_transition = accepted.get('source_policy_transition')
    first_radio_run = _validate_source_policy_history(
        accepted_policy, policy_transition, runs)
    if accepted_policy is not None:
        transition_parent = policy_transition.get('legacy_parent_growth')
        transition_path = Path(transition_parent['path']).resolve()
        transition_bytes = transition_path.read_bytes()
        transition_graph = json.loads(transition_bytes)
        if (sha(transition_bytes) != transition_parent['sha256']
                or transition_graph.get('source_policy') is not None
                or transition_graph.get('producer') != 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1'):
            raise ValueError('Radio-only policy transition parent is not the exact legacy growth graph')
    state = baseline_state
    roots = baseline_roots
    normalized_runs = []
    for run_index, run in enumerate(runs):
        if accepted_policy is not None and run_index == first_radio_run:
            transition_parent = policy_transition['legacy_parent_growth']
            transition_path = Path(transition_parent['path']).resolve()
            transition_graph = json.loads(transition_path.read_bytes())
            transition_state_path = transition_path.parent / 'state.json'
            transition_state = json.loads(transition_state_path.read_bytes())
            if (state.get('state_sha256') != transition_state.get('state_sha256')
                    or _relation_map(roots, 'Replayed legacy transition roots')
                       != _relation_map(transition_graph.get('accepted_roots'),
                                        'Legacy transition parent roots')
                    or run.get('source_policy_transition') != policy_transition):
                raise ValueError('Radio-only policy boundary does not extend the exact replayed legacy graph/state')
        new_roots, normalized = _validate_run_receipt(
            run, matcher.tracks, matcher, library_hash, identity_hash)
        roots, _ = _append_graphs(roots, new_roots)
        update = plan_genius_update(state, roots, matcher, library_hash, identity_hash)
        state = update['next_state']
        if (run.get('resulting_state_sha256') is not None
                and run['resulting_state_sha256'] != state['state_sha256']):
            raise ValueError('Previous collector run virtual state cannot be reproduced')
        normalized['resulting_state_sha256'] = state['state_sha256']
        normalized_runs.append(normalized)
    previous_state = json.loads((previous_dir / 'state.json').read_text(encoding='utf-8'))
    previous_relations = json.loads((previous_dir / 'relations.json').read_text(encoding='utf-8'))
    previous_evaluation = json.loads((previous_dir / 'accepted-evaluation.json').read_text(encoding='utf-8'))
    if (state.get('state_sha256') != previous_state.get('state_sha256')
            or _relation_map(roots, 'Replayed prior roots') !=
               _relation_map(accepted.get('accepted_roots'), 'Prior accepted roots')
            or _relation_map(roots, 'Replayed prior roots') !=
               _relation_map(previous_relations.get('relation_rows'), 'Prior relations')
            or previous_evaluation.get('virtual_state_sha256') != state['state_sha256']
            or _relation_map(roots, 'Replayed prior roots') !=
               _relation_map(previous_evaluation.get('relation_rows'), 'Prior evaluation')):
        raise ValueError('Previous growth state, relations, and accepted receipts do not replay exactly')
    return state, roots, normalized_runs, sha(accepted_bytes)


def _merge_radio_growth_with_radio_proof_cache(*, track_snapshot, identity_map_path, baseline_state_path,
                       baseline_graph_export_path, rebase_report_path,
                       collector_summary_path, seed_manifest_path,
                       snapshot_paths, output_dir, seed_video_evidence_path=None,
                       previous_growth_dir=None, source_policy=None):
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError('Output directory exists; choose a new directory')
    if source_policy not in (None, 'authenticated-watch-radio-only-v1'):
        raise ValueError('Unsupported authenticated recommendation source policy')
    tracks, library_hash, library_input = load_library(track_snapshot=Path(track_snapshot))
    map_bytes = Path(identity_map_path).read_bytes()
    identity_hash = sha(map_bytes)
    matcher = IdentityMap(json.loads(map_bytes), tracks, library_hash)
    seed_manifest_path = Path(seed_manifest_path).resolve()
    seed_manifest_bytes = seed_manifest_path.read_bytes()
    seed_manifest = json.loads(seed_manifest_bytes)
    if (seed_manifest.get('library_sha256') != library_hash
            or seed_manifest.get('identity_map_sha256') != identity_hash
            or not isinstance(seed_manifest.get('seeds'), list)
            or not seed_manifest['seeds']):
        raise ValueError('Collector seed manifest is not bound to this Library and map')
    manifest_pids = [row.get('persistent_id') for row in seed_manifest['seeds']
                     if isinstance(row, dict)]
    if (len(manifest_pids) != len(seed_manifest['seeds'])
            or len(set(manifest_pids)) != len(manifest_pids)):
        raise ValueError('Collector seed manifest has malformed or duplicate PIDs')
    evidence_path = evidence_bytes = evidence = None
    unresolved_seed_dispositions = []
    collected_seed_ineligible_dispositions = []
    if seed_video_evidence_path is not None:
        evidence_path = Path(seed_video_evidence_path).resolve()
        evidence_bytes = evidence_path.read_bytes()
        evidence = json.loads(evidence_bytes)
        if seed_manifest.get('seed_video_evidence_sha256') != sha(evidence_bytes):
            raise ValueError('Collector seed manifest does not bind the supplied video evidence')
        selected_hints = select_seed_video_evidence(
            evidence, evidence_path,
            [{'persistent_id': pid} for pid in manifest_pids],
            tracks, matcher, library_hash, identity_hash)
        collector_path = Path(collector_summary_path).resolve()
        collector = json.loads(collector_path.read_bytes())
        for seed in seed_manifest['seeds']:
            batch_dir = seed_manifest_path.parent.parent
            expected_receipt_paths = {
                'track_snapshot': Path(track_snapshot),
                'identity_map': Path(identity_map_path),
                'collector_snapshot': seed_manifest_path.parent /
                    (seed['persistent_id'] + '.json'),
                'collector_summary': Path(collector_summary_path),
                'collector_seed_manifest': seed_manifest_path,
                'batch_intent': batch_dir / 'intent.json',
                'seed_plan': batch_dir / 'seed-plan.json',
                'seed_video_evidence': evidence_path,
            }
            disposition = _unresolved_seed_disposition(
                seed, selected_hints, source_policy=source_policy,
                collector_summary=collector,
                collector_summary_path=collector_path,
                seed_manifest=seed_manifest,
                seed_manifest_path=seed_manifest_path,
                seed_video_evidence_path=evidence_path,
                snapshot_paths=snapshot_paths,
                library_hash=library_hash, identity_hash=identity_hash,
                expected_source_paths={'track_snapshot': Path(track_snapshot),
                                      'identity_map': Path(identity_map_path)},
                tracks=tracks, matcher=matcher)
            if disposition is not None:
                unresolved_seed_dispositions.append(disposition)
                continue
            ineligible = _collected_seed_ineligible_disposition(
                seed, selected_hints, source_policy=source_policy,
                collector_summary=collector,
                collector_summary_path=collector_path,
                seed_manifest=seed_manifest,
                seed_manifest_path=seed_manifest_path,
                seed_video_evidence_path=evidence_path,
                snapshot_paths=snapshot_paths,
                track_snapshot_path=track_snapshot,
                identity_map_path=identity_map_path,
                library_hash=library_hash, identity_hash=identity_hash)
            if ineligible is not None:
                collected_seed_ineligible_dispositions.append(ineligible)
                continue
            _validate_collector_seed_video_binding(
                seed, selected_hints, snapshot_paths, source_policy,
                library_hash, identity_hash, expected_receipt_paths)
    elif seed_manifest.get('seed_video_evidence_sha256') is not None:
        raise ValueError('Collector used saved-video evidence but its sidecar was not supplied')
    graph_doc, report, state, rebase_provenance = _verify_rebase_receipt(
        baseline_graph_export_path, rebase_report_path, baseline_state_path,
        library_hash, identity_hash)
    graph_roots = graph_doc.get('accepted_roots')
    root_map = _relation_map(graph_roots, 'Rebased graph export')
    state_map = _relation_map(_state_graph_rows(state), 'Rebased state')
    if root_map != state_map:
        raise ValueError('Rebased graph export differs from the recommendation edges in its state')
    baseline_track_count = len(state_rows(state)[0])
    if (len(root_map) != baseline_track_count
            or baseline_track_count != report.get('indexed_tracks')
            or sum(map(len, root_map.values())) !=
            report['relation_revalidation']['output_directed_edge_count']):
        raise ValueError('Baseline root, index, or edge counts differ from the verified rebase')
    baseline_state_check = plan_genius_update(
        state, graph_roots, matcher, library_hash, identity_hash)
    if baseline_state_check['next_state']['state_sha256'] != state['state_sha256']:
        raise ValueError('Rebased recommendation graph does not reproduce the baseline state')
    original_state_sha256 = state['state_sha256']
    original_baseline_roots = graph_roots
    prior_growth_receipt = None
    growth_runs = []
    if previous_growth_dir is not None:
        state, graph_roots, growth_runs, prior_growth_receipt = _replay_previous_growth(
            previous_growth_dir, state, graph_roots, rebase_provenance,
            matcher, library_hash, identity_hash)
        prior_graph_doc = json.loads((Path(previous_growth_dir).resolve() / 'accepted-graphs.json').read_bytes())
        prior_policy = prior_graph_doc.get('source_policy')
        latest_run_policy = growth_runs[-1].get('source_policy') if growth_runs else prior_policy
        if source_policy != latest_run_policy:
            if not (source_policy == 'authenticated-watch-radio-only-v1'
                    and prior_policy is None and latest_run_policy is None):
                raise ValueError('Current merge source policy differs from its prior growth chain')
    direct_parent_state_sha256 = state['state_sha256']

    collector_path = Path(collector_summary_path).resolve()
    collector_bytes = collector_path.read_bytes()
    collector = json.loads(collector_bytes)
    completed = (collector.get('status') == 'completed'
                 and collector.get('account_authentication_verified') is True)
    boundary_stopped = (
        collector.get('status') == 'stopped'
        and collector.get('account_authentication_verified') is True
        and type(collector.get('http_requests')) is int
        and type(collector.get('request_budget')) is int
        and collector['http_requests'] == collector['request_budget']
        and collector.get('error_type') == 'RuntimeError'
        and isinstance(collector.get('error_location'), dict)
        and collector['error_location'].get('function') == 'request'
        and 'http_error_status' not in collector)
    if (type(collector.get('schema_version')) is not int
            or collector.get('schema_version') != 1
            or not (completed or boundary_stopped)
            or collector.get('library_sha256') != library_hash
            or type(collector.get('http_requests')) is not int
            or type(collector.get('request_budget')) is not int
            or not 0 < collector['http_requests'] <= collector['request_budget']):
        raise ValueError('Collector summary is not an authenticated run or request-boundary stop for this Library')
    collected = {row.get('persistent_id') for row in collector.get('results', [])
                 if isinstance(row, dict) and row.get('status') == 'collected'}
    paths = [Path(path).resolve() for path in snapshot_paths]
    if not paths:
        raise ValueError('At least one collector snapshot is required')
    if source_policy is None:
        _require_legacy_source_policy(paths)
    graphs, skipped, inputs = collect_graphs(
        paths, matcher, library_hash, source_policy=source_policy,
        identity_map_sha256=identity_hash)
    if not graphs:
        raise ValueError('No collector snapshots rematched as valid YT Music graphs')
    if any(graph['root_pid'] not in collected for graph in graphs):
        raise ValueError('A graph root is absent from authenticated collected seed results')
    new_roots = merge_graphs(graphs)
    source_policy_transition = None
    if source_policy == 'authenticated-watch-radio-only-v1':
        if previous_growth_dir is None:
            raise ValueError('Radio-only growth must extend a fully replayed parent graph')
        prior_graph_doc = json.loads((Path(previous_growth_dir).resolve() / 'accepted-graphs.json').read_bytes())
        if prior_graph_doc.get('source_policy') is None:
            source_policy_transition = {
                'schema_version': 1,
                'producer': 'opengenius-radio-source-policy-transition-v1',
                'from_policy': 'legacy-related-required-v1',
                'to_policy': source_policy,
                'legacy_parent_growth': {
                    'path': str((Path(previous_growth_dir).resolve() / 'accepted-graphs.json')),
                    'sha256': sha((Path(previous_growth_dir).resolve() / 'accepted-graphs.json').read_bytes()),
                },
                'legacy_run_count': len(growth_runs),
                'promotion_http_requests': 0,
                'promotion_attempt_delta': 0,
            }
        else:
            source_policy_transition = prior_graph_doc.get('source_policy_transition')
            if not isinstance(source_policy_transition, dict):
                raise ValueError('Prior radio-only graph has no typed policy transition receipt')
    roots, original_edges = _append_graphs(graph_roots, new_roots)
    update = plan_genius_update(state, roots, matcher, library_hash, identity_hash)
    next_state = update['next_state']
    old_ids, _, _ = state_rows(state)
    new_ids, _, _ = state_rows(next_state)
    baseline_edges = _relation_map(graph_roots, 'Previous accepted recommendation graph')
    accepted_edges = _relation_map(roots, 'Merged YT Music graph')
    appended_edges = (sum(len(accepted_edges[root]) - len(targets)
                          for root, targets in baseline_edges.items())
                      + sum(len(accepted_edges[root])
                            for root in set(accepted_edges) - set(baseline_edges)))
    roots_with_appended_edges = {
        root for root, targets in baseline_edges.items()
        if accepted_edges[root][len(targets):]}
    newly_observed_tracks = {
        pid for row in new_roots if row['ordered_target_pids']
        for pid in (row['root_pid'], *row['ordered_target_pids'])}
    baseline_active_roots = {root for root, targets in baseline_edges.items() if targets}
    accepted_active_roots = {root for root, targets in accepted_edges.items() if targets}
    source_relations = {
        'schema_version': 1, 'source_library_sha256': library_hash,
        'source_state_sha256': next_state['state_sha256'],
        'relation_rows': roots,
    }
    evaluation = {
        'schema_version': 1, 'library_sha256': library_hash,
        'relation_rows': roots, 'network_requests': 0,
        'virtual_state_sha256': next_state['state_sha256'],
        'evaluation_scope': 'accepted YT Music recommendation relation graph; new roots are not core-evaluated',
    }
    output_dir.mkdir(parents=True)
    write_json(output_dir / 'relations.json', source_relations)
    write_json(output_dir / 'state.json', next_state)
    write_json(output_dir / 'accepted-evaluation.json', evaluation)
    write_json(output_dir / 'virtual-update-audit.json', update)
    write_json(output_dir / 'inputs.json', {
        'schema_version': 1,
        'library_sha256': library_hash,
        'identity_map_sha256': identity_hash,
        'track_snapshot': {'path': str(Path(track_snapshot).resolve()),
                           'sha256': sha(Path(track_snapshot).read_bytes())},
        'identity_map': {'path': str(Path(identity_map_path).resolve()),
                         'sha256': identity_hash},
        'baseline_state': {'path': str(Path(baseline_state_path).resolve()),
                           'sha256': rebase_provenance['state_file_sha256']},
        'baseline_graph_export': {
            'path': rebase_provenance['graph_export_path'],
            'sha256': rebase_provenance['graph_export_sha256']},
        'rebase_report': {
            'path': rebase_provenance['rebase_report_path'],
            'sha256': rebase_provenance['rebase_report_sha256']},
        'collector_summary': {'path': str(collector_path),
                              'sha256': sha(collector_bytes)},
        'seed_manifest': {'path': str(seed_manifest_path),
                          'sha256': sha(seed_manifest_bytes)},
        **({'seed_video_evidence': {'path': str(evidence_path),
                                    'sha256': sha(evidence_bytes)}}
           if evidence_path is not None else {}),
        'collector_snapshots': inputs,
        **({'previous_growth': {'path': str(Path(previous_growth_dir).resolve()),
                                'accepted_graphs_sha256': prior_growth_receipt}}
           if previous_growth_dir is not None else {}),
    })
    write_json(output_dir / 'accepted-graphs.json', {
        'schema_version': 1, 'producer': 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1',
        'library_sha256': library_hash, 'identity_map_sha256': identity_hash,
        'baseline_state_sha256': original_state_sha256,
        'direct_parent_state_sha256': direct_parent_state_sha256,
        'baseline_rebase_provenance': rebase_provenance,
        'collector_summary': {'path': str(collector_path),
                              'sha256': sha(collector_bytes),
                              'http_requests': collector['http_requests'],
                              'request_budget': collector['request_budget']},
        'seed_manifest': {'path': str(seed_manifest_path),
                          'sha256': sha(seed_manifest_bytes),
                          **({'seed_video_evidence_path': str(evidence_path),
                              'seed_video_evidence_sha256': sha(evidence_bytes)}
                             if evidence_path is not None else {}),
                          'seed_count': len(seed_manifest['seeds'])},
        **({'source_policy': source_policy} if source_policy is not None else {}),
        **({'source_policy_transition': source_policy_transition}
           if source_policy_transition is not None else {}),
        'baseline_accepted_roots': original_baseline_roots,
        'growth_runs': [*growth_runs, {
            'collector_summary': {'path': str(collector_path),
                                  'sha256': sha(collector_bytes),
                                  'http_requests': collector['http_requests'],
                                  'request_budget': collector['request_budget']},
            'seed_manifest': {'path': str(seed_manifest_path),
                              'sha256': sha(seed_manifest_bytes),
                              **({'seed_video_evidence': {
                                  'path': str(evidence_path),
                                  'sha256': sha(evidence_bytes)}}
                                 if evidence_path is not None else {}),
                              'seed_count': len(seed_manifest['seeds'])},
            'snapshot_inputs': inputs,
            **({'source_policy': source_policy} if source_policy is not None else {}),
            **({'source_policy_transition': source_policy_transition}
               if source_policy_transition is not None
               and source_policy_transition.get('legacy_run_count') == len(growth_runs) else {}),
            'skipped_snapshots': skipped,
            'new_graphs': [{'root_pid': row['root_pid'],
                            'ordered_target_pids': row['ordered_target_pids']}
                           for row in new_roots],
            'resulting_state_sha256': next_state['state_sha256'],
            **({'unresolved_seed_dispositions': unresolved_seed_dispositions}
               if unresolved_seed_dispositions else {}),
            **({'collected_seed_ineligible_dispositions': collected_seed_ineligible_dispositions}
               if collected_seed_ineligible_dispositions else {}),
        }],
        **({'previous_growth_receipt': {'path': str(Path(previous_growth_dir).resolve()),
                                        'accepted_graphs_sha256': prior_growth_receipt}}
           if previous_growth_dir is not None else {}),
        'accepted_roots': roots,
        'new_graphs': [{
            'root_pid': graph['root_pid'],
            'ordered_target_pids': graph['ordered_target_pids'],
            'input': graph['input'],
            'observation_count': len(graph['observations']),
            'source_relations': sorted({row['relation'] for row in graph['observations']}),
            'identity_status': 'unverified_metadata_candidate',
        } for graph in graphs],
        'snapshot_inputs': inputs, 'skipped_snapshots': skipped,
    })
    new_pids = set(new_ids) - set(old_ids)
    newly_active_outgoing_roots = accepted_active_roots - baseline_active_roots
    audit = {
        'schema_version': 1, 'producer': 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'library_sha256': library_hash, 'identity_map_sha256': identity_hash,
        **({'source_policy': source_policy} if source_policy is not None else {}),
        'track_snapshot_sha256': sha(Path(track_snapshot).read_bytes()),
        'library_input': library_input,
        'origin_rebased_state_sha256': original_state_sha256,
        'baseline_state_sha256': direct_parent_state_sha256,
        'next_state_sha256': next_state['state_sha256'],
        'baseline_root_count': len(baseline_edges),
        'baseline_directed_edge_count': sum(map(len, baseline_edges.values())),
        'baseline_active_outgoing_root_count': len(baseline_active_roots),
        'accepted_collector_snapshot_count': len(graphs),
        'collector_snapshot_input_count': len(inputs),
        'accepted_graph_snapshot_count': len(graphs),
        'rejected_snapshot_count': len(skipped),
        'collector_graph_root_count': len(new_roots),
        'newly_collected_graph_root_count': len(new_roots),
        'new_outgoing_root_count': len(newly_active_outgoing_roots),
        'newly_active_outgoing_root_count': len(newly_active_outgoing_roots),
        'output_active_outgoing_root_count': len(accepted_active_roots),
        'roots_with_appended_recommendation_edges': len(roots_with_appended_edges),
        'appended_recommendation_edge_count': appended_edges,
        'new_outgoing_edge_count': appended_edges,
        'recommendation_tracks_touched': len(newly_observed_tracks),
        'newly_indexed_track_count': len(new_pids),
        'newly_indexed_pids': sorted(new_pids),
        'indexed_track_count': len(new_ids),
        'accepted_root_count': len(roots),
        'accepted_edge_count': sum(len(row['ordered_target_pids']) for row in roots),
        'baseline_edge_prefix_preserved': all(
            [row['ordered_target_pids'] for row in roots if row['root_pid'] == pid][0][:len(edges)] == edges
            for pid, edges in original_edges.items()),
        'catalog_edges_added': 0, 'local_album_edges_added': 0,
        'unattributed_edges_added': 0,
        'network_requests_in_merge': 0,
        'rebase_provenance': rebase_provenance,
    }
    write_json(output_dir / 'merge-audit.json', audit)
    return audit


def merge_radio_growth(*args, **kwargs):
    from radio_snapshot_proof import radio_snapshot_proof_cache_scope
    from seed_video_evidence import seed_video_selector_cache_scope
    with radio_snapshot_proof_cache_scope(), seed_video_selector_cache_scope():
        return _merge_radio_growth_with_radio_proof_cache(*args, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--identity-map', type=Path, required=True)
    parser.add_argument('--baseline-state', type=Path, required=True)
    parser.add_argument('--baseline-graph-export', type=Path, required=True)
    parser.add_argument('--rebase-report', type=Path, required=True)
    parser.add_argument('--collector-summary', type=Path, required=True)
    parser.add_argument('--seed-manifest', type=Path, required=True)
    parser.add_argument('--seed-video-evidence', type=Path)
    parser.add_argument('--snapshot', type=Path, action='append', default=[])
    parser.add_argument('--snapshot-directory', type=Path, action='append', default=[])
    parser.add_argument('--previous-growth', type=Path,
                        help='Prior merged growth directory to receipt-check and extend')
    parser.add_argument('--source-policy', choices=('authenticated-watch-radio-only-v1',),
                        help='Require independently replayable authenticated radio-only proof sidecars')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = list(args.snapshot)
    for directory in args.snapshot_directory:
        paths.extend(sorted(path for path in directory.glob('*.json')
                            if re.fullmatch(r'[A-F0-9]{16}\.json', path.name)))
    try:
        audit = merge_radio_growth(
            track_snapshot=args.track_snapshot,
            identity_map_path=args.identity_map,
            baseline_state_path=args.baseline_state,
            baseline_graph_export_path=args.baseline_graph_export,
            rebase_report_path=args.rebase_report,
            collector_summary_path=args.collector_summary,
            seed_manifest_path=args.seed_manifest,
            seed_video_evidence_path=args.seed_video_evidence,
            snapshot_paths=paths, output_dir=args.output,
            previous_growth_dir=args.previous_growth,
            source_policy=args.source_policy)
    except Exception as error:
        parser.exit(1, 'Stopped (' + type(error).__name__ + '). No database was changed.\n')
    print(json.dumps({key: audit[key] for key in (
        'collector_snapshot_input_count', 'accepted_graph_snapshot_count',
        'rejected_snapshot_count', 'newly_collected_graph_root_count',
        'baseline_active_outgoing_root_count', 'newly_active_outgoing_root_count',
        'output_active_outgoing_root_count',
        'roots_with_appended_recommendation_edges', 'appended_recommendation_edge_count',
        'recommendation_tracks_touched', 'newly_indexed_track_count',
        'indexed_track_count', 'accepted_root_count', 'accepted_edge_count')}))


if __name__ == '__main__':
    main()
