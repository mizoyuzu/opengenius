"""Audit saved personal recommendations without inferring music quality.

The report measures Library coverage, graph participation, exposure, path and
metadata classification. Explicit feedback stays contextual to its saved
seed-target pair; unrated recommendations are never treated as positive or
negative evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from audit_recommendation_paths import audit_paths
from music_identity_map import IdentityMap, load_library, version_facets, version_signature
from taste_feedback import compare_feedback, summarize_feedback, validate_feedback


def _evaluation(document):
    """Accept a direct evaluation or a common-space comparison report."""
    if not isinstance(document, dict):
        raise ValueError('Evaluation must be an object')
    if isinstance(document.get('root_results'), list):
        return document
    comparison = document.get('graph_comparison')
    if isinstance(comparison, dict):
        expanded = comparison.get('expanded')
        if isinstance(expanded, dict) and isinstance(expanded.get('root_results'), list):
            # Common-space child evaluations omit some top-level provenance.
            # Inherit only absent fields so a conflicting child Library hash
            # remains visible and is rejected by the caller's binding check.
            inherited = dict(expanded)
            for key in ('library_sha256', 'library_input', 'input_sha256',
                        'input_provenance', 'observation_inputs'):
                if key not in inherited and key in document:
                    inherited[key] = document[key]
            return inherited
    raise ValueError('Evaluation must contain root_results')


def _counts_by_kind(pids, classifications):
    kinds = Counter(classifications[pid]['kind'] for pid in pids)
    return {kind: kinds[kind] for kind in ('vocal', 'bgm', 'off_vocal', 'unknown')}


def _graph_nodes(evaluation, tracks):
    rows = evaluation.get('relation_rows')
    if not isinstance(rows, list):
        raise ValueError('Evaluation must contain relation_rows')
    edges, roots, candidates = {}, set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid relation row')
        root, targets = row.get('root_pid'), row.get('ordered_target_pids')
        if not isinstance(root, str) or root not in tracks or root in edges:
            raise ValueError('Invalid or duplicate relation root PID')
        if not isinstance(targets, list) or any(not isinstance(pid, str) or pid not in tracks for pid in targets):
            raise ValueError('Invalid relation target PID')
        if len(set(targets)) != len(targets) or root in targets:
            raise ValueError('Duplicate or self-referential relation target')
        roots.add(root)
        candidates.update(targets)
        edges[root] = targets
    reachable = set()
    for root in roots:
        pending, visited = list(edges[root]), {root}
        while pending:
            pid = pending.pop()
            if pid in visited:
                continue
            visited.add(pid)
            reachable.add(pid)
            pending.extend(edges.get(pid, ()))
    return roots, candidates, reachable, edges


def _root_rows(evaluation, tracks):
    rows = evaluation.get('root_results')
    if not isinstance(rows, list):
        raise ValueError('Evaluation must contain root_results')
    results, seen = [], set()
    for row in rows:
        root = row.get('root_pid') if isinstance(row, dict) else None
        if not isinstance(root, str) or root not in tracks or root in seen:
            raise ValueError('Invalid or duplicate evaluated root PID')
        seen.add(root)
        playlist = row.get('playlist')
        if not isinstance(playlist, list) or any(not isinstance(item, dict) for item in playlist):
            raise ValueError('Invalid evaluated playlist entries')
        pids = [item.get('persistent_id') for item in playlist]
        if any(not isinstance(pid, str) or pid not in tracks for pid in pids):
            raise ValueError('Invalid evaluated playlist PID')
        if row.get('playlist_pids', pids) != pids:
            raise ValueError('Playlist representations disagree')
        results.append((root, pids))
    return results


def _exposure(rows, tracks, matcher):
    pid_counts, artist_counts = Counter(), Counter()
    artist_display = {}
    total = known_artist_slots = 0
    missing_artist_slots = 0
    for root, pids in rows:
        for pid in pids:
            if pid == root:
                continue
            total += 1
            pid_counts[pid] += 1
            raw = tracks[pid].get('artist') or ''
            if not raw.strip():
                missing_artist_slots += 1
                continue
            credits = matcher.artist_credits(raw)
            if not credits:
                missing_artist_slots += 1
                continue
            known_artist_slots += 1
            key = tuple(sorted(credits))
            artist_counts[key] += 1
            artist_display.setdefault(key, raw)
    top_pids = [dict(persistent_id=pid, title=tracks[pid].get('title'),
                     artist=tracks[pid].get('artist'), count=count,
                     denominator=total, share=count / total if total else None)
                for pid, count in sorted(pid_counts.items(), key=lambda item: (-item[1], item[0]))[:25]]
    top_artists = [dict(canonical_artist_credits=list(key), display=artist_display[key],
                        count=count, denominator=known_artist_slots,
                        share=count / known_artist_slots if known_artist_slots else None)
                   for key, count in sorted(artist_counts.items(), key=lambda item: (-item[1], item[0]))[:25]]
    return dict(nonroot_exposure_slots=total, unique_nonroot_pids=len(pid_counts),
                missing_artist_exposure_slots=missing_artist_slots,
                known_artist_exposure_slots=known_artist_slots,
                top_nonroot_pids=top_pids, top_artist_credits=top_artists)


def _version_counts(pids, tracks):
    marker_slots, year_slots, unknown_qualifier_slots = Counter(), Counter(), Counter()
    marker_tracks, year_tracks, unknown_qualifier_tracks = Counter(), Counter(), Counter()
    for pid in pids:
        title = tracks[pid].get('title') or ''
        if callable(version_facets):
            facets = version_facets(title)
            markers, years = facets['markers'], facets['years']
            unknown = facets['unknown_qualifiers']
        else:
            signature = version_signature(title)
            markers = sorted(value for value in signature if not value.startswith('year:'))
            years = sorted(value[5:] for value in signature if value.startswith('year:'))
            unknown = []
        marker_slots.update(markers)
        year_slots.update(years)
        unknown_qualifier_slots.update(unknown)
    for pid in set(pids):
        title = tracks[pid].get('title') or ''
        if callable(version_facets):
            facets = version_facets(title)
            markers, years = facets['markers'], facets['years']
            unknown = facets['unknown_qualifiers']
        else:
            signature = version_signature(title)
            markers = sorted(value for value in signature if not value.startswith('year:'))
            years = sorted(value[5:] for value in signature if value.startswith('year:'))
            unknown = []
        marker_tracks.update(set(markers))
        year_tracks.update(set(years))
        unknown_qualifier_tracks.update(set(unknown))
    return dict(marker_exposure_slots=dict(sorted(marker_slots.items())),
                marker_unique_tracks=dict(sorted(marker_tracks.items())),
                year_exposure_slots=dict(sorted(year_slots.items())),
                year_unique_tracks=dict(sorted(year_tracks.items())),
                unknown_qualifier_exposure_slots=dict(sorted(unknown_qualifier_slots.items())),
                unknown_qualifier_unique_tracks=dict(sorted(unknown_qualifier_tracks.items())))


def _path_summary(path_report, roots=None):
    rows = [row for row in path_report['root_results'] if roots is None or row['root_pid'] in roots]
    hops, gateways = Counter(), Counter()
    playlist_size = missing_paths = verified_paths = 0
    per_root = []
    for row in rows:
        for hop, count in row['hop_distribution'].items():
            hops[str(hop)] += count
        missing_paths += row['missing_path_count']
        verified_paths += row['verified_graph_path_count']
        playlist_size += row['playlist_size']
        for gateway in row['gateways']:
            gateways[gateway['persistent_id']] += gateway['nonroot_paths']
        per_root.append(dict(root_pid=row['root_pid'], title=row['title'], artist=row['artist'],
                             playlist_size=row['playlist_size'], nonroot_count=row['nonroot_count'],
                             verified_graph_path_count=row['verified_graph_path_count'],
                             missing_path_count=row['missing_path_count'],
                             hop_distribution=row['hop_distribution'],
                             largest_gateway_verified_nonroot_share=row['largest_gateway_verified_nonroot_share'],
                             largest_known_nonroot_artist_credit_share=row['largest_known_nonroot_artist_credit_share']))
    return dict(playlist_slots=playlist_size, verified_path_count=verified_paths,
                missing_path_count=missing_paths, hop_distribution=dict(sorted(hops.items(), key=lambda item: int(item[0]))),
                top_gateways=[dict(persistent_id=pid, path_count=count) for pid, count in
                              sorted(gateways.items(), key=lambda item: (-item[1], item[0]))[:25]],
                root_concentration=per_root,
                interpretation='Saved shortest paths are structural descriptions; alternative observed paths may exist.')


def audit_personal_recommendations(evaluation, tracks, matcher, library_hash, *,
                                   cluster_config=None, feedback=None, comparison=None):
    """Produce structural, exposure, explicit-classification and feedback metrics."""
    if not isinstance(evaluation, dict) or evaluation.get('library_sha256') != library_hash:
        raise ValueError('Evaluation belongs to a different Library')
    track_map = {track['persistent_id']: track for track in tracks}
    roots, candidate_pids, reachable, _ = _graph_nodes(evaluation, track_map)
    evaluated_rows = _root_rows(evaluation, track_map)
    path_report = audit_paths(evaluation, matcher)
    evaluated_roots = {root for root, _ in evaluated_rows}
    evaluated_pids = {pid for _, pids in evaluated_rows for pid in pids}
    participating = roots | candidate_pids
    report = {
        'schema_version': 1,
        'library_sha256': library_hash,
        'identity_status': 'metadata_match_unverified_recording',
        'verified_recordings': 0,
        'music_app_acceptance_verified': False,
        'ipod_acceptance_verified': False,
        'network_requests': 0,
        'structural_coverage': {
            'library_track_count': len(tracks),
            'observed_root_count': len(roots),
            'evaluated_root_count': len(evaluated_roots),
            'root_coverage': len(roots) / len(tracks) if tracks else None,
            'evaluated_root_coverage': len(evaluated_roots) / len(tracks) if tracks else None,
            'distinct_observed_candidate_count': len(candidate_pids),
            'candidate_library_coverage': len(candidate_pids) / len(tracks) if tracks else None,
            'distinct_reachable_node_count': len(reachable),
            'reachable_library_coverage': len(reachable) / len(tracks) if tracks else None,
            'participating_node_count': len(participating),
            'participating_node_coverage': len(participating) / len(tracks) if tracks else None,
            'evaluated_track_count': len(evaluated_pids),
            'evaluated_track_coverage': len(evaluated_pids) / len(tracks) if tracks else None,
            'unseen_tracks_interpretation': 'No observed participation does not mean unrelated or disliked.',
        },
        'exposure': _exposure(evaluated_rows, track_map, matcher),
        'version_facets': _version_counts([pid for _, pids in evaluated_rows for pid in pids], track_map),
        'paths': _path_summary(path_report),
        'quality_winner': None,
        'quality_interpretation': 'This report measures structure and explicit feedback, not recommendation quality.',
    }
    if cluster_config is not None:
        from music_clusters import classify_tracks
        classifications = classify_tracks(cluster_config, tracks, library_hash)
        report['classification'] = {
            'source': 'explicit cluster configuration plus safe title markers',
            'participating_tracks': dict(track_count=len(participating), kind_counts=_counts_by_kind(participating, classifications)),
            'evaluated_tracks': dict(track_count=len(evaluated_pids), kind_counts=_counts_by_kind(evaluated_pids, classifications)),
            'evaluated_exposure_slots': dict(
                slot_count=sum(len(pids) for _, pids in evaluated_rows),
                kind_counts=dict(Counter(classifications[pid]['kind'] for _, pids in evaluated_rows for pid in pids))),
            'recording_identity_inferred': False,
        }
    if feedback is not None:
        report['feedback'] = summarize_feedback(evaluation, feedback, tracks, library_hash)
        report['feedback']['reason_counts_are_contextual'] = True
        report['feedback']['all_pair_reason_counts_include_stale_contexts'] = True
    else:
        report['feedback'] = {'available': False, 'interpretation': 'No explicit feedback supplied; no ratings inferred.'}
    if comparison is not None:
        if comparison.get('library_sha256') != library_hash:
            raise ValueError('Comparison belongs to a different Library')
        comparison_rows = _root_rows(comparison, track_map)
        comparison_paths = audit_paths(comparison, matcher)
        common_roots = sorted(evaluated_roots & {root for root, _ in comparison_rows})
        common_eval = dict(evaluation, root_results=[row for row in evaluation['root_results'] if row['root_pid'] in common_roots])
        common_comparison = dict(comparison, root_results=[row for row in comparison['root_results'] if row['root_pid'] in common_roots])
        base = dict(common_roots=common_roots,
                    same_library=True,
                    baseline_path_summary=_path_summary(path_report, set(common_roots)),
                    comparison_path_summary=_path_summary(comparison_paths, set(common_roots)),
                    baseline_exposure=_exposure([row for row in evaluated_rows if row[0] in common_roots], track_map, matcher),
                    comparison_exposure=_exposure([row for row in comparison_rows if row[0] in common_roots], track_map, matcher),
                    winner=None,
                    interpretation='Descriptive paired comparison on common roots; no quality winner.')
        if feedback is not None:
            base['feedback_comparison'] = compare_feedback(common_eval, common_comparison, feedback, tracks, library_hash)
        report['comparison'] = base
    return report


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


def _read(path):
    return json.loads(path.read_bytes(), object_pairs_hook=_unique_keys)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('evaluation', 'track-snapshot', 'identity-map', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--cluster-config', type=Path)
    parser.add_argument('--feedback', type=Path)
    parser.add_argument('--comparison', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    sources = [path.resolve() for path in (args.evaluation, args.track_snapshot, args.identity_map,
                                            args.cluster_config, args.feedback, args.comparison) if path]
    if output.exists() or output in sources:
        parser.error('Output must be new and separate from input files')
    tracks, library_hash, provenance = load_library(track_snapshot=args.track_snapshot)
    identity_doc = _read(args.identity_map)
    matcher = IdentityMap(identity_doc, tracks, library_hash)
    evaluation_doc = _read(args.evaluation)
    evaluation = _evaluation(evaluation_doc)
    cluster_config = _read(args.cluster_config) if args.cluster_config else None
    feedback = _read(args.feedback) if args.feedback else None
    if feedback is not None:
        validate_feedback(feedback, tracks, library_hash)
    comparison = _evaluation(_read(args.comparison)) if args.comparison else None
    report = audit_personal_recommendations(evaluation, tracks, matcher, library_hash,
                                            cluster_config=cluster_config, feedback=feedback,
                                            comparison=comparison)
    input_paths = dict(evaluation=args.evaluation, track_snapshot=args.track_snapshot, identity_map=args.identity_map)
    for name in ('cluster_config', 'feedback', 'comparison'):
        path = getattr(args, name)
        if path:
            input_paths[name] = path
    report.update(library_input=provenance,
                  inputs={name: {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                          for name, path in input_paths.items()},
                  audit_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'output': str(output), 'roots': report['structural_coverage']['evaluated_root_count'],
                      'network_requests': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
