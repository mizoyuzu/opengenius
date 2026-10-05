"""Offline, artist-balanced PID plan for observing missing outgoing relations.

A plan requests searches through probe_ytmusic_batch; it confirms no video IDs,
recordings, recommendation quality, or relationships.
"""
import argparse
from collections import Counter, deque
import hashlib
import json
from pathlib import Path

from music_clusters import classify_tracks, KINDS
from music_identity_map import IdentityMap, load_library
from probe_ytmusic_batch import select_planned_seeds


def plan_expansion(tracks, library_hash, identity, evaluation, count=10,
                   classifications=None, excluded_kinds=(), *, playlist_target=25,
                   attempt_summaries=()):
    if type(count) is not int or not 1 <= count <= 20:
        raise ValueError('Seed count must be between 1 and 20')
    if type(playlist_target) is not int or not 1 <= playlist_target <= 100:
        raise ValueError('Playlist target must be between 1 and 100')
    if evaluation.get('library_sha256') != library_hash:
        raise ValueError('Evaluation belongs to a different Library')
    if any(kind not in KINDS for kind in excluded_kinds):
        raise ValueError('Invalid excluded kind')
    by_pid = {track['persistent_id']: track for track in tracks}
    if len(by_pid) != len(tracks):
        raise ValueError('Duplicate Library persistent IDs')
    if not isinstance(attempt_summaries, (list, tuple)):
        raise ValueError('Attempt summaries must be a list or tuple')
    held_unresolved = set()
    for summary in attempt_summaries:
        if (not isinstance(summary, dict) or type(summary.get('schema_version')) is not int
                or summary['schema_version'] != 1 or summary.get('library_sha256') != library_hash
                or not isinstance(summary.get('results'), list)):
            raise ValueError('Attempt summary schema or Library hash differs')
        for attempt in summary['results']:
            if (not isinstance(attempt, dict) or not isinstance(attempt.get('persistent_id'), str)
                    or attempt['persistent_id'] not in by_pid
                    or not isinstance(attempt.get('status'), str) or not attempt['status'].strip()):
                raise ValueError('Invalid attempt summary result')
            if attempt['status'] == 'search_unresolved':
                held_unresolved.add(attempt['persistent_id'])
    rows = evaluation.get('relation_rows')
    results = evaluation.get('root_results')
    if not isinstance(rows, list) or not rows or not isinstance(results, list):
        raise ValueError('Need evaluated observed relation rows and root results')
    graph = {}
    for row in rows:
        root, targets = row.get('root_pid'), row.get('ordered_target_pids')
        if root not in by_pid or root in graph or not isinstance(targets, list):
            raise ValueError('Invalid or duplicate graph root')
        if any(pid not in by_pid for pid in targets) or len(set(targets)) != len(targets):
            raise ValueError('Invalid or duplicate graph target')
        graph[root] = targets
    sizes = {}
    for result in results:
        root, playlist = result.get('root_pid'), result.get('playlist_pids')
        if root not in graph or root in sizes or not isinstance(playlist, list):
            raise ValueError('Invalid or duplicate evaluated root')
        if any(pid not in by_pid for pid in playlist) or len(set(playlist)) != len(playlist):
            raise ValueError('Invalid evaluated playlist')
        sizes[root] = len(playlist)
    if set(sizes) != set(graph):
        raise ValueError('Evaluation and graph roots differ')
    if classifications is None:
        if excluded_kinds:
            raise ValueError('Excluded kinds require a cluster classification')
        classifications = {pid: {'kind': 'unknown', 'tags': []} for pid in by_pid}
    if any(pid not in classifications for pid in by_pid):
        raise ValueError('Missing track classification')
    incoming = Counter(pid for targets in graph.values() for pid in targets)
    candidates = set(incoming) - set(graph)
    weak = {root for root, size in sizes.items() if size < playlist_target}
    # Reachability identifies where a new observation could help, not how many
    # songs a service will return or whether those songs belong in a playlist.
    weak_sources = {pid: set() for pid in candidates}
    for root in weak:
        pending, seen = deque([root]), set()
        while pending:
            pid = pending.popleft()
            if pid in seen:
                continue
            seen.add(pid)
            if pid in weak_sources:
                weak_sources[pid].add(root)
            pending.extend(graph.get(pid, []))
    def priority(pid):
        return (-sum(playlist_target - sizes[root] for root in weak_sources[pid]),
                -len(weak_sources[pid]), -incoming[pid], pid)
    buckets, excluded = {}, Counter()
    for pid in sorted(candidates, key=priority):
        track = by_pid[pid]
        if pid in held_unresolved:
            excluded['search_unresolved_attempt'] += 1
            continue
        if classifications[pid]['kind'] in excluded_kinds:
            excluded['excluded_kind'] += 1
            continue
        if not all(track.get(field) for field in ('title', 'artist', 'album', 'duration_ms')):
            excluded['missing_search_metadata'] += 1
            continue
        credits = tuple(sorted(identity.artist_credits(track['artist'])))
        buckets.setdefault(credits, []).append(pid)
    eligible = sum(map(len, buckets.values()))
    eligible_credit_count = len(buckets)
    selected, selected_albums = [], set()
    # Give each canonical credit set one turn before taking a second track.
    while buckets and len(selected) < count:
        order = sorted(buckets, key=lambda credits: (priority(buckets[credits][0]), credits))
        for credits in order:
            options = buckets[credits]
            index = next((i for i, pid in enumerate(options)
                          if by_pid[pid]['album'] not in selected_albums), 0)
            pid = options.pop(index)
            selected.append(pid)
            selected_albums.add(by_pid[pid]['album'])
            if not options:
                del buckets[credits]
            if len(selected) == count:
                break
    if not selected:
        raise ValueError('No eligible unobserved outgoing targets')
    plan = {'schema_version': 1, 'source_library_sha256': library_hash,
            'seeds': [{'persistent_id': pid,
                       'series': ', '.join(classifications[pid]['tags']) or '未分類',
                       'sample_kind': classifications[pid]['kind']} for pid in selected]}
    select_planned_seeds(plan, tracks, library_hash, len(selected))
    audit = {'schema_version': 1, 'source_library_sha256': library_hash,
             'network_requests': 0, 'video_ids_confirmed': 0,
             'recordings_verified': 0, 'requested_seed_count': count,
             'selected_seed_count': len(selected), 'library_track_count': len(tracks),
             'playlist_target': playlist_target,
             'attempt_summary_count': len(attempt_summaries),
             'held_search_unresolved_pid_count': len(held_unresolved),
             'held_search_unresolved_pids': sorted(held_unresolved),
             'observed_root_count': len(graph), 'directed_relation_count': sum(map(len, graph.values())),
             'related_only_count': len(candidates), 'eligible_target_count': eligible,
             'eligible_artist_credit_count': eligible_credit_count,
             'selected_artist_credit_count': len({tuple(sorted(identity.artist_credits(by_pid[pid]['artist']))) for pid in selected}),
             'selected_album_count': len(selected_albums),
             'observed_root_library_fraction': len(graph) / len(tracks),
             'related_track_library_fraction': len(set(graph) | set(incoming)) / len(tracks),
             'short_roots_reachable_to_selected_count': len(set().union(*(weak_sources[pid] for pid in selected))),
             'excluded_counts': dict(excluded), 'excluded_kinds': list(excluded_kinds),
             'playlist_size_distribution': dict(sorted(Counter(sizes.values()).items())),
             'short_playlist_roots': [{'persistent_id': root, 'playlist_size': sizes[root]}
                                      for root in sorted(weak)],
             'selected': [{'persistent_id': pid, 'title': by_pid[pid]['title'],
                           'artist': by_pid[pid]['artist'], 'album': by_pid[pid]['album'],
                           'observed_incoming_root_count': incoming[pid],
                           'reachable_short_root_deficit_sum': sum(playlist_target - sizes[root] for root in weak_sources[pid]),
                           'reachable_from_short_roots': sorted(weak_sources[pid]),
                           'kind': classifications[pid]['kind']}
                          for pid in selected],
             'scope': 'Artist-balanced expansion of the observed graph; not a representative Library sample or quality score.'}
    return plan, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--identity-map', type=Path, required=True)
    parser.add_argument('--evaluation', type=Path, required=True)
    parser.add_argument('--cluster-config', type=Path)
    parser.add_argument('--exclude-kind', choices=sorted(KINDS), action='append', default=[])
    parser.add_argument('--attempt-summary', type=Path, action='append', default=[],
                        help='Library-bound batch summary; hold search_unresolved PIDs without retrying')
    parser.add_argument('--seeds', type=int, default=10)
    parser.add_argument('--playlist-target', type=int, default=25,
                        help='Desired total playlist size for identifying short roots, including the seed')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--audit-output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.audit_output.resolve():
        parser.error('Plan and audit output must differ')
    for path in (args.output, args.audit_output):
        if path.exists():
            parser.error('Output exists; choose a new path')
    tracks, library_hash, source = load_library(track_snapshot=args.track_snapshot)
    identity = IdentityMap(json.loads(args.identity_map.read_text()), tracks, library_hash)
    classification = (classify_tracks(json.loads(args.cluster_config.read_text()), tracks, library_hash)
                      if args.cluster_config else None)
    plan, audit = plan_expansion(tracks, library_hash, identity,
                                json.loads(args.evaluation.read_text()), args.seeds,
                                classification, args.exclude_kind, playlist_target=args.playlist_target,
                                attempt_summaries=[json.loads(path.read_text()) for path in args.attempt_summary])
    audit['inputs'] = {key: {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                       for key, path in vars(args).items()
                       if key in ('track_snapshot', 'identity_map', 'evaluation', 'cluster_config') and path}
    audit['attempt_summary_inputs'] = [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                                     for path in args.attempt_summary]
    audit['planner_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    audit['library_input'] = source
    for path, document in ((args.output, plan), (args.audit_output, audit)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x') as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
    print(json.dumps({'selected_seed_count': len(plan['seeds']), 'network_requests': 0}))


if __name__ == '__main__':
    main()
