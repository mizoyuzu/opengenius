"""Report recommendation coverage by evidence source without blending metrics.

This is an offline audit of the saved graph artifacts. It does not infer radio
coverage from indexed-row count: radio roots, radio-touched tracks, remote
catalog edges, and local album edges are reported independently.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

from evaluate_ytmusic_batch import collect_graphs, merge_graphs
from genius_format import parse_similarities
from music_identity_map import IdentityMap, normalize, version_facets, version_signature
from update_genius_bundle import state_rows


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _json_sha(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode('utf-8')
    return _sha_bytes(raw)


def _edge_set(rows):
    edges = set()
    for row in rows:
        root = row['root_pid']
        targets = row['ordered_target_pids']
        if len(targets) != len(set(targets)):
            raise ValueError('duplicate target in a source graph')
        if root in targets:
            raise ValueError('self edge in a source graph')
        edges.update((root, target) for target in targets)
    return edges


def _root_sets(rows_by_type):
    return {kind: {row['root_pid'] for row in rows if row['ordered_target_pids']}
            for kind, rows in rows_by_type.items()}


def _facet_profile(source):
    profile = source.get('version_facet_profile') or {}
    return tuple((key, tuple(profile.get(key, []))) for key in
                 ('kinds', 'markers', 'unknown_qualifiers', 'years'))


def _coverage_counts(base_rows, remote_rows, local_rows, final_rows):
    baseline_edges = _edge_set(base_rows)
    remote_edges = _edge_set(remote_rows)
    local_edges = _edge_set(local_rows)
    final_edges = _edge_set(final_rows)
    if final_edges != baseline_edges | remote_edges | local_edges:
        raise ValueError('final edges differ from deduplicated typed source union')
    if len(final_edges) != sum(len(row['ordered_target_pids']) for row in final_rows):
        raise ValueError('duplicate final directed edge')
    baseline_seeds = {row['root_pid'] for row in base_rows}
    baseline_outgoing_roots = {row['root_pid'] for row in base_rows if row['ordered_target_pids']}
    baseline_targets = {target for _, target in baseline_edges}
    baseline_touched = baseline_outgoing_roots | baseline_targets
    baseline_incoming_only = baseline_targets - baseline_outgoing_roots
    typed_root_sets = _root_sets({'baseline_recommendation': base_rows,
                                  'remote_catalog': remote_rows,
                                  'local_catalog': local_rows})
    root_membership = {root: [kind for kind, roots in typed_root_sets.items() if root in roots]
                       for root in set.union(*typed_root_sets.values())}
    buckets = {'baseline_recommendation_only': 0, 'remote_catalog_only': 0, 'local_catalog_only': 0,
                'baseline_recommendation_and_remote': 0, 'baseline_recommendation_and_local': 0,
                'remote_and_local': 0, 'all_three': 0}
    bucket_for = {('baseline_recommendation',): 'baseline_recommendation_only', ('remote_catalog',): 'remote_catalog_only',
                  ('local_catalog',): 'local_catalog_only',
                  ('baseline_recommendation', 'remote_catalog'): 'baseline_recommendation_and_remote',
                  ('baseline_recommendation', 'local_catalog'): 'baseline_recommendation_and_local',
                  ('remote_catalog', 'local_catalog'): 'remote_and_local',
                  ('baseline_recommendation', 'remote_catalog', 'local_catalog'): 'all_three'}
    for sources in root_membership.values():
        bucket = bucket_for.get(tuple(sources))
        if bucket is None:
            raise ValueError('unclassified source-root overlap')
        buckets[bucket] += 1
    return {
        'baseline_recommendation_seed_roots': len(baseline_seeds),
        'baseline_recommendation_roots_with_nonempty_outgoing_edges': len(baseline_outgoing_roots),
        'baseline_recommendation_touched_tracks': len(baseline_touched),
        'baseline_recommendation_incoming_only_tracks': len(baseline_incoming_only),
        'baseline_recommendation_seed_roots_without_incident_edges': len(baseline_seeds - baseline_touched),
        'root_source_coverage': {
            'baseline_recommendation_roots': len(typed_root_sets['baseline_recommendation']),
            'remote_catalog_roots': len(typed_root_sets['remote_catalog']),
            'local_catalog_roots': len(typed_root_sets['local_catalog']),
            'union_roots': len(root_membership),
            'overlap_buckets': buckets,
        },
        'directed_edges': {
            'baseline_recommendation_graph': len(baseline_edges),
            'remote_catalog_candidates': len(remote_edges),
            'local_album_catalog_candidates': len(local_edges),
            'baseline_remote_overlap': len(baseline_edges & remote_edges),
            'baseline_local_overlap': len(baseline_edges & local_edges),
            'remote_local_overlap': len(remote_edges & local_edges),
            'baseline_remote_local_overlap': len(baseline_edges & remote_edges & local_edges),
            'remote_catalog_edges_added_after_baseline_dedup': len(remote_edges - baseline_edges),
            'local_album_edges_added_after_baseline_and_remote_dedup': len(local_edges - baseline_edges - remote_edges),
            'deduplicated_union': len(final_edges),
            'final_relation_rows': len(final_rows),
            'final_edge_count_matches_union': True,
        },
    }


def build_report(graph_dir, baseline_graphs_path, baseline_state_path,
                 track_snapshot_path):
    graph_dir = Path(graph_dir)
    relations = _read(graph_dir / 'relations.json')
    accepted = _read(graph_dir / 'accepted-graphs.json')
    state = _read(graph_dir / 'state.json')
    provenance = _read(graph_dir / 'catalog-provenance.json')
    baseline_graphs = _read(baseline_graphs_path)
    baseline_state = _read(baseline_state_path)
    snapshot = _read(track_snapshot_path)

    # Validate sealed row sets and all artifact links before counting coverage.
    state_ids, _, state_relations = state_rows(state)
    baseline_ids, _, baseline_relations = state_rows(baseline_state)
    lib_hash = relations['source_library_sha256']
    if (state['source'].get('library_sha256') != lib_hash
            or accepted.get('library_sha256') != lib_hash
            or provenance.get('library_sha256') != lib_hash
            or snapshot.get('source_sha256') != lib_hash
            or _sha_bytes(Path(track_snapshot_path).read_bytes())
            != provenance['local_catalog_source']['source_track_snapshot_sha256']):
        raise ValueError('Library or track snapshot hash provenance mismatch')
    if state['state_sha256'] != relations['source_state_sha256']:
        raise ValueError('Output state hash provenance mismatch')
    baseline_file_sha = _sha_bytes(Path(baseline_state_path).read_bytes())
    accepted_file_sha = _sha_bytes(Path(baseline_graphs_path).read_bytes())
    if (baseline_state['state_sha256'] != provenance.get('baseline_state_sha256')
            or baseline_state['state_sha256'] != accepted.get('baseline_state_sha256')
            or baseline_file_sha != provenance.get('baseline_state_file_sha256')
            or accepted_file_sha != provenance.get('baseline_accepted_graphs_sha256')
            or baseline_graphs.get('source_state_sha256') != baseline_file_sha):
        raise ValueError('Baseline graph/state hash provenance mismatch')
    catalog_source = accepted.get('catalog_source') or {}
    for artifact_path_key, artifact_sha_key, accepted_sha_key in (
            ('catalog_evidence_path', 'catalog_evidence_sha256', 'catalog_evidence_sha256'),
            ('album_plan_path', 'album_plan_sha256', 'album_plan_sha256')):
        artifact_path = Path(provenance[artifact_path_key])
        artifact_sha = provenance[artifact_sha_key]
        if (catalog_source.get(accepted_sha_key) != artifact_sha
                or not artifact_path.is_file()
                or _sha_bytes(artifact_path.read_bytes()) != artifact_sha):
            raise ValueError('remote catalog source artifact hash mismatch')
    if snapshot.get('tracks') is None:
        raise ValueError('track snapshot has no tracks')
    tracks = snapshot['tracks']
    tracks_by_pid = {row['persistent_id']: row for row in tracks}
    if len(tracks_by_pid) != len(tracks) or not set(state_ids) <= tracks_by_pid.keys():
        raise ValueError('state IDs do not match unique Library snapshot tracks')

    base_roots = baseline_graphs['accepted_roots']
    baseline_rows_by_root = {row['root_pid']: row for row in base_roots}
    baseline_edges = _edge_set(base_roots)
    def state_edges(ids, relations):
        reverse = {gid: pid for pid, gid in ids.items()}
        edges = set()
        for pid, gid in ids.items():
            _, targets = parse_similarities(relations[gid])
            try:
                edges.update((pid, reverse[target]) for target in targets)
            except KeyError:
                raise ValueError('state relation points outside its PID map') from None
        return edges
    if state_edges(baseline_ids, baseline_relations) != baseline_edges:
        raise ValueError('baseline recommendation graph differs from sealed baseline state edges')
    remote_rows = accepted['new_graphs']
    local_rows = accepted['local_album_catalog_graphs']
    remote_edges = _edge_set(remote_rows)
    local_edges = _edge_set(local_rows)
    final_rows = relations['relation_rows']
    final_edges = _edge_set(final_rows)
    if state_edges(state_ids, state_relations) != final_edges:
        raise ValueError('final relation graph differs from sealed output state edges')
    if len(base_roots) != len(baseline_ids):
        raise ValueError('baseline radio root count differs from baseline indexed IDs')
    if {row['root_pid'] for row in final_rows} != set(state_ids):
        raise ValueError('final relation roots differ from sealed state assignments')
    if final_edges != baseline_edges | remote_edges | local_edges:
        raise ValueError('final edges differ from deduplicated typed source union')
    if len(final_edges) != sum(len(row['ordered_target_pids']) for row in final_rows):
        raise ValueError('duplicate final directed edge')

    # Validate the detailed edge ledger, including original-track hashes for
    # local album edges and exact version facet preservation.
    ledger = provenance.get('edge_provenance', [])
    ledger_by_type = {'catalog_index': set(), 'local_album_catalog': set()}
    generic_artist_edges = set()
    facet_mismatch_count = 0
    local_hash_mismatch_count = 0
    remote_source_hash_mismatch_count = 0
    remote_responses = {row['sha256'] for row in provenance.get('catalog_response_inputs', [])}
    for response in provenance.get('catalog_response_inputs', []):
        path = Path(response['path'])
        if not path.is_file() or _sha_bytes(path.read_bytes()) != response['sha256']:
            remote_source_hash_mismatch_count += 1
    generic_credits = {'va', 'various artists', 'various', 'soundtrack', 'original soundtrack'}
    for item in ledger:
        edge = (item['root_pid'], item['target_pid'])
        kind = item['source_relation']
        if kind == 'radio':
            source = item.get('source') or {}
            expected_root_row = baseline_rows_by_root.get(item['root_pid'])
            if (source.get('accepted_graphs_sha256') != accepted_file_sha
                    or source.get('baseline_state_sha256') != baseline_state['state_sha256']
                    or source.get('accepted_root_record_sha256') != _json_sha(expected_root_row)
                    or source.get('root_pid') != item['root_pid']):
                raise ValueError('radio edge source provenance hash mismatch')
            continue
        if kind not in ledger_by_type:
            raise ValueError('unknown non-radio edge provenance type')
        ledger_by_type[kind].add(edge)
        sources = item.get('sources') or []
        if not sources:
            raise ValueError('edge provenance is missing source evidence')
        for source in sources:
            if kind == 'local_album_catalog':
                if (source.get('source_library_sha256') != lib_hash
                        or source.get('source_track_snapshot_sha256') != provenance['local_catalog_source']['source_track_snapshot_sha256']):
                    local_hash_mismatch_count += 1
                left, right = tracks_by_pid.get(edge[0]), tracks_by_pid.get(edge[1])
                if left is None or right is None:
                    raise ValueError('local catalog edge references unknown Library PID')
                canonical = lambda row: _sha_bytes(json.dumps(row, ensure_ascii=False,
                    sort_keys=True, separators=(',', ':')).encode('utf-8'))
                # Builders hash the record before adding internal audit fields;
                # compare only source row fields, which are exactly this snapshot.
                if (canonical(left) != source.get('root_track_record_sha256')
                        or canonical(right) != source.get('target_track_record_sha256')):
                    local_hash_mismatch_count += 1
                def expected_signature(track):
                    title = track.get('title') or ''
                    signature = set(version_signature(title))
                    normalized = normalize(title).replace('_', ' ')
                    if re.search(r'\bbgm\b|ＢＧＭ', normalized, re.IGNORECASE):
                        signature.add('bgm')
                    if re.search(r'\bvocal\b|ボーカル', normalized, re.IGNORECASE):
                        signature.add('vocal')
                    return sorted(signature)
                root_facets = version_facets(left.get('title') or '')
                target_facets = version_facets(right.get('title') or '')
                facet_keys = ('markers', 'years', 'kinds', 'unknown_qualifiers')
                expected_profile = {key: root_facets[key] for key in facet_keys}
                target_profile = {key: target_facets[key] for key in facet_keys}
                expected_profile_tuple = tuple((key, tuple(expected_profile[key])) for key in
                    ('kinds', 'markers', 'unknown_qualifiers', 'years'))
                if (source.get('root_version_signature') != source.get('target_version_signature')
                        or source.get('root_version_signature') != expected_signature(left)
                        or expected_signature(left) != expected_signature(right)
                        or _facet_profile(source) != expected_profile_tuple
                        or expected_profile != target_profile):
                    facet_mismatch_count += 1
                credits = {str(value).strip().casefold() for credit in
                           source.get('canonical_artist_credits', [])
                           for value in (credit if isinstance(credit, list) else [credit])}
                if credits & generic_credits:
                    generic_artist_edges.add(edge)
            else:
                if (source.get('target_response_sha256') not in remote_responses
                        or source.get('root_response_sha256') not in remote_responses):
                    remote_source_hash_mismatch_count += 1
    if (ledger_by_type['catalog_index'] != remote_edges
            or ledger_by_type['local_album_catalog'] != local_edges):
        raise ValueError('typed edge ledger does not cover accepted graph edge sets')
    if local_hash_mismatch_count or facet_mismatch_count or remote_source_hash_mismatch_count:
        raise ValueError('typed edge provenance validation failed')

    coverage = _coverage_counts(base_roots, remote_rows, local_rows, final_rows)

    track_total = len(tracks)
    indexed_count = len(state_ids)
    return {
        'schema_version': 1,
        'evidence': {
            'library_sha256': lib_hash,
            'baseline_state_sha256': baseline_state['state_sha256'],
            'output_state_sha256': state['state_sha256'],
            'source_hashes_validated': True,
            'sealed_states_validated': True,
            'typed_edge_ledger_validated': True,
        },
        'library_coverage': {
            'library_tracks': track_total,
            'indexed_tracks': indexed_count,
            'indexed_fraction': round(indexed_count / track_total, 6) if track_total else None,
            'baseline_recommendation_seed_roots': coverage['baseline_recommendation_seed_roots'],
            'baseline_recommendation_seed_root_fraction_of_library': round(
                coverage['baseline_recommendation_seed_roots'] / track_total, 6) if track_total else None,
            'baseline_recommendation_roots_with_nonempty_outgoing_edges':
                coverage['baseline_recommendation_roots_with_nonempty_outgoing_edges'],
            'baseline_recommendation_touched_tracks': coverage['baseline_recommendation_touched_tracks'],
            'baseline_recommendation_incoming_only_tracks': coverage['baseline_recommendation_incoming_only_tracks'],
            'baseline_recommendation_seed_roots_without_incident_edges':
                coverage['baseline_recommendation_seed_roots_without_incident_edges'],
            'baseline_recommendation_touched_track_ids_not_present_in_library': len(
                ({target for edge in baseline_edges for target in edge} | {root for root, _ in baseline_edges})
                - tracks_by_pid.keys()),
        },
        'root_source_coverage': coverage['root_source_coverage'],
        'directed_edges': coverage['directed_edges'],
        'negative_case_checks': {
            'local_edge_track_record_hash_mismatches': local_hash_mismatch_count,
            'local_edge_version_facet_mismatches': facet_mismatch_count,
            'remote_edges_missing_response_hashes': remote_source_hash_mismatch_count,
            'generic_artist_credit_edge_count': len(generic_artist_edges),
            'generic_artist_credit_policy': 'counted as album evidence only; never claimed as artist-closeness evidence',
            'remix_and_other_version_facets': 'checked for exact root/target signature equality on every local edge',
            'unknown_version_qualifiers': 'retained in full facet profile and checked for equality',
        },
        'interpretation': {
            'indexed_track_fraction_is_baseline_recommendation_coverage': False,
            'local_album_edges_are_baseline_recommendation_observations': False,
            'baseline_graph_endpoint_relation_scope': 'accepted pre-catalog YTMusic recommendation graph; radio and related endpoint provenance is not split here',
            'catalog_edges_are_music_distance_scores': False,
        },
    }


def audit_genuine_growth(growth_dir, baseline_state_path, track_snapshot_path):
    """Independently recompute a radio/related growth merge from raw snapshots."""
    growth_dir = Path(growth_dir)
    accepted = _read(growth_dir / 'accepted-graphs.json')
    state = _read(growth_dir / 'state.json')
    relations = _read(growth_dir / 'relations.json')
    audit = _read(growth_dir / 'merge-audit.json')
    inputs = _read(growth_dir / 'inputs.json')
    baseline_state = _read(baseline_state_path)
    snapshot = _read(track_snapshot_path)
    baseline_ids, _, baseline_relations = state_rows(baseline_state)
    ids, _, state_relations = state_rows(state)
    lib_hash = snapshot.get('source_sha256')
    if (accepted.get('producer') != 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1'
            or audit.get('producer') != accepted.get('producer')
            or accepted.get('library_sha256') != lib_hash
            or state.get('source', {}).get('library_sha256') != lib_hash
            or relations.get('source_library_sha256') != lib_hash
            or audit.get('library_sha256') != lib_hash
            or inputs.get('library_sha256') != lib_hash
            or accepted.get('identity_map_sha256') != inputs.get('identity_map_sha256')
            or state.get('source') != baseline_state.get('source')
            or accepted.get('baseline_state_sha256') != baseline_state.get('state_sha256')
            or audit.get('baseline_state_sha256') != baseline_state.get('state_sha256')
            or relations.get('source_state_sha256') != state.get('state_sha256')):
        raise ValueError('genuine recommendation growth Library/state provenance mismatch')
    track_snapshot_record = inputs.get('track_snapshot') or {}
    if (Path(track_snapshot_record.get('path', '')).resolve() != Path(track_snapshot_path).resolve()
            or track_snapshot_record.get('sha256') != _sha_bytes(Path(track_snapshot_path).read_bytes())
            or audit.get('track_snapshot_sha256') != track_snapshot_record.get('sha256')):
        raise ValueError('genuine recommendation track snapshot hash mismatch')

    identity_record = inputs.get('identity_map') or {}
    identity_path = Path(identity_record.get('path', '')).resolve()
    if (not identity_path.is_file()
            or _sha_bytes(identity_path.read_bytes()) != identity_record.get('sha256')
            or identity_record.get('sha256') != accepted.get('identity_map_sha256')):
        raise ValueError('genuine recommendation identity map hash mismatch')
    tracks = snapshot.get('tracks') or []
    matcher = IdentityMap(_read(identity_path), tracks, lib_hash)

    def verified_input(key):
        record = inputs.get(key) or {}
        path = Path(record.get('path', '')).resolve()
        if not path.is_file() or _sha_bytes(path.read_bytes()) != record.get('sha256'):
            raise ValueError('genuine recommendation input hash mismatch: ' + key)
        return path, record

    # The baseline state and exported graph are tied to the original verified
    # rebase receipt, including every raw snapshot hash in that receipt.
    for key in ('baseline_state', 'baseline_graph_export', 'rebase_report',
                'collector_summary', 'seed_manifest', 'seed_video_evidence'):
        verified_input(key)
    if (Path(inputs['baseline_state']['path']).resolve() != Path(baseline_state_path).resolve()
            or inputs['baseline_state']['sha256'] != _sha_bytes(Path(baseline_state_path).read_bytes())):
        raise ValueError('genuine recommendation baseline state file mismatch')
    baseline_graph_doc = _read(verified_input('baseline_graph_export')[0])
    baseline_graph_rows = accepted.get('baseline_accepted_roots')
    baseline_edges = _edge_set(baseline_graph_rows)
    old_ids = set(baseline_ids)
    baseline_reverse = {gid: pid for pid, gid in baseline_ids.items()}
    state_baseline_edges = set()
    for pid, gid in baseline_ids.items():
        _, target_ids = parse_similarities(baseline_relations[gid])
        state_baseline_edges.update((pid, baseline_reverse[target]) for target in target_ids)
    if (baseline_graph_doc.get('accepted_roots') != baseline_graph_rows
            or baseline_graph_doc.get('source_state_sha256')
            != inputs['baseline_state']['sha256']
            or state_baseline_edges != baseline_edges):
        raise ValueError('genuine recommendation baseline differs from its saved state')
    rebase_report_path, rebase_record = verified_input('rebase_report')
    rebase_report = _read(rebase_report_path)
    rebase_audit = rebase_report.get('relation_revalidation') or {}
    raw_receipts = rebase_audit.get('raw_input_provenance') or []
    anchored_inputs = (rebase_audit.get('accepted_input_manifest') or {}).get(
        'anchored_observation_inputs')
    if (baseline_graph_doc.get('source_rebase_report_sha256') != rebase_record['sha256']
            or rebase_report.get('next_state_sha256') != baseline_state.get('state_sha256')
            or rebase_report.get('zero_id_library_sha256') != lib_hash
            or rebase_report.get('identity_map_sha256') != accepted.get('identity_map_sha256')
            or rebase_audit.get('all_original_observation_hashes_verified') is not True
            or rebase_audit.get('all_retained_edges_same_pid_and_fresh_metadata_match') is not True
            or not isinstance(anchored_inputs, list)
            or len(raw_receipts) != rebase_audit.get('source_observation_count')):
        raise ValueError('genuine recommendation baseline rebase receipt is incomplete')
    baseline_provenance = accepted.get('baseline_rebase_provenance') or {}
    if (audit.get('rebase_provenance') != baseline_provenance
            or baseline_provenance.get('graph_export_sha256') != inputs['baseline_graph_export']['sha256']
            or baseline_provenance.get('rebase_report_sha256') != rebase_record['sha256']
            or baseline_provenance.get('state_file_sha256') != inputs['baseline_state']['sha256']
            or baseline_provenance.get('raw_observation_receipt_count') != len(raw_receipts)):
        raise ValueError('genuine recommendation merge receipt differs from the baseline rebase receipt')
    verified_rebase_receipts = set()
    for receipt in raw_receipts:
        receipt_path = Path(receipt['path']).resolve()
        if (not receipt_path.is_file()
                or _sha_bytes(receipt_path.read_bytes()) != receipt.get('sha256')):
            raise ValueError('genuine recommendation original observation receipt changed')
        verified_rebase_receipts.add((str(receipt_path), receipt['sha256']))
    for anchored in anchored_inputs:
        if (str(Path(anchored[0]).resolve()), anchored[1]) not in verified_rebase_receipts:
            raise ValueError('genuine recommendation anchored baseline input lacks a verified receipt')

    seed_path, seed_record = verified_input('seed_manifest')
    seed_manifest = _read(seed_path)
    _, video_evidence_record = verified_input('seed_video_evidence')
    if (seed_manifest.get('library_sha256') != lib_hash
            or seed_manifest.get('identity_map_sha256') != accepted.get('identity_map_sha256')
            or seed_manifest.get('seed_video_evidence_sha256') != video_evidence_record['sha256']
            or accepted.get('seed_manifest', {}).get('sha256') != seed_record['sha256']
            or accepted.get('seed_manifest', {}).get('seed_video_evidence_sha256') != video_evidence_record['sha256']
            or accepted.get('seed_manifest', {}).get('seed_count') != len(seed_manifest.get('seeds', []))):
        raise ValueError('genuine recommendation seed/video evidence hash mismatch')
    collector_path, collector_record = verified_input('collector_summary')
    collector = _read(collector_path)
    if (collector.get('account_authentication_verified') is not True
            or collector.get('library_sha256') != lib_hash
            or collector.get('status') not in ('completed', 'stopped')
            or accepted.get('collector_summary', {}).get('sha256') != collector_record['sha256']
            or accepted.get('collector_summary', {}).get('http_requests') != collector.get('http_requests')
            or accepted.get('collector_summary', {}).get('request_budget') != collector.get('request_budget')):
        raise ValueError('genuine recommendation collector receipt mismatch')

    snapshot_records = inputs.get('collector_snapshots') or []
    if snapshot_records != accepted.get('snapshot_inputs') or not snapshot_records:
        raise ValueError('genuine recommendation raw snapshot manifest mismatch')
    paths = []
    raw_observation_count = 0
    relation_counts = {'radio': 0, 'related': 0}
    for record in snapshot_records:
        path = Path(record['path']).resolve()
        raw = path.read_bytes()
        if _sha_bytes(raw) != record['sha256']:
            raise ValueError('genuine recommendation source snapshot hash mismatch')
        doc = json.loads(raw)
        if (doc.get('library_sha256') != lib_hash
                or doc.get('identity_map_sha256') != accepted.get('identity_map_sha256')):
            raise ValueError('genuine recommendation snapshot Library/map mismatch')
        for observation in doc.get('observations', []):
            relation = observation.get('relation')
            if observation.get('source') != 'ytmusic' or relation not in relation_counts:
                raise ValueError('genuine recommendation snapshot has unsupported source relation')
            relation_counts[relation] += 1
            raw_observation_count += 1
        paths.append(path)

    regenerated, skipped, regenerated_inputs = collect_graphs(paths, matcher, lib_hash)
    if skipped or regenerated_inputs != snapshot_records:
        raise ValueError('genuine recommendation snapshots did not rematch exactly')
    by_input = {row['input']['sha256']: row for row in regenerated}
    accepted_graphs = accepted.get('new_graphs') or []
    if len(by_input) != len(accepted_graphs):
        raise ValueError('genuine recommendation source graph count mismatch')
    for row in accepted_graphs:
        source = by_input.get(row.get('input', {}).get('sha256'))
        if (source is None or source['input'] != row['input']
                or source['root_pid'] != row['root_pid']
                or source['ordered_target_pids'] != row['ordered_target_pids']
                or len(source['observations']) != row.get('observation_count')
                or sorted({item['relation'] for item in source['observations']})
                != row.get('source_relations')):
            raise ValueError('genuine recommendation graph differs from raw observations')
    collected_roots = {row.get('persistent_id') for row in collector.get('results', [])
                       if isinstance(row, dict) and row.get('status') == 'collected'}
    if {row['root_pid'] for row in accepted_graphs} - collected_roots:
        raise ValueError('a rematched source graph root is missing a collected collector result')

    final_rows = accepted.get('accepted_roots') or []
    final_edges = _edge_set(final_rows)
    relation_rows = relations.get('relation_rows') or []
    if _edge_set(relation_rows) != final_edges:
        raise ValueError('genuine recommendation relation rows differ from accepted roots')
    reverse = {gid: pid for pid, gid in ids.items()}
    state_edges = set()
    for pid, gid in ids.items():
        _, target_ids = parse_similarities(state_relations[gid])
        state_edges.update((pid, reverse[target]) for target in target_ids)
    if state_edges != final_edges:
        raise ValueError('genuine recommendation graph differs from sealed output state')
    final_by_root = {row['root_pid']: row['ordered_target_pids'] for row in final_rows}
    baseline_by_root = {row['root_pid']: row['ordered_target_pids'] for row in baseline_graph_rows}
    expected_merged = {root: list(targets) for root, targets in baseline_by_root.items()}
    for graph in merge_graphs(regenerated):
        targets = expected_merged.setdefault(graph['root_pid'], [])
        seen = set(targets)
        for target in graph['ordered_target_pids']:
            if target not in seen:
                targets.append(target)
                seen.add(target)
    if expected_merged != final_by_root:
        raise ValueError('final recommendation graph omits or changes rematched source edges')
    appended_edges = sum(len(final_by_root[root]) - len(targets)
                         for root, targets in baseline_by_root.items())
    appended_edges += sum(len(targets) for root, targets in final_by_root.items()
                          if root not in baseline_by_root)
    if any(final_by_root[root][:len(targets)] != targets
           for root, targets in baseline_by_root.items()):
        raise ValueError('genuine recommendation baseline edge prefix changed')
    new_roots = [row for row in final_rows if row['root_pid'] not in baseline_by_root]
    roots_with_appended = sum(final_by_root[root][len(targets):] != []
                              for root, targets in baseline_by_root.items())
    touched = {pid for row in accepted_graphs
               for pid in (row['root_pid'], *row['ordered_target_pids'])}
    new_ids = set(ids) - old_ids
    active_roots = sum(bool(row['ordered_target_pids']) for row in final_rows)
    measures = {
        'baseline_seed_roots': len(baseline_graph_rows),
        'baseline_active_outgoing_roots': sum(bool(row['ordered_target_pids']) for row in baseline_graph_rows),
        'baseline_directed_edges': len(baseline_edges),
        'collector_snapshots_hash_validated': len(snapshot_records),
        'collector_status': collector.get('status'),
        'collector_http_requests': collector.get('http_requests'),
        'collector_request_budget': collector.get('request_budget'),
        'collector_seed_count': len(seed_manifest.get('seeds', [])),
        'new_graph_roots_reported_collected': len(accepted_graphs),
        'baseline_raw_observation_receipts_validated': len(verified_rebase_receipts),
        'baseline_anchored_observations': len(anchored_inputs),
        'original_library_hash_verified_this_run': audit.get('library_input', {}).get(
            'original_library_hash_verified_this_run'),
        'collector_graph_roots_rematched': len(regenerated),
        'new_outgoing_roots': len(new_roots),
        'newly_collected_graph_roots': len({row['root_pid'] for row in accepted_graphs}),
        'newly_active_outgoing_roots': sum(bool(row['ordered_target_pids']) for row in new_roots),
        'output_active_outgoing_roots': active_roots,
        'roots_with_appended_recommendation_edges': roots_with_appended,
        'new_recommendation_edges_after_dedup': len(final_edges - baseline_edges),
        'recommendation_tracks_touched_in_new_snapshots': len(touched),
        'newly_indexed_tracks': len(new_ids),
        'indexed_tracks': len(ids),
        'accepted_roots': len(final_rows),
        'accepted_directed_edges': len(final_edges),
        'radio_observation_rows': relation_counts['radio'],
        'related_observation_rows': relation_counts['related'],
        'raw_observation_rows': raw_observation_count,
        'catalog_edges_added': 0,
        'local_album_edges_added': 0,
        'all_growth_source_hashes_validated': True,
        'all_new_graphs_recomputed_from_snapshots': True,
        'sealed_output_state_matches_graph': True,
    }
    expected_fields = {
        'baseline_root_count': measures['baseline_seed_roots'],
        'baseline_active_outgoing_root_count': measures['baseline_active_outgoing_roots'],
        'baseline_directed_edge_count': measures['baseline_directed_edges'],
        'accepted_collector_snapshot_count': measures['collector_snapshots_hash_validated'],
        'collector_graph_root_count': measures['collector_graph_roots_rematched'],
        'new_outgoing_root_count': measures['new_outgoing_roots'],
        'newly_collected_graph_root_count': measures['newly_collected_graph_roots'],
        'newly_active_outgoing_root_count': measures['newly_active_outgoing_roots'],
        'output_active_outgoing_root_count': measures['output_active_outgoing_roots'],
        'roots_with_appended_recommendation_edges': measures['roots_with_appended_recommendation_edges'],
        'appended_recommendation_edge_count': appended_edges,
        'new_outgoing_edge_count': appended_edges,
        'recommendation_tracks_touched': measures['recommendation_tracks_touched_in_new_snapshots'],
        'newly_indexed_track_count': measures['newly_indexed_tracks'],
        'newly_indexed_pids': sorted(new_ids),
        'indexed_track_count': measures['indexed_tracks'],
        'accepted_root_count': measures['accepted_roots'],
        'accepted_edge_count': measures['accepted_directed_edges'],
        'baseline_edge_prefix_preserved': True,
        'catalog_edges_added': 0,
        'local_album_edges_added': 0,
        'unattributed_edges_added': 0,
    }
    if any(audit.get(key) != value for key, value in expected_fields.items()):
        raise ValueError('genuine recommendation merge audit counters differ from recomputed values')
    return measures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph-dir', required=True, type=Path)
    parser.add_argument('--baseline-graphs', required=True, type=Path)
    parser.add_argument('--baseline-state', required=True, type=Path)
    parser.add_argument('--track-snapshot', required=True, type=Path)
    parser.add_argument('--genuine-radio-growth', type=Path,
                        help='also independently audit a radio/related growth merge directory')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = build_report(args.graph_dir, args.baseline_graphs,
                          args.baseline_state, args.track_snapshot)
    if args.genuine_radio_growth:
        report['genuine_recommendation_growth'] = audit_genuine_growth(
            args.genuine_radio_growth, args.baseline_state, args.track_snapshot)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(rendered, encoding='utf-8')
    else:
        print(rendered, end='')


if __name__ == '__main__':
    main()
