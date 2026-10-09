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
        _verify_identity_map_rebinding(graph_doc, report, state, library_hash,
                                       identity_hash, Path(state_path).resolve())
    return graph_doc, report, state, {
        'graph_export_path': str(Path(graph_path).resolve()),
        'graph_export_sha256': sha(graph_bytes),
        'rebase_report_path': str(Path(report_path).resolve()),
        'rebase_report_sha256': sha(report_bytes),
        'state_path': str(Path(state_path).resolve()),
        'state_file_sha256': sha(state_bytes),
        'raw_observation_receipt_count': len(raw_inputs),
        'anchored_observation_count': len(manifest['anchored_observation_inputs']),
        'accepted_input_manifest_sha256': sha(json.dumps(
            manifest, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode('utf-8')),
    }


def _verify_identity_map_rebinding(graph_doc, report, state, library_hash,
                                   identity_hash, rebound_state_path):
    """Replay old-map growth and prove a metadata-only map transition."""
    from plan_genius_update import _hash as state_hash
    from music_identity_map import IdentityMap

    binding = report.get('identity_map_rebinding')
    if (not isinstance(binding, dict)
            or binding.get('schema_version') != 1
            or binding.get('new_identity_map_sha256') != identity_hash
            or binding.get('library_sha256') != library_hash
            or any(binding.get(key) is not True for key in (
                'local_track_metadata_unchanged', 'local_equivalence_labels_unchanged',
                'pid_gid_assignments_unchanged', 'metadata_bytes_unchanged',
                'relation_bytes_unchanged', 'old_growth_replayed_with_old_map'))):
        raise ValueError('Typed identity-map rebinding proof is incomplete')
    def checked_file(record, description):
        if not isinstance(record, dict) or not isinstance(record.get('path'), str) or not isinstance(record.get('sha256'), str):
            raise ValueError('Malformed ' + description + ' receipt in identity-map rebinding')
        path = _resolve_input(record['path'])
        data = path.read_bytes()
        if sha(data) != record['sha256']:
            raise ValueError(description + ' changed after identity-map rebinding')
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
    if old_map != new_map or new_aliases[:len(old_aliases)] != old_aliases:
        raise ValueError('Identity-map rebind changed fields other than append-only credit aliases')
    if len(new_aliases) <= len(old_aliases):
        raise ValueError('Identity-map rebind did not add any reviewed credit aliases')

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
    base_roots = base_graph.get('accepted_roots')
    if _relation_map(base_roots, 'Rebinding source baseline') != _relation_map(
            _state_graph_rows(base_state), 'Rebinding source state'):
        raise ValueError('Rebinding source baseline graph differs from its state')
    source_state, source_roots = base_state, base_roots
    growth = binding.get('source_growth')
    if growth is not None:
        if not isinstance(growth, dict) or not isinstance(growth.get('path'), str):
            raise ValueError('Malformed old-map growth receipt in identity-map rebinding')
        growth_dir = _resolve_input(growth['path'])
        accepted_path = growth_dir / 'accepted-graphs.json'
        accepted_bytes = accepted_path.read_bytes()
        if sha(accepted_bytes) != growth.get('accepted_graphs_sha256'):
            raise ValueError('Old-map growth receipt changed after identity-map rebinding')
        source_state, source_roots, _, accepted_hash = _replay_previous_growth(
            growth_dir, base_state, base_roots, base_provenance, old_matcher,
            library_hash, binding['old_identity_map_sha256'])
        if accepted_hash != growth.get('accepted_graphs_sha256'):
            raise ValueError('Old-map growth replay used a different receipt')
    if source_state['state_sha256'] != binding.get('old_state_sha256'):
        raise ValueError('Old-map state does not match the replayed state in rebinding receipt')
    graph_roots = _relation_map(graph_doc.get('accepted_roots'), 'Rebound accepted roots')
    replay_roots = _relation_map(source_roots, 'Replayed old-map roots')
    source_state_roots = _relation_map(_state_graph_rows(source_state), 'Replayed old-map state roots')
    if (graph_roots != source_state_roots
            or {pid: edges for pid, edges in graph_roots.items() if edges}
               != {pid: edges for pid, edges in replay_roots.items() if edges}):
        raise ValueError('Rebound graph differs from old-map graph replay')
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


def _validate_run_receipt(run, tracks, matcher, library_hash, identity_hash):
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
        for seed in manifest['seeds']:
            hint = hints.get(seed['persistent_id'])
            if (hint is None or hint.get('video_id') != seed.get('seed_video_id')
                    or hint.get('source') != seed.get('seed_video_source')):
                raise ValueError('A previous seed differs from its strict saved-video evidence')
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
    graphs, skipped, inputs = collect_graphs(paths, matcher, library_hash)
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
    if evidence_info is not None:
        normalized_run['seed_manifest']['seed_video_evidence'] = {
            'path': str(evidence_path), 'sha256': evidence_info['sha256']}
    if run.get('resulting_state_sha256'):
        normalized_run['resulting_state_sha256'] = run['resulting_state_sha256']
    return incoming_roots, normalized_run


def _replay_previous_growth(previous_dir, baseline_state, baseline_roots,
                            baseline_provenance, matcher, library_hash, identity_hash):
    previous_dir = Path(previous_dir).resolve()
    accepted_path = previous_dir / 'accepted-graphs.json'
    accepted_bytes = accepted_path.read_bytes()
    accepted = json.loads(accepted_bytes)
    if (accepted.get('producer') != 'opengenius-ytmusic-recommendation-growth-rebased-merge-v1'
            or accepted.get('library_sha256') != library_hash
            or accepted.get('identity_map_sha256') != identity_hash
            or accepted.get('baseline_state_sha256') != baseline_state.get('state_sha256')
            or accepted.get('baseline_rebase_provenance') != baseline_provenance
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
    state = baseline_state
    roots = baseline_roots
    normalized_runs = []
    for run in runs:
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


def merge_radio_growth(*, track_snapshot, identity_map_path, baseline_state_path,
                       baseline_graph_export_path, rebase_report_path,
                       collector_summary_path, seed_manifest_path,
                       snapshot_paths, output_dir, seed_video_evidence_path=None,
                       previous_growth_dir=None):
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError('Output directory exists; choose a new directory')
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
        for seed in seed_manifest['seeds']:
            hint = selected_hints.get(seed.get('persistent_id'))
            if (hint is None or hint.get('video_id') != seed.get('seed_video_id')
                    or hint.get('source') != seed.get('seed_video_source')):
                raise ValueError('Collector seed differs from the strict saved-video evidence')
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
    graphs, skipped, inputs = collect_graphs(paths, matcher, library_hash)
    if not graphs:
        raise ValueError('No collector snapshots rematched as valid YT Music graphs')
    if any(graph['root_pid'] not in collected for graph in graphs):
        raise ValueError('A graph root is absent from authenticated collected seed results')
    new_roots = merge_graphs(graphs)
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
            'skipped_snapshots': skipped,
            'new_graphs': [{'root_pid': row['root_pid'],
                            'ordered_target_pids': row['ordered_target_pids']}
                           for row in new_roots],
            'resulting_state_sha256': next_state['state_sha256'],
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
            previous_growth_dir=args.previous_growth)
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
