"""Create a source-bound current-map receipt for an unchanged Genius baseline.

This rebinding changes only the identity-map hash in a verified synthetic
baseline state. It replays the reviewed alias transitions, the original
rebase receipt, and the baseline SQL rows before writing a separate artifact.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from apply_artist_title_aliases import verify_title_alias_transition
from music_identity_map import (
    IdentityMap, apply_artist_credit_alias_review, load_library,
)
from merge_rebased_radio_growth import (
    _relation_map, _state_graph_rows, _verify_rebase_receipt, write_json,
)
from plan_genius_update import _hash as state_hash, _source as identity_source, plan_genius_update
from update_genius_bundle import read_bundle, state_rows, update_bundle


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    return path, raw, json.loads(raw)


def _receipt(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': _sha(path.read_bytes())}


def _reseal_map_hash(state, map_sha256):
    rebound = deepcopy(state)
    rebound['source']['identity_map_sha256'] = map_sha256
    rebound['state_sha256'] = state_hash({k: v for k, v in rebound.items()
                                          if k != 'state_sha256'})
    return rebound


def _assert_sql_state_rows_unchanged(original, rebound):
    old_ids, old_metadata, old_relations = state_rows(original)
    new_ids, new_metadata, new_relations = state_rows(rebound)
    if ((old_ids, old_metadata, old_relations) != (new_ids, new_metadata, new_relations)
            or original['rows'] != rebound['rows']):
        raise ValueError('Baseline rebind changed SQL PID/GID, metadata, or relation rows')


def rebind_baseline(*, bundle_path, track_snapshot_path, old_identity_map_path,
                    new_identity_map_path, credit_intermediate_map_path,
                    credit_application_receipt_path, credit_review_path,
                    credit_proposals_path, title_application_receipt_path,
                    title_review_path, title_proposals_path, baseline_state_path,
                    baseline_graph_path, rebase_report_path, executable_path,
                    output_dir):
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError('Output directory exists; choose a new directory')

    snapshot_path, snapshot_bytes, _ = _read(track_snapshot_path)
    tracks, library_hash, _ = load_library(track_snapshot=snapshot_path)
    old_path, old_bytes, old_map = _read(old_identity_map_path)
    new_path, new_bytes, new_map = _read(new_identity_map_path)
    credit_map_path, credit_map_bytes, credit_map = _read(credit_intermediate_map_path)
    old_hash, new_hash = _sha(old_bytes), _sha(new_bytes)
    if any(doc.get('library_sha256') != library_hash
           for doc in (old_map, credit_map, new_map)):
        raise ValueError('Identity-map transition belongs to a different Library')

    # Reproduce the exact whole-credit stage and bind its independently
    # reviewed inputs to the receipt that produced the intermediate map.
    credit_receipt_path, credit_receipt_bytes, credit_receipt = _read(
        credit_application_receipt_path)
    credit_review_path, credit_review_bytes, credit_review = _read(credit_review_path)
    credit_proposals_path, credit_proposals_bytes, credit_proposals = _read(
        credit_proposals_path)
    source_ref = credit_proposals.get('source_report') or {}
    source_report_path = Path(source_ref.get('path') or '').resolve()
    if (not source_report_path.is_file()
            or _sha(source_report_path.read_bytes()) != source_ref.get('sha256')
            or credit_review.get('source_report_sha256') != source_ref.get('sha256')
            or credit_receipt.get('operation') != 'apply_reviewed_whole_artist_credit_aliases'
            or credit_receipt.get('library_sha256') != library_hash
            or credit_receipt.get('input_identity_map_sha256') != old_hash
            or credit_receipt.get('output_identity_map_sha256') != _sha(credit_map_bytes)
            or credit_receipt.get('proposal_sha256') != _sha(credit_proposals_bytes)
            or credit_receipt.get('review_decisions_sha256') != _sha(credit_review_bytes)
            or credit_receipt.get('source_report_sha256') != source_ref.get('sha256')
            or credit_receipt.get('recording_identity_verified') is not False
            or credit_review.get('approval', {}).get('reviewed_by') != 'root'
            or credit_review.get('approval', {}).get('recording_identity_verified') is not False
            or credit_review.get('approval', {}).get('new_recording_edges_authorized') is not False):
        raise ValueError('Whole-credit review/application receipt chain is invalid')
    rebuilt_credit_map = apply_artist_credit_alias_review(
        old_map, credit_review, credit_proposals, tracks, library_hash, old_hash,
        _sha(credit_proposals_bytes))
    if rebuilt_credit_map != credit_map:
        raise ValueError('Reviewed whole-credit receipt does not reproduce its map')

    # Reproduce the final spelling stage from its exact review and application
    # receipt, including the intermediate-map file hash.
    title_receipt_path, title_receipt_bytes, title_receipt = _read(
        title_application_receipt_path)
    title_review_path, title_review_bytes, title_review = _read(title_review_path)
    title_proposals_path, title_proposals_bytes, title_proposals = _read(title_proposals_path)
    if (title_receipt.get('input_identity_map_sha256') != _sha(credit_map_bytes)
            or title_receipt.get('output_identity_map_sha256') != new_hash
            or title_receipt.get('recording_identity_verified') is not False
            or title_receipt.get('recording_edges_added') != 0
            or title_review.get('approval', {}).get('reviewed_by') != 'root'
            or title_review.get('approval', {}).get('recording_identity_verified') is not False
            or title_review.get('approval', {}).get('new_recording_edges_authorized') is not False):
        raise ValueError('Exact-title review/application receipt chain is invalid')
    verified_title = verify_title_alias_transition(
        track_snapshot=snapshot_path,
        input_identity_map_path=credit_map_path,
        output_identity_map_path=new_path,
        proposals_path=title_proposals_path,
        review_decisions_path=title_review_path,
        receipt_path=title_receipt_path)
    if (verified_title.get('output_identity_map_sha256') != new_hash
            or title_receipt.get('proposal_sha256') != _sha(title_proposals_bytes)
            or title_receipt.get('review_decisions_sha256') != _sha(title_review_bytes)):
        raise ValueError('Title-map receipt replay differs from the final map')

    old_matcher = IdentityMap(old_map, tracks, library_hash)
    new_matcher = IdentityMap(new_map, tracks, library_hash)
    _, old_labels = identity_source(old_matcher, library_hash, old_hash)
    _, new_labels = identity_source(new_matcher, library_hash, new_hash)
    if old_labels != new_labels:
        raise ValueError('Reviewed aliases changed canonical Library identity labels')

    graph_path, graph_bytes, source_graph = _read(baseline_graph_path)
    report_path, report_bytes, _ = _read(rebase_report_path)
    state_path, state_bytes, old_state = _read(baseline_state_path)
    verified_graph, verified_report, verified_state, source_provenance = _verify_rebase_receipt(
        graph_path, report_path, state_path, library_hash, old_hash)
    if (_sha(graph_bytes) != source_provenance['graph_export_sha256']
            or _sha(report_bytes) != source_provenance['rebase_report_sha256']
            or _sha(state_bytes) != source_provenance['state_file_sha256']):
        raise ValueError('Original baseline source receipts changed during verification')
    base_roots = verified_graph['accepted_roots']
    if _relation_map(base_roots, 'Original baseline graph') != _relation_map(
            _state_graph_rows(verified_state), 'Original baseline state'):
        raise ValueError('Original baseline graph differs from its typed state')
    if plan_genius_update(verified_state, base_roots, old_matcher,
                          library_hash, old_hash)['next_state']['state_sha256'] != verified_state['state_sha256']:
        raise ValueError('Original baseline does not replay under its source identity map')

    rebound_state = _reseal_map_hash(verified_state, new_hash)
    rebound_plan = plan_genius_update(rebound_state, base_roots, new_matcher,
                                      library_hash, new_hash)['next_state']
    if rebound_plan['state_sha256'] != rebound_state['state_sha256']:
        raise ValueError('Baseline state is not reproducible under the reviewed map')
    _assert_sql_state_rows_unchanged(verified_state, rebound_state)
    new_ids, _, _ = state_rows(rebound_state)

    # Run the native-bundle updater in memory with the baseline graph itself.
    # Its full SQL comparison proves this physical 515-track source matches
    # the baseline state before any later relation expansion is attempted.
    files = read_bundle(Path(bundle_path).resolve())
    prepared_files, sql_report, _ = update_bundle(
        files, rebound_state, base_roots, new_matcher, library_hash, new_hash,
        Path(executable_path).resolve(), allow_unverified_profile=True)
    changed_files = {name for name in files if prepared_files[name] != files[name]}
    if (changed_files - {'Library.musicdb'}
            or sql_report.get('sql_integrity_check') != 'ok'
            or sql_report.get('changed_id_slots')
            or not sql_report.get('config_and_auxiliary_tables_preserved')
            or not sql_report.get('local_genius_state_preserved')):
        raise ValueError('Physical baseline SQL or existing PID/GID assignments differ from typed state')

    output_dir.mkdir(parents=True)
    state_out = output_dir / 'baseline-state.json'
    graph_out = output_dir / 'accepted-baseline-graphs.json'
    receipt_out = output_dir / 'baseline-identity-rebinding-receipt.json'
    report_out = output_dir / 'baseline-identity-rebinding-report.json'
    write_json(state_out, rebound_state)
    graph_doc = {
        'schema_version': 1,
        'producer': 'opengenius-typed-rebound-baseline-graph-v1',
        'source_state_path': str(state_out.resolve()),
        'source_state_sha256': _sha(state_out.read_bytes()),
        'source_rebinding_receipt_path': str(receipt_out.resolve()),
        'accepted_roots': base_roots,
        'identity_status': 'unverified_metadata_candidate',
    }
    write_json(graph_out, graph_doc)
    receipt = {
        'schema_version': 1,
        'producer': 'opengenius-typed-genius-baseline-identity-rebind-v1',
        'old_identity_map': _receipt(old_path),
        'new_identity_map': _receipt(new_path),
        'credit_intermediate_map': _receipt(credit_map_path),
        'credit_application_receipt': _receipt(credit_receipt_path),
        'credit_review_decisions': _receipt(credit_review_path),
        'credit_proposals': _receipt(credit_proposals_path),
        'title_application_receipt': _receipt(title_receipt_path),
        'title_review_decisions': _receipt(title_review_path),
        'title_proposals': _receipt(title_proposals_path),
        'track_snapshot': _receipt(snapshot_path),
        'source_baseline_state': _receipt(state_path),
        'source_baseline_graph': _receipt(graph_path),
        'source_rebase_report': _receipt(report_path),
        'source_bundle_file_sha256': {name: _sha(blob) for name, blob in sorted(files.items())},
        'new_state_path': str(state_out.resolve()),
        'new_state_sha256': _sha(state_out.read_bytes()),
        'new_graph_path': str(graph_out.resolve()),
        'new_graph_sha256': _sha(graph_out.read_bytes()),
        'preserved_track_count': len(tracks),
        'preserved_state_assignment_count': len(new_ids),
        'preserved_directed_edge_count': sum(len(row['ordered_target_pids']) for row in base_roots),
        'pid_gid_assignments_unchanged': True,
        'metadata_bytes_unchanged': True,
        'ordered_relation_bytes_unchanged': True,
        'canonical_identity_labels_unchanged': True,
        'physical_bundle_sql_check': sql_report,
        'source_bundle_unchanged_on_disk': True,
        'prepared_library_changed_only_for_sync_revisions': changed_files <= {'Library.musicdb'},
        'recording_identity_verified': False,
        'recording_edges_added': 0,
        'network_requests': 0,
    }
    write_json(receipt_out, receipt)
    report = {
        'schema_version': 1,
        'producer': 'opengenius-typed-genius-baseline-identity-rebind-report-v1',
        'receipt_path': str(receipt_out.resolve()),
        'receipt_sha256': _sha(receipt_out.read_bytes()),
        'source_rebase_provenance': source_provenance,
        'new_state_sha256': rebound_state['state_sha256'],
        'preserved_track_count': len(tracks),
        'preserved_state_assignment_count': len(new_ids),
        'physical_bundle_integrity': sql_report.get('sql_integrity_check'),
        'source_bundle_unchanged_on_disk': True,
        'prepared_library_changed_only_for_sync_revisions': changed_files <= {'Library.musicdb'},
        'network_requests': 0,
        'recording_identity_verified': False,
    }
    write_json(report_out, report)
    return {'output_dir': str(output_dir), 'state_sha256': rebound_state['state_sha256'],
            'track_count': len(tracks), 'assignment_count': len(new_ids),
            'directed_edge_count': receipt['preserved_directed_edge_count'],
            'sql_integrity_check': sql_report.get('sql_integrity_check'),
            'receipt_sha256': _sha(receipt_out.read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'track-snapshot', 'old-identity-map', 'new-identity-map',
                 'credit-intermediate-map', 'credit-application-receipt',
                 'credit-review', 'credit-proposals', 'title-application-receipt',
                 'title-review', 'title-proposals', 'baseline-state',
                 'baseline-graph', 'rebase-report', 'executable', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = rebind_baseline(
            bundle_path=args.bundle, track_snapshot_path=args.track_snapshot,
            old_identity_map_path=args.old_identity_map,
            new_identity_map_path=args.new_identity_map,
            credit_intermediate_map_path=args.credit_intermediate_map,
            credit_application_receipt_path=args.credit_application_receipt,
            credit_review_path=args.credit_review, credit_proposals_path=args.credit_proposals,
            title_application_receipt_path=args.title_application_receipt,
            title_review_path=args.title_review, title_proposals_path=args.title_proposals,
            baseline_state_path=args.baseline_state, baseline_graph_path=args.baseline_graph,
            rebase_report_path=args.rebase_report, executable_path=args.executable,
            output_dir=args.output)
    except Exception as error:
        parser.exit(1, 'Stopped (' + type(error).__name__ + '): ' + str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
