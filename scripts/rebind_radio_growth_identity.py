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


def _read(path):
    path = Path(path).resolve()
    data = path.read_bytes()
    return path, data, json.loads(data)


def _stable_map_delta(old_map, new_map):
    old_base, new_base = deepcopy(old_map), deepcopy(new_map)
    old_aliases = old_base.pop('artist_credit_aliases', [])
    new_aliases = new_base.pop('artist_credit_aliases', [])
    if old_base != new_base or not isinstance(new_aliases, list) or new_aliases[:len(old_aliases)] != old_aliases:
        raise ValueError('Map revisions differ beyond append-only whole-credit aliases')
    if len(new_aliases) <= len(old_aliases):
        raise ValueError('New identity map adds no whole-credit aliases')
    return old_aliases, new_aliases


def _rebind_state_map_hash(state, new_identity_map_sha256):
    """Change only the state source map hash and reseal its document."""
    rebound = deepcopy(state)
    rebound['source']['identity_map_sha256'] = new_identity_map_sha256
    rebound['state_sha256'] = state_hash({key: value for key, value in rebound.items()
                                          if key != 'state_sha256'})
    return rebound


def rebind_radio_growth(*, track_snapshot, old_identity_map_path, new_identity_map_path,
                        alias_application_receipt_path, review_decisions_path,
                        proposals_path, baseline_state_path, baseline_graph_export_path,
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
    _, new_aliases = _stable_map_delta(old_map, new_map)
    old_matcher = IdentityMap(old_map, tracks, library_hash)
    new_matcher = IdentityMap(new_map, tracks, library_hash)

    app_path, app_bytes, application = _read(alias_application_receipt_path)
    review_path, review_bytes, review = _read(review_decisions_path)
    proposal_path, proposal_bytes, proposals = _read(proposals_path)
    if (application.get('operation') != 'apply_reviewed_whole_artist_credit_aliases'
            or application.get('input_identity_map_sha256') != old_hash
            or application.get('output_identity_map_sha256') != new_hash
            or application.get('review_decisions_sha256') != sha(review_bytes)
            or application.get('proposal_sha256') != sha(proposal_bytes)
            or application.get('recording_identity_verified') is not False
            or review.get('approval', {}).get('reviewed_by') != 'root'
            or review.get('approval', {}).get('recording_identity_verified') is not False
            or review.get('approval', {}).get('new_recording_edges_authorized') is not False
            or len(review.get('decisions', [])) != len(new_aliases) - len(old_map.get('artist_credit_aliases', []))):
        raise ValueError('Alias review/application receipts do not authorize this exact map revision')
    proposal_report = proposals.get('source_report') or {}
    report_source = Path(proposal_report.get('path') or '').resolve()
    if (not report_source.is_file() or sha(report_source.read_bytes()) != proposal_report.get('sha256')
            or review.get('proposal_sha256') != sha(proposal_bytes)
            or review.get('source_report_sha256') != proposal_report.get('sha256')):
        raise ValueError('Reviewed credit proposals are not bound to their unchanged source report')

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
        'schema_version': 1, 'library_sha256': library_hash,
        'old_identity_map_sha256': old_hash, 'new_identity_map_sha256': new_hash,
        'old_identity_map': {'path': str(old_path), 'sha256': old_hash},
        'new_identity_map': {'path': str(new_path), 'sha256': new_hash},
        'track_snapshot': {'path': str(snapshot_path), 'sha256': snapshot_hash},
        'alias_application_receipt': {'path': str(app_path), 'sha256': sha(app_bytes)},
        'review_decisions': {'path': str(review_path), 'sha256': sha(review_bytes)},
        'proposals': {'path': str(proposal_path), 'sha256': sha(proposal_bytes)},
        'source_rebase': {
            'graph_export_path': str(Path(baseline_graph_export_path).resolve()),
            'graph_export_sha256': base_provenance['graph_export_sha256'],
            'rebase_report_path': str(Path(rebase_report_path).resolve()),
            'rebase_report_sha256': base_provenance['rebase_report_sha256'],
            'state_path': str(Path(baseline_state_path).resolve()),
            'state_file_sha256': base_provenance['state_file_sha256'],
        },
        'source_growth': {'path': str(growth_path), 'accepted_graphs_sha256': accepted_hash},
        'old_state_sha256': old_state['state_sha256'],
        'new_state_sha256': rebound_state['state_sha256'],
        'growth_run_count_replayed_under_old_map': len(normalized_runs),
        'local_track_metadata_unchanged': True,
        'local_equivalence_labels_unchanged': True,
        'pid_gid_assignments_unchanged': True,
        'metadata_bytes_unchanged': True,
        'relation_bytes_unchanged': True,
        'old_growth_replayed_with_old_map': True,
        'activated_whole_credit_alias_count': len(new_aliases) - len(old_map.get('artist_credit_aliases', [])),
        'recording_identity_verified': False,
        'recording_edges_added_by_rebinding': 0,
    }
    relation_audit = deepcopy(source_audit)
    relation_audit['output_directed_edge_count'] = sum(
        len(row['ordered_target_pids']) for row in roots)
    report = {
        'schema_version': 1,
        'producer': 'opengenius-typed-identity-map-rebinding-v1',
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
            baseline_state_path=args.baseline_state,
            baseline_graph_export_path=args.baseline_graph_export,
            rebase_report_path=args.rebase_report,
            growth_dir=args.growth_dir, output_dir=args.output)
    except Exception as error:
        parser.exit(1, 'Stopped (' + type(error).__name__ + '): ' + str(error) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
