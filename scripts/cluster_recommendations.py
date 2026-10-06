"""User cluster preference for review: native-core in-scope pass, then fill.

This is an application selection policy, not an Apple Genius config encoding.
Every emitted path still consists of observed edges only.
"""
import copy
from evaluate_ytmusic_batch import merge_graphs
from music_clusters import scope_graphs


def evaluate_clusters(graphs, matcher, config, executable, classifications, *, tags=(), kind=None,
                      excluded_kinds=(), mode='only', profile='without-compatible-genre',
                      artist_preference='moderate', root_pids=None, limit=25, core_factory=None):
    from evaluate_ytmusic_batch import evaluate_graphs
    filtered, scope = scope_graphs(graphs, classifications, tags, kind, excluded_kinds, mode=mode)
    options = dict(profile=profile, artist_preference=artist_preference,
                   root_pids=root_pids, limit=limit, core_factory=core_factory)
    if mode == 'only' or not (tags or kind is not None):
        evaluated = evaluate_graphs(filtered, matcher, config, executable, **options)
        return evaluated, scope
    preferred = set(scope['preferred_pids'])
    restricted = copy.deepcopy(filtered)
    for graph in restricted:
        graph['ordered_target_pids'] = [pid for pid in graph['ordered_target_pids'] if pid in preferred]
    universe = merge_graphs(filtered)
    baseline = evaluate_graphs(filtered, matcher, config, executable, id_space_roots=universe, **options)
    first = evaluate_graphs(restricted, matcher, config, executable, id_space_roots=universe, **options)
    by_root = {row['root_pid']: row for row in first['root_results']}
    for row in baseline['root_results']:
        seed = row['root_pid']
        early = by_root[seed]
        original = copy.deepcopy(row)
        items = {item['persistent_id']: item for item in [*row['playlist'], *early['playlist']]}
        order = []
        if row['playlist_pids']:
            order.append(seed)
        # Preferred tracks in the fallback also precede outside tracks, even
        # when their observed route crosses an outside intermediate node.
        for pid in [*early['playlist_pids'],
                    *(pid for pid in row['playlist_pids'] if pid in preferred), *row['playlist_pids']]:
            if pid not in order and len(order) < limit:
                order.append(pid)
        playlist = [items[pid] for pid in order]
        for item in playlist:
            item['cluster_preferred'] = item['persistent_id'] in preferred
        credits = [matcher.artist_credits(next(t for t in matcher.tracks if t['persistent_id'] == pid).get('artist') or '')
                   for pid in order]
        adjacent = sum(left == right for left, right in zip(credits, credits[1:]))
        row.update(playlist_pids=order, playlist=playlist, generated_count=len(order),
                   generated_nonroot_count=sum(pid != seed for pid in order),
                   direct_candidate_count=sum(item['observed_relation_hops'] == 1 for item in playlist),
                   indirect_candidate_count=sum(item['observed_relation_hops'] > 1 for item in playlist),
                   unique_artist_count=len(set(credits)), adjacent_same_artist_count=adjacent,
                   adjacent_same_artist_rate=adjacent / (len(credits)-1) if len(credits)>1 else None,
                   selection_policy='user_cluster_preferred_then_fill',
                   core_result=None, core_passes={'preferred': early['core_result'], 'fallback': original['core_result']},
                   unscoped_playlist_pids=original['playlist_pids'],
                   preferred_count=sum(pid in preferred for pid in order),
                   preferred_nonroot_count=sum(pid in preferred and pid != seed for pid in order),
                   fallback_nonroot_count=sum(pid not in preferred and pid != seed for pid in order))
    # The original overlaps describe the fallback, so do not present them as
    # measurements of the composed result.
    baseline['root_overlaps'] = []
    baseline['overlap_measurement'] = 'not_computed_for_cluster_composition'
    baseline['selection_policy'] = 'user_cluster_preferred_then_fill'
    baseline['total_instructions'] += first['total_instructions']
    baseline['native_selection_order_verified'] = False
    return baseline, scope
