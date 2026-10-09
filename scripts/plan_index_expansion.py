"""Plan offline YTMusic roots from a Library snapshot and a synthetic index."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from genius_format import parse_similarities
from music_identity_map import load_library, version_facets
from plan_genius_update import PRODUCER, _hash
from probe_ytmusic_batch import select_planned_seeds
from update_genius_bundle import state_rows


def _validate_relation_document(document, tracks_by_pid, state, state_ids, state_relations, library_hash):
    if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
            or document['schema_version'] != 1
            or document.get('source_library_sha256') != library_hash
            or document.get('source_state_sha256') != state.get('state_sha256')):
        raise ValueError('Relation graph is not bound to this Library and synthetic state')
    rows = document.get('relation_rows')
    if not isinstance(rows, list):
        raise ValueError('Relation graph needs relation_rows')
    graph = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid relation row')
        root, targets = row.get('root_pid'), row.get('ordered_target_pids')
        if (not isinstance(root, str) or root not in tracks_by_pid or root not in state_ids
                or root in graph or not isinstance(targets, list)):
            raise ValueError('Unknown or duplicate relation root')
        if (any(not isinstance(pid, str) or pid not in tracks_by_pid or pid not in state_ids
                for pid in targets) or len(set(targets)) != len(targets)):
            raise ValueError('Unknown or duplicate relation target')
        graph[root] = targets
    # State relation blobs and the separately supplied graph must agree exactly.
    # This validates provenance without treating a synthetic state as Apple DB evidence.
    reverse = {gid: pid for pid, gid in state_ids.items()}
    decoded = {}
    for pid, gid in state_ids.items():
        _, target_gids = parse_similarities(state_relations[gid])
        try:
            decoded[pid] = [reverse[target] for target in target_gids]
        except KeyError:
            raise ValueError('Synthetic state relation targets an unassigned ID') from None
    if any(graph.get(pid, []) != targets for pid, targets in decoded.items()
           if pid in graph or targets):
        raise ValueError('Relation rows differ from the bound synthetic state')
    return graph


def _round_robin(pids, tracks_by_pid, count, artist_loads, tie_seed):
    """Prefer the least represented exact raw artist label, breaking ties stably."""
    buckets = defaultdict(list)
    for pid in sorted(pids):
        buckets[tracks_by_pid[pid]['artist']].append(pid)
    selected, albums, selected_by_artist = [], set(), Counter()
    while buckets and len(selected) < count:
        least_load = min(artist_loads[artist] + selected_by_artist[artist]
                         for artist in buckets)
        tied_artists = [artist for artist in buckets
                        if artist_loads[artist] + selected_by_artist[artist] == least_load]
        # A stable hash spreads equal-load artists without making alphabetical labels
        # the permanent tie breaker across repeated planning runs.
        artist = min(tied_artists, key=lambda value: hashlib.sha256(
            (tie_seed + '\0' + value).encode('utf-8')).digest())
        choices = buckets[artist]
        # Prefer a new exact album label when available.
        index = next((i for i, pid in enumerate(choices)
                      if tracks_by_pid[pid]['album'] not in albums), 0)
        pid = choices.pop(index)
        selected.append(pid)
        selected_by_artist[artist] += 1
        albums.add(tracks_by_pid[pid]['album'])
        if not choices:
            del buckets[artist]
    return selected


def plan_index_expansion(tracks, library_hash, state, relations, count, excluded_pids=(),
                         unindexed_count=0):
    if type(count) is not int or not 1 <= count <= 20:
        raise ValueError('Seed count must be between 1 and 20')
    if type(unindexed_count) is not int or not 0 <= unindexed_count <= count:
        raise ValueError('Unindexed seed count must be between 0 and the requested seed count')
    if (not isinstance(state, dict) or type(state.get('schema_version')) is not int
            or state.get('schema_version') != 1 or state.get('producer') != PRODUCER
            or not isinstance(state.get('source'), dict)
            or state['source'].get('library_sha256') != library_hash
            or state.get('state_sha256') != _hash({k: v for k, v in state.items() if k != 'state_sha256'})):
        raise ValueError('Invalid synthetic state or Library hash mismatch')
    state_ids, _, state_relations = state_rows(state)
    by_pid = {track.get('persistent_id'): track for track in tracks}
    if len(by_pid) != len(tracks) or any(pid not in by_pid for pid in state_ids):
        raise ValueError('Duplicate Library PID or state PID missing from snapshot')
    if state['source'].get('tracks_sha256') != _hash([by_pid[pid] for pid in sorted(by_pid)]):
        raise ValueError('Library track metadata differs from synthetic state source')
    if not isinstance(excluded_pids, (list, tuple)):
        raise ValueError('Excluded PIDs must be a list or tuple')
    if (any(not isinstance(pid, str) or pid not in by_pid for pid in excluded_pids)
            or len(set(excluded_pids)) != len(excluded_pids)):
        raise ValueError('Excluded PIDs must be known and unique')
    excluded_pids = set(excluded_pids)
    graph = _validate_relation_document(relations, by_pid, state, state_ids,
                                        state_relations, library_hash)
    incoming = Counter(target for targets in graph.values() for target in targets)
    # Explicit empty rows represent indexed incoming targets, not observed
    # recommendation roots. Treat only nonempty outgoing rows as sampled.
    roots = {pid for pid, targets in graph.items() if targets}
    indexed = set(state_ids)
    target_only = (set(incoming) & indexed) - roots
    unindexed = set(by_pid) - indexed

    excluded = Counter()
    def eligible(pid):
        track = by_pid[pid]
        if not all(isinstance(track.get(field), str) and track[field].strip()
                   for field in ('title', 'artist', 'album')):
            excluded['missing_search_metadata'] += 1
            return False
        if type(track.get('duration_ms')) is not int or track['duration_ms'] <= 0:
            excluded['missing_search_metadata'] += 1
            return False
        if set(version_facets(track['title'])['kinds']) & {'off_vocal', 'karaoke', 'instrumental'}:
            excluded['excluded_version_marker'] += 1
            return False
        return True

    excluded_candidates = (target_only | unindexed) & excluded_pids
    if excluded_candidates:
        excluded['previously_attempted_pid'] = len(excluded_candidates)
    target_candidates = [pid for pid in target_only - excluded_pids if eligible(pid)]
    unindexed_candidates = [pid for pid in unindexed - excluded_pids if eligible(pid)]
    # Count distinct already observed or attempted PIDs by exact Library artist
    # label. This dampens repeated selection from prolific artists while keeping
    # the raw-label-only policy free of alias or genre inference.
    previously_sampled = roots | excluded_pids
    artist_loads = Counter(by_pid[pid]['artist'] for pid in previously_sampled
                           if by_pid[pid].get('artist'))
    target_quota = count - unindexed_count
    chosen_target = _round_robin(target_candidates, by_pid, target_quota,
                                  artist_loads, library_hash)
    for pid in chosen_target:
        artist_loads[by_pid[pid]['artist']] += 1
    chosen_unindexed = _round_robin(unindexed_candidates, by_pid, unindexed_count,
                                    artist_loads, library_hash)
    for pid in chosen_unindexed:
        artist_loads[by_pid[pid]['artist']] += 1
    chosen = chosen_target + chosen_unindexed
    # If the requested unindexed quota was unavailable, incoming targets fill the
    # shortfall. Remaining slots still prefer incoming targets before extra frontier
    # tracks, sharing the same artist trial counts across both phases.
    extra_target = []
    if len(chosen) < count:
        chosen_ids = set(chosen)
        extra_target = _round_robin([pid for pid in target_candidates if pid not in chosen_ids],
                                    by_pid, count - len(chosen), artist_loads, library_hash)
        chosen.extend(extra_target)
        for pid in extra_target:
            artist_loads[by_pid[pid]['artist']] += 1
    if len(chosen) < count:
        chosen_ids = set(chosen)
        chosen.extend(_round_robin([pid for pid in unindexed_candidates if pid not in chosen_ids],
                                   by_pid, count - len(chosen), artist_loads, library_hash))
    if len(chosen) != count:
        raise ValueError(f'Only {len(chosen)} eligible roots available for requested count {count}')
    reasons = {pid: 'indexed_target_only_no_outgoing_relation' for pid in chosen_target + extra_target}
    reasons.update({pid: 'unindexed_track' for pid in chosen_unindexed})
    # If incoming candidates are exhausted, extra unindexed PIDs can fill the plan.
    reasons.update({pid: 'unindexed_track' for pid in chosen if pid not in reasons})
    plan = {
        'schema_version': 1,
        'source_library_sha256': library_hash,
        'seeds': [
            {'persistent_id': pid, 'series': reasons[pid], 'sample_kind': reasons[pid]}
            for pid in chosen
        ],
    }
    select_planned_seeds(plan, tracks, library_hash, count)
    audit = {
        'schema_version': 1,
        'source_library_sha256': library_hash,
        'source_state_sha256': state['state_sha256'],
        'network_requests': 0,
        'requested_seed_count': count,
        'requested_unindexed_seed_count': unindexed_count,
        'selected_seed_count': len(chosen),
        'selected': [
            {'persistent_id': pid, 'title': by_pid[pid]['title'], 'artist': by_pid[pid]['artist'],
             'album': by_pid[pid]['album'], 'selection_reason': reasons[pid],
             'current_indexed': pid in indexed, 'current_outgoing_relation_count': len(graph.get(pid, [])),
             'current_incoming_relation_count': incoming[pid]}
            for pid in chosen
        ],
        'current_indexed_pid_count': len(indexed),
        'current_observed_root_count': len(roots),
        'current_relation_row_count': len(graph),
        'current_directed_relation_count': sum(len(targets) for targets in graph.values()),
        'current_incoming_target_pid_count': len(incoming),
        'current_indexed_target_only_count': len(target_only),
        'current_unindexed_library_track_count': len(unindexed),
        'eligible_indexed_target_only_count': len(target_candidates),
        'eligible_unindexed_track_count': len(unindexed_candidates),
        'selected_indexed_target_only_count': sum(reason == 'indexed_target_only_no_outgoing_relation'
                                                  for reason in reasons.values()),
        'selected_unindexed_track_count': sum(reason == 'unindexed_track'
                                              for reason in reasons.values()),
        'excluded_counts': dict(excluded),
        'excluded_pids': sorted(excluded_pids),
        'raw_label_diversity': 'Exact snapshot artist and album strings only; selection balances distinct previously observed/attempted PIDs per exact artist label, with a stable hash tie break; no alias or genre assumptions.',
        'evidence_limit': 'The index and relation counts describe the supplied synthetic state and graph. They do not establish Apple database contents or recording identity.',
    }
    return plan, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track-snapshot', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True, help='Sealed OpenGenius synthetic state JSON')
    parser.add_argument('--relations', type=Path, required=True, help='Library/state-bound relation graph JSON')
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--unindexed-count', type=int, default=0,
                        help='Prefer this many eligible unindexed Library tracks; incoming targets fill any shortage')
    parser.add_argument('--exclude-pid', action='append', default=[],
                        help='Known PID already attempted; may be repeated')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--audit-output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.audit_output.resolve():
        parser.error('Plan and audit output must differ')
    if any(path.exists() for path in (args.output, args.audit_output)):
        parser.error('Output exists; choose new paths')
    tracks, library_hash, source = load_library(track_snapshot=args.track_snapshot)
    state_bytes, relation_bytes = args.state.read_bytes(), args.relations.read_bytes()
    state, relations = json.loads(state_bytes), json.loads(relation_bytes)
    plan, audit = plan_index_expansion(tracks, library_hash, state, relations, args.count,
                                       excluded_pids=args.exclude_pid,
                                       unindexed_count=args.unindexed_count)
    audit['inputs'] = {
        'track_snapshot': {'path': str(args.track_snapshot),
                           'sha256': hashlib.sha256(args.track_snapshot.read_bytes()).hexdigest()},
        'synthetic_state': {'path': str(args.state), 'sha256': hashlib.sha256(state_bytes).hexdigest()},
        'relation_graph': {'path': str(args.relations), 'sha256': hashlib.sha256(relation_bytes).hexdigest()},
    }
    audit['planner_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    audit['library_input'] = source
    for path, document in ((args.output, plan), (args.audit_output, audit)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
    print(json.dumps({'selected_seed_count': len(plan['seeds']), 'network_requests': 0}))


if __name__ == '__main__':
    main()
