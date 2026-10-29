"""Collect a bounded authenticated radio frontier for one reviewed work.

Outputs are private standalone metadata candidates. Nothing here mutates the
identity map, Library database, prior graph, or source-credit state.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import traceback

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))

from probe_ytmusic_batch import CachedClient, verify_account  # noqa: E402
from probe_ytmusic_session_observer_v02 import create_observed_paced_session  # noqa: E402
from work_track_video_bindings import (POLICY, ORIGIN_SUFFIX_POLICY,
                                       bind_rows, bind_video_row,
                                       bind_rows_with_origin_suffix,
                                       load_binding_inputs,
                                       load_reviewed_recipe_context,
                                       ORIGIN_SUFFIX_PLAYLIST_PATH,
                                       ordered_radio_targets_with_origin_suffix,
                                       sha256)


def atomic_json(path: Path, value: dict):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         indent=2).encode('utf-8') + b'\n'
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def candidate_row(binding: dict) -> dict:
    return {
        'videoId': binding.get('video_id'), 'title': binding.get('remote_title'),
        'artists': binding.get('remote_artists', binding.get('uploader_artists')),
        'album': binding.get('remote_album', binding.get('remote_album_field')),
        'length': binding.get('remote_length', binding.get('remote_duration')),
    }


def request_key(video_id: str, radio: bool, limit: int) -> str:
    args = {'videoId': video_id, 'radio': radio, 'limit': limit}
    encoded = json.dumps(['get_watch_playlist', args], ensure_ascii=False,
                         sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _safe_error(error):
    frames = traceback.extract_tb(error.__traceback__)
    frame = frames[-1] if frames else None
    return {'exception_type': type(error).__name__,
            'location': ({'file': Path(frame.filename).name, 'function': frame.name,
                          'line': frame.lineno} if frame else None)}


def mark_stopped(initial, session, output: Path, error, authenticated: bool):
    """Persist actual failure accounting without promoting provisional rows."""
    initial.update(status='stopped', finished_at=datetime.now(timezone.utc).isoformat(),
                   account_authentication_verified=authenticated is True,
                   http_requests=session.count if session else 0,
                   error=_safe_error(error))
    if session is not None and hasattr(session, 'active_work_pid'):
        initial['partial_persistent_id'] = session.active_work_pid
    progress_path = output / 'progress.json'
    if progress_path.exists():
        progress = json.loads(progress_path.read_text())
        progress.update(status='stopped_nonterminal_provisional',
                        http_requests=session.count if session else 0,
                        terminal_error=_safe_error(error),
                        root_credit=0, edge_credit=0, source_credit=0)
        atomic_json(progress_path, progress)
    return initial


def initial_frontier(bindings_doc, official, local_tracks, max_seeds):
    rows = bindings_doc.get('bindings')
    if not isinstance(rows, list):
        raise ValueError('candidate binding document has no bindings list')
    candidates, seen_pid, seen_vid = [], set(), set()
    for original in rows:
        binding = bind_video_row(candidate_row(original), official, local_tracks)
        if binding is None:
            raise ValueError('initial contextual binding no longer passes the reviewed recipe')
        if (binding['persistent_id'] != original.get('local_track', {}).get('persistent_id')
                or binding['video_id'] != original.get('video_id')
                or binding['disc_track'] != original.get('disc_track')):
            raise ValueError('initial candidate identity tuple differs from source packet')
        binding['initial_candidate_response_position'] = original.get('response_position')
        binding['initial_candidate_packet_only'] = True
        if binding['persistent_id'] in seen_pid or binding['video_id'] in seen_vid:
            raise ValueError('initial packet has duplicate persistent ID or video ID')
        seen_pid.add(binding['persistent_id']); seen_vid.add(binding['video_id'])
        candidates.append(binding)
    return candidates


def _register(binding, registry, disqualified_pids, disqualified_videos):
    pid, vid = binding['persistent_id'], binding['video_id']
    old_vid = registry['by_pid'].get(pid)
    old_pid = registry['by_video'].get(vid)
    if old_vid is not None and old_vid != vid:
        disqualified_pids.add(pid)
        disqualified_videos.update((old_vid, vid))
    if old_pid is not None and old_pid != pid:
        disqualified_videos.add(vid)
        disqualified_videos.add(old_vid)
        disqualified_pids.add(pid)
        disqualified_pids.add(old_pid)
    registry['by_pid'].setdefault(pid, vid)
    registry['by_video'].setdefault(vid, pid)
    registry['bindings'][(pid, vid)] = binding
    return (pid not in disqualified_pids and vid not in disqualified_videos
            and (old_vid is None or old_vid == vid)
            and (old_pid is None or old_pid == pid))


def _seed_rows(response):
    rows = response.get('tracks') if isinstance(response, dict) else None
    return rows if isinstance(rows, list) else []


def run_collection(*, bindings_doc, binding_report, official, local_tracks,
                   input_hashes, client, session, output, max_seeds=44,
                   radio_candidate_limit=100, frontier_bindings=(),
                   origin_suffix_bindings=(), recipe_context=None,
                   skip_seed_pids=frozenset()):
    if type(max_seeds) is not int or not 1 <= max_seeds <= 44:
        raise ValueError('seed cap must be an integer in 1..44')
    if type(radio_candidate_limit) is not int or not 1 <= radio_candidate_limit <= 100:
        raise ValueError('radio candidate limit must be an integer in 1..100')
    initial = initial_frontier(bindings_doc, official, local_tracks, max_seeds)
    if not isinstance(skip_seed_pids, (set, frozenset)):
        raise ValueError('skip seed PIDs must be a set from a validated input list')
    if (not isinstance(origin_suffix_bindings, (list, tuple))
            or len(origin_suffix_bindings) > 40
            or any(item.get('policy') != ORIGIN_SUFFIX_POLICY
                   for item in origin_suffix_bindings)):
        raise ValueError('origin-suffix collection inputs are outside the fixed 40-row profile')
    if origin_suffix_bindings:
        if (recipe_context is None
                or [x.get('persistent_id') for x in recipe_context.origin_suffix_bindings]
                != [x.get('persistent_id') for x in origin_suffix_bindings]
                or [x.get('video_id') for x in recipe_context.origin_suffix_bindings]
                != [x.get('video_id') for x in origin_suffix_bindings]):
            raise ValueError('origin-suffix rows require the pinned reviewed recipe context')
    registry = {'by_pid': {}, 'by_video': {}, 'bindings': {}}
    disqualified_pids, disqualified_videos = set(), set()
    for item in initial:
        _register(item, registry, disqualified_pids, disqualified_videos)
    for item in frontier_bindings:
        pair = (item['persistent_id'], item['video_id'])
        if pair in registry['bindings']:
            continue
        _register(item, registry, disqualified_pids, disqualified_videos)
    queue = [item for item in registry['bindings'].values()
             if item['persistent_id'] not in skip_seed_pids
             and item['persistent_id'] not in disqualified_pids
             and item['video_id'] not in disqualified_videos]
    client_cache = CachedClient(client, output / 'requests')
    attempted = set()
    results = []
    candidate_pair_count = len(registry['bindings'])
    while queue and len(results) < max_seeds and session.count + 2 <= 200:
        seed = queue.pop(0)
        pid, vid = seed['persistent_id'], seed['video_id']
        pair = (pid, vid)
        if pair in attempted or pid in disqualified_pids or vid in disqualified_videos:
            continue
        attempted.add(pair)
        start_count = session.count
        session.active_work_pid = pid
        radio = client_cache.get_watch_playlist(videoId=vid, radio=True,
                                                limit=radio_candidate_limit)
        radio_rows = _seed_rows(radio)
        if recipe_context is not None:
            targets, target_stats = recipe_context.ordered_radio_targets(radio_rows, pid)
        else:
            targets, target_stats = ordered_radio_targets_with_origin_suffix(
                radio_rows, pid, official, local_tracks, list(origin_suffix_bindings))
        # The non-radio watch response independently confirms the same selected
        # video and work binding; search/radio metadata alone never suffices.
        watch = client_cache.get_watch_playlist(videoId=vid, radio=False, limit=25)
        seed_return_rows = _seed_rows(watch)
        returned = [row for row in seed_return_rows if row.get('videoId') == vid]
        if recipe_context is not None:
            returned_bindings, _ = recipe_context.bind_rows(returned)
        else:
            returned_bindings, _ = bind_rows_with_origin_suffix(
                returned, official, local_tracks, list(origin_suffix_bindings))
        seed_return_ok = (len(returned_bindings) == 1
                          and returned_bindings[0]['persistent_id'] == pid
                          and returned_bindings[0]['video_id'] == vid)
        status = ('candidate_ready' if seed_return_ok and targets
                  else 'seed_not_returned_or_binding_invalid' if not seed_return_ok
                  else 'no_nonempty_bound_radio_targets')
        radio_key = request_key(vid, True, radio_candidate_limit)
        seed_key = request_key(vid, False, 25)
        radio_cache = output / 'requests' / (radio_key + '.json')
        seed_cache = output / 'requests' / (seed_key + '.json')
        result = {
            'persistent_id': pid, 'seed_video_id': vid,
            'seed_binding': seed,
            'watch_radio': {'request_key': radio_key,
                'receipt_sha256': sha256(radio_cache.read_bytes()),
                'returned_track_count': len(radio_rows),
                'ordered_target_pid_vector': [row['persistent_id'] for row in targets],
                'target_bindings': targets, 'binding_counts': target_stats},
            'watch_seed': {'request_key': seed_key,
                'receipt_sha256': sha256(seed_cache.read_bytes()),
                'returned_track_count': len(seed_return_rows),
                'same_video_returned': bool(returned),
                'same_binding_valid': seed_return_ok,
                'same_video_binding': returned_bindings[0] if seed_return_ok else None},
            'status': status,
            'recording_identity_verified': False,
            'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
            'http_request_count': session.count - start_count,
        }
        results.append(result)
        atomic_json(output / 'progress.json', {
            'schema_version': 1,
            'status': 'collecting_nonterminal_provisional',
            'producer': 'opengenius-reviewed-work-track-position-radio-candidates-v1',
            'policy': POLICY, 'inputs': input_hashes,
            'completed_seed_pairs': len(results),
            'last_completed_persistent_id': pid,
            'http_requests_so_far': session.count,
            'results_so_far': results,
            'candidate_graphs': [],
            'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
            'recording_identity_verified': False,
            'note': 'Provisional only; final global PID/video collision pass has not completed.'})
        session.active_work_pid = None

        # Only saved radio rows passing the identical primary-work recipe can
        # extend the bounded frontier. Candidate discovery confers no credit.
        if recipe_context is not None:
            new_bindings, _ = recipe_context.bind_rows(radio_rows)
        else:
            new_bindings, _ = bind_rows_with_origin_suffix(
                radio_rows, official, local_tracks, list(origin_suffix_bindings))
        for binding in new_bindings:
            pair = (binding['persistent_id'], binding['video_id'])
            if pair in registry['bindings']:
                continue
            candidate_pair_count += 1
            if _register(binding, registry, disqualified_pids, disqualified_videos):
                queue.append(binding)
            else:
                # This pair remains recorded as a collision diagnostic, never
                # queued as a seed or target.
                pass

    resolved = []
    for result in results:
        seed = result['seed_binding']
        conflict = (seed['persistent_id'] in disqualified_pids
                    or seed['video_id'] in disqualified_videos)
        if conflict:
            result['status'] = 'globally_ambiguous_video_pid_binding'
        result['globally_unambiguous'] = not conflict
        safe_targets = [target for target in result['watch_radio']['target_bindings']
                        if target['persistent_id'] not in disqualified_pids
                        and target['video_id'] not in disqualified_videos]
        result['watch_radio']['target_bindings'] = safe_targets
        result['watch_radio']['ordered_target_pid_vector'] = [
            target['persistent_id'] for target in safe_targets]
        if (result['status'] == 'candidate_ready' and not conflict
                and not safe_targets):
            result['status'] = 'no_targets_after_global_ambiguity_filter'
        if (result['status'] == 'candidate_ready' and not conflict
                and result['watch_radio']['ordered_target_pid_vector']):
            resolved.append({
                'persistent_id': result['persistent_id'],
                'seed_video_id': result['seed_video_id'],
                'seed_binding': result['seed_binding'],
                'watch_radio_request_key': result['watch_radio']['request_key'],
                'watch_radio_receipt_sha256': result['watch_radio']['receipt_sha256'],
                'watch_seed_request_key': result['watch_seed']['request_key'],
                'watch_seed_receipt_sha256': result['watch_seed']['receipt_sha256'],
                'fresh_seed_return_binding': result['watch_seed']['same_video_binding'],
                'ordered_target_pids': result['watch_radio']['ordered_target_pid_vector'],
                'targets': result['watch_radio']['target_bindings'],
                'status': 'review_candidate_only',
                'policy': seed['policy'],
                'recording_identity_verified': False,
                'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
            })
    return {
        'schema_version': 1,
        'producer': 'opengenius-reviewed-work-track-position-radio-candidates-v1',
        'policy': POLICY,
        'status': 'review_candidates_only',
        'official_work': {'label': 'KIRBY STAR ALLIES: THE ORIGINAL SOUNDTRACK',
                          'local_album': '星のカービィ スターアライズ オリジナル・サウンドトラック'},
        'inputs': input_hashes,
        'request_limits': {'minimum_interval_seconds': 3, 'stage_http_max': 200,
                           'seed_max': max_seeds,
                           'radio_candidate_limit_per_watch_radio': radio_candidate_limit,
                           'watch_seed_limit': 25,
                           'max_frontier_cache_files': 200,
                           'retries': 0, 'cache': 'request-hash JSON receipts',
                           'endpoints_per_candidate': ['watch_radio', 'watch_seed']},
        'account_authentication_verified': True,
        'results': results,
        'candidate_graphs': resolved,
        'summary': {
            'http_requests': session.count,
            'http_request_categories': dict(sorted(session.request_categories.items())),
            'request_event_count': len(session.request_events),
            'request_cache_hits': client_cache.cache_hits,
            'seeds_attempted': len(results),
            'metadata_candidate_pairs_seen': candidate_pair_count,
            'radio_candidate_limit_per_watch_radio': radio_candidate_limit,
            'skipped_seed_count': sum(item['persistent_id'] in skip_seed_pids
                                      for item in registry['bindings'].values()),
            'candidate_graphs_ready': len(resolved),
            'ambiguous_pids': sorted(disqualified_pids),
            'ambiguous_videos': sorted(disqualified_videos),
            'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
            'database_written': False,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bindings', type=Path, required=True)
    parser.add_argument('--binding-report', type=Path, required=True)
    parser.add_argument('--official-html', type=Path, required=True)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--auth', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed-limit', type=int, default=44)
    parser.add_argument('--radio-candidate-limit', type=int, default=100)
    parser.add_argument('--frontier-watch-cache', type=Path, action='append', default=[],
                        help='Optional saved radio response used only as an uncredited metadata hint')
    parser.add_argument('--frontier-playlist-cache', type=Path, action='append', default=[],
                        help='Pinned reviewed playlist metadata used only as an uncredited seed hint')
    parser.add_argument('--skip-seed-pids', type=Path,
                        help='Optional JSON list of PIDs already probed; affects seed selection only')
    parser.add_argument('--stage-http-limit', type=int, default=200)
    parser.add_argument('--interval', type=float, default=3.0)
    parser.add_argument('--execute', action='store_true',
                        help='Required to perform the bounded authenticated collection')
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error('no network is used unless --execute is explicitly supplied')
    if (args.stage_http_limit != 200 or args.interval != 3.0
            or not 1 <= args.seed_limit <= 44
            or not 1 <= args.radio_candidate_limit <= 100
            or len(args.frontier_watch_cache) > 200
            or len(args.frontier_playlist_cache) > 1):
        parser.error('limits are fixed: interval 3s, <=200 HTTP, <=44 seeds, radio window <=100')
    if (any(not path.is_file() for path in args.frontier_watch_cache + args.frontier_playlist_cache)
            or sum(path.stat().st_size for path in args.frontier_watch_cache) > 100_000_000):
        parser.error('frontier watch-cache input total exceeds 100 MB')
    output = args.output.resolve()
    if output.exists():
        parser.error('output namespace must be new and empty')
    if args.auth.resolve() in output.parents or output in args.auth.resolve().parents:
        parser.error('auth and output namespaces must be separate')
    doc, binding_report, official, tracks, input_hashes = load_binding_inputs(
        args.bindings, args.binding_report, args.official_html, args.track_snapshot)
    from work_track_video_bindings import load_frontier_watch_cache, load_skip_seed_pids
    frontier_bindings, frontier_receipts = [], []
    for cache_path in args.frontier_watch_cache:
        found, receipt = load_frontier_watch_cache(cache_path, official, tracks)
        frontier_bindings.extend(found)
        frontier_receipts.append(receipt)
    playlist_bindings, playlist_receipts = [], []
    recipe_context = None
    for cache_path in args.frontier_playlist_cache:
        if cache_path.resolve() != ORIGIN_SUFFIX_PLAYLIST_PATH.resolve():
            parser.error('the v04 capture accepts only the exact reviewed playlist cache path')
        recipe_context = load_reviewed_recipe_context(official, tracks)
        found = list(recipe_context.frontier_bindings)
        receipt = dict(recipe_context.receipt)
        playlist_bindings.extend(found)
        playlist_receipts.append(receipt)
    frontier_bindings.extend(playlist_bindings)
    skip_seed_pids, skip_receipt = load_skip_seed_pids(args.skip_seed_pids, tracks)
    input_hashes['frontier_watch_cache_hints'] = frontier_receipts
    input_hashes['frontier_playlist_cache_hints'] = playlist_receipts
    input_hashes['skip_seed_pids'] = skip_receipt
    origin_suffix_bindings = [item for item in playlist_bindings
                               if item.get('policy') == ORIGIN_SUFFIX_POLICY]
    output.mkdir(parents=True)
    (output / 'requests').mkdir()
    initial = {
        'schema_version': 1, 'status': 'running',
        'producer': 'opengenius-reviewed-work-track-position-radio-candidates-v1',
        'started_at': datetime.now(timezone.utc).isoformat(),
        'inputs': input_hashes, 'account_authentication_verified': False,
        'http_requests': 0, 'stage_http_max': 200, 'seed_max': args.seed_limit,
        'radio_candidate_limit_per_watch_radio': args.radio_candidate_limit,
        'minimum_interval_seconds': 3, 'root_credit': 0, 'edge_credit': 0,
        'source_credit': 0,
    }
    atomic_json(output / 'process.json', initial)
    session = None
    authenticated = False
    try:
        from ytmusicapi import YTMusic
        session = create_observed_paced_session(3, 200)
        client = YTMusic(str(args.auth), requests_session=session, language='en', location='JP')
        authenticated = verify_account(client)
        if authenticated is not True:
            raise RuntimeError('authenticated account preflight failed')
        result = run_collection(bindings_doc=doc, binding_report=binding_report,
                                official=official, local_tracks=tracks,
                                input_hashes=input_hashes, client=client,
                                session=session, output=output,
                                max_seeds=args.seed_limit,
                                radio_candidate_limit=args.radio_candidate_limit,
                                frontier_bindings=frontier_bindings,
                                origin_suffix_bindings=origin_suffix_bindings,
                                recipe_context=recipe_context,
                                skip_seed_pids=skip_seed_pids)
        result['started_at'] = initial['started_at']
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        result['minimum_interval_seconds'] = 3
        if result['summary']['request_cache_hits'] != 0:
            raise RuntimeError('fresh collection unexpectedly reused a cached request')
        result['process_receipts'] = {
            'request_events': session.request_events,
            'cache_hits': result['summary']['request_cache_hits'],
            'request_cache_file_count': len(list((output / 'requests').glob('*.json'))),
        }
        atomic_json(output / 'candidate-graphs.json', result)
        candidate_graph_path = output / 'candidate-graphs.json'
        atomic_json(output / 'progress.json', {
            'schema_version': 1, 'status': 'completed_finalized_candidate_only',
            'producer': result['producer'], 'policy': POLICY,
            'candidate_graphs_sha256': sha256((output / 'candidate-graphs.json').read_bytes()),
            'completed_seed_pairs': len(result['results']),
            'http_requests': session.count, 'candidate_graphs': [],
            'root_credit': 0, 'edge_credit': 0, 'source_credit': 0,
            'recording_identity_verified': False,
            'note': 'See candidate-graphs.json; progress deliberately contains no accepted graph.'})
        initial.update(status='completed', finished_at=result['finished_at'],
                       account_authentication_verified=True,
                       http_requests=session.count,
                       summary=result['summary'],
                       candidate_graphs_sha256=sha256(candidate_graph_path.read_bytes()))
    except Exception as error:
        mark_stopped(initial, session, output, error, authenticated)
    finally:
        if session is not None:
            initial['http_requests'] = session.count
            initial['http_request_categories'] = dict(sorted(session.request_categories.items()))
            initial['request_events'] = session.request_events
        cache_dir = output / 'requests'
        cache_rows = []
        if cache_dir.is_dir():
            for receipt_path in sorted(cache_dir.glob('*.json')):
                data = receipt_path.read_bytes()
                cache_rows.append({'path': str(receipt_path.relative_to(output)),
                                   'sha256': sha256(data), 'bytes': len(data)})
        initial['request_cache_manifest'] = cache_rows
        initial['request_cache_manifest_sha256'] = sha256(json.dumps(
            cache_rows, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode())
    atomic_json(output / 'process.json', initial)
    print(json.dumps({'status': initial['status'], 'output': str(output),
                      'http_requests': initial.get('http_requests'),
                      'candidate_graphs_ready': initial.get('summary', {}).get('candidate_graphs_ready', 0),
                      'root_credit': 0, 'source_credit': 0}, ensure_ascii=False))
    return 0 if initial['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
