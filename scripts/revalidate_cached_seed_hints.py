"""Revalidate old search misses against a reviewed matcher using cached responses.

This creates seed-video metadata hints only. It does not create observations,
recommendation edges, or outgoing roots. The prior identity-map hash and every
historical/cache file hash remain explicit in the output.
"""
import argparse
import hashlib
import json
from pathlib import Path

from music_identity_map import IdentityMap, load_library
from seed_video_evidence import strict_cached_search_match, verify_typed_map_transition


PRODUCER = 'opengenius-cached-seed-hint-revalidation-v1'


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _receipt(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': _sha(path.read_bytes())}


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _search_cache_path(collector_dir, arguments):
    encoded = json.dumps(['search', arguments], sort_keys=True,
                         ensure_ascii=False).encode('utf-8')
    key = hashlib.sha256(encoded).hexdigest()
    return Path(collector_dir).resolve() / 'requests' / (key + '.json')


def revalidate_cached_seed_hints(*, track_snapshot, old_identity_map,
                                 current_identity_map, collector_dirs,
                                 typed_transition):
    """Return current-map hints that resolve prior strict search misses.

    Only exact cached request envelopes can be used. For each historical miss,
    the manifest and summary must bind the same Library and old map. The raw
    response is then passed through the collector's current strict seed search
    resolver under both maps. A row is emitted only if the old resolver failed
    and the current resolver selects one video ID.
    """
    tracks, library_hash, library_input = load_library(
        track_snapshot=Path(track_snapshot))
    old_bytes = Path(old_identity_map).read_bytes()
    current_bytes = Path(current_identity_map).read_bytes()
    old_hash, current_hash = _sha(old_bytes), _sha(current_bytes)
    old_matcher = IdentityMap(json.loads(old_bytes), tracks, library_hash)
    current_matcher = IdentityMap(json.loads(current_bytes), tracks, library_hash)
    transition_receipts, verified_transition = verify_typed_map_transition(
        typed_transition, old_hash, current_hash, library_hash)
    seen = {}
    ambiguous_pids, ambiguous_videos = set(), set()
    skipped = []
    source_runs = []
    local_tracks = {track['persistent_id']: track for track in tracks}

    for raw_dir in collector_dirs:
        collector_dir = Path(raw_dir).resolve()
        manifest_path = collector_dir / 'seeds.json'
        try:
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            skipped.append({'collector_dir': str(collector_dir),
                            'reason': 'missing_or_invalid_seed_manifest'})
            continue
        if (manifest.get('library_sha256') != library_hash
                or manifest.get('identity_map_sha256') != old_hash
                or not isinstance(manifest.get('seeds'), list)):
            skipped.append({'collector_dir': str(collector_dir),
                            'reason': 'manifest_library_or_old_map_mismatch'})
            continue
        seeds = {}
        malformed = False
        for seed in manifest['seeds']:
            pid = seed.get('persistent_id') if isinstance(seed, dict) else None
            if not isinstance(pid, str) or pid not in {t['persistent_id'] for t in tracks} or pid in seeds:
                malformed = True
                break
            seeds[pid] = seed
        if malformed:
            skipped.append({'collector_dir': str(collector_dir),
                            'reason': 'malformed_or_duplicate_manifest_pids'})
            continue
        manifest_matches_library = True
        for pid, seed in seeds.items():
            local = local_tracks[pid]
            if any(seed.get(field) != local.get(field)
                   for field in ('title', 'artist', 'album', 'duration_ms')):
                manifest_matches_library = False
                break
        if not manifest_matches_library:
            skipped.append({'collector_dir': str(collector_dir),
                            'reason': 'manifest_seed_metadata_differs_from_library'})
            continue

        summary_paths = sorted(collector_dir.glob('summary*.json'))
        for summary_path in summary_paths:
            try:
                summary_bytes = summary_path.read_bytes()
                summary = json.loads(summary_bytes)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                skipped.append({'path': str(summary_path), 'reason': 'invalid_summary'})
                continue
            if (summary.get('library_sha256') != library_hash
                    or summary.get('account_authentication_verified') is not True
                    or summary.get('status') not in ('completed', 'stopped')
                    or type(summary.get('http_requests')) is not int
                    or type(summary.get('request_budget')) is not int
                    or not 0 < summary['http_requests'] <= summary['request_budget']
                    or not isinstance(summary.get('results'), list)):
                skipped.append({'path': str(summary_path),
                                'reason': 'summary_library_or_authentication_mismatch'})
                continue
            summary_receipt = {'path': str(summary_path), 'sha256': _sha(summary_bytes)}
            manifest_receipt = {'path': str(manifest_path),
                                'sha256': _sha(manifest_bytes)}
            source_runs.append({'collector_dir': str(collector_dir),
                                'summary': summary_receipt,
                                'seed_manifest': manifest_receipt,
                                'old_identity_map_sha256': old_hash})
            result_pids = [result.get('persistent_id') for result in summary['results']
                           if isinstance(result, dict)]
            if (len(result_pids) != len(summary['results'])
                    or len(set(result_pids)) != len(result_pids)
                    or any(pid not in seeds for pid in result_pids)):
                skipped.append({'path': str(summary_path),
                                'reason': 'summary_results_duplicate_or_unbound_pids'})
                continue
            for result in summary['results']:
                if not isinstance(result, dict) or result.get('status') != 'search_unresolved':
                    continue
                pid = result.get('persistent_id')
                seed = seeds.get(pid)
                if (seed is None or seed.get('seed_video_id')
                        or type(result.get('candidate_video_ids')) is not int
                        or result['candidate_video_ids'] != 0):
                    skipped.append({'summary': str(summary_path), 'persistent_id': pid,
                                    'reason': 'unbound_or_not_strictly_unresolved_result'})
                    continue
                query = str(seed.get('title') or '') + ' ' + str(seed.get('artist') or '')
                arguments = {'query': query, 'filter': 'songs', 'limit': 5}
                candidate_requests = [arguments]
                fallback_history_status = 'not_attempted'
                fallback_args = {'query': str(seed.get('title') or ''),
                                 'filter': 'songs', 'limit': 5}
                if result.get('title_fallback_attempted') is True:
                    if (type(result.get('title_fallback_candidate_video_ids')) is not int
                            or result['title_fallback_candidate_video_ids'] != 0
                            or not seed.get('title') or seed['title'] == query):
                        skipped.append({'summary': str(summary_path), 'persistent_id': pid,
                                        'reason': 'fallback_history_not_strictly_unresolved'})
                        continue
                    candidate_requests.append(fallback_args)
                    fallback_history_status = 'attempted_and_zero_candidates'
                elif result.get('title_fallback_attempted') is not False:
                    if (not seed.get('title') or seed['title'] == query
                            or _search_cache_path(collector_dir, fallback_args).exists()):
                        skipped.append({'summary': str(summary_path), 'persistent_id': pid,
                                        'reason': 'fallback_history_flag_missing_or_cached_fallback_exists'})
                        continue
                    fallback_history_status = 'legacy_summary_omits_flag_no_fallback_cache'
                elif (seed.get('title') and seed['title'] != query
                      and _search_cache_path(collector_dir, fallback_args).exists()):
                    skipped.append({'summary': str(summary_path), 'persistent_id': pid,
                                    'reason': 'fallback_flag_conflicts_with_cached_request'})
                    continue
                found_video, found_args, found_path, found_doc = None, None, None, None
                old_outcome = None
                requests_complete = True
                for request_args in candidate_requests:
                    response_path = _search_cache_path(collector_dir, request_args)
                    try:
                        response_bytes = response_path.read_bytes()
                        response_doc = json.loads(response_bytes)
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        requests_complete = False
                        continue
                    if (not isinstance(response_doc, dict)
                            or set(response_doc) != {'endpoint', 'arguments', 'observed_at', 'response'}
                            or response_doc.get('endpoint') != 'search'
                            or response_doc.get('arguments') != request_args
                            or not isinstance(response_doc.get('response'), list)
                            or not isinstance(response_doc.get('observed_at'), str)):
                        skipped.append({'summary': str(summary_path), 'persistent_id': pid,
                                        'response': str(response_path),
                                        'reason': 'cached_search_envelope_mismatch'})
                        continue
                    old_result = strict_cached_search_match(response_doc['response'], seed,
                                                            old_matcher)
                    current_result = strict_cached_search_match(response_doc['response'], seed,
                                                                current_matcher)
                    if old_result is not None:
                        old_outcome = old_result
                    if current_result is not None:
                        if found_video is not None and found_video != current_result['video_id']:
                            found_video = None
                            found_args = None
                            found_path = None
                            found_doc = None
                            skipped.append({'summary': str(summary_path), 'persistent_id': pid,
                                            'reason': 'conflicting_cached_video_ids'})
                            break
                        found_video = current_result['video_id']
                        found_args = request_args
                        found_path = response_path
                        found_doc = (response_doc, response_bytes, current_result)
                if not requests_complete:
                    skipped.append({'summary': str(summary_path), 'persistent_id': pid,
                                    'reason': 'incomplete_cached_query_history'})
                    continue
                if old_outcome is not None:
                    skipped.append({'summary': str(summary_path), 'persistent_id': pid,
                                    'reason': 'already_resolvable_under_old_map'})
                    continue
                if found_video is None:
                    skipped.append({'summary': str(summary_path), 'persistent_id': pid,
                                    'reason': 'no_current_map_match_in_cached_search'})
                    continue
                response_doc, response_bytes, current_result = found_doc
                row = {
                    'persistent_id': pid,
                    'video_id': found_video,
                    'source_relation': 'cached_search_candidate_unverified',
                    'identity_status': 'unverified_metadata_candidate',
                    'input_path': str(found_path),
                    'input_sha256': _sha(response_bytes),
                    'request_arguments': found_args,
                    'observed_at': response_doc['observed_at'],
                    'selected_result_positions': current_result['result_positions'],
                    'strict_match_evidence': current_result['matches'],
                    'historical_summary': summary_receipt,
                    'historical_seed_manifest': manifest_receipt,
                    'historical_result_status': 'search_unresolved',
                    'historical_fallback_status': fallback_history_status,
                    'historical_old_identity_map_sha256': old_hash,
                    'revalidated_identity_map_sha256': current_hash,
                    'typed_transition_report_sha256': transition_receipts['rebase_report']['sha256'],
                    'resolver': {'producer': 'seed_video_evidence.strict_cached_search_match',
                                 'status': 'unique_complete_credit_title_version_duration_album_match',
                                 'candidate_video_id_count': 1},
                }
                if pid in ambiguous_pids or row['video_id'] in ambiguous_videos:
                    skipped.append({'summary': str(summary_path), 'persistent_id': pid,
                                    'reason': 'conflicting_revalidation_sources'})
                    continue
                old = seen.get(pid)
                other_pids = {known_pid for known_pid, known in seen.items()
                              if known['video_id'] == row['video_id'] and known_pid != pid}
                if old is not None and old['video_id'] != row['video_id']:
                    seen.pop(pid)
                    ambiguous_pids.add(pid)
                    ambiguous_videos.update((old['video_id'], row['video_id']))
                    skipped.append({'summary': str(summary_path), 'persistent_id': pid,
                                    'reason': 'conflicting_revalidation_sources'})
                elif other_pids:
                    for other_pid in other_pids:
                        seen.pop(other_pid, None)
                    ambiguous_pids.update(other_pids | {pid})
                    ambiguous_videos.add(row['video_id'])
                    skipped.append({'summary': str(summary_path), 'persistent_id': pid,
                                    'reason': 'conflicting_revalidation_sources'})
                elif old is None:
                    seen[pid] = row

    rows = [seen[pid] for pid in sorted(seen)]
    return {
        'schema_version': 1,
        'producer': PRODUCER,
        'library_sha256': library_hash,
        'library_input': library_input,
        'prior_identity_map': {'path': str(Path(old_identity_map).resolve()),
                               'sha256': old_hash},
        'revalidated_identity_map': {'path': str(Path(current_identity_map).resolve()),
                                     'sha256': current_hash},
        'typed_map_transition': transition_receipts,
        'typed_transition_verification': verified_transition,
        'source_runs': source_runs,
        'rows': rows,
        'skipped': skipped,
        'audit': {'cached_search_responses_revalidated': len({r['input_sha256'] for r in rows}),
                  'newly_resolvable_seed_pids': len(rows),
                  'recommendation_observations_created': 0,
                  'recommendation_edges_created': 0,
                  'outgoing_roots_created': 0,
                  'network_requests': 0},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--old-identity-map', type=Path, required=True)
    parser.add_argument('--current-identity-map', type=Path, required=True)
    parser.add_argument('--transition-graph-export', type=Path, required=True)
    parser.add_argument('--transition-report', type=Path, required=True)
    parser.add_argument('--transition-state', type=Path, required=True)
    parser.add_argument('--collector-dir', type=Path, action='append', required=True,
                        help='Prior collector directory with immutable seeds.json, summaries, and requests cache')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    transition = {
        'old_identity_map': _receipt(args.old_identity_map),
        'new_identity_map': _receipt(args.current_identity_map),
        'graph_export': _receipt(args.transition_graph_export),
        'rebase_report': _receipt(args.transition_report),
        'state': _receipt(args.transition_state),
    }
    result = revalidate_cached_seed_hints(
        track_snapshot=args.track_snapshot,
        old_identity_map=args.old_identity_map,
        current_identity_map=args.current_identity_map,
        collector_dirs=args.collector_dir,
        typed_transition=transition)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps(result['audit'], ensure_ascii=False))


if __name__ == '__main__':
    main()
