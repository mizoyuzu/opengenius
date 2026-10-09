"""User cluster preference for review: native-core in-scope pass, then fill.

This is an application selection policy, not an Apple Genius config encoding.
Every emitted path still consists of observed edges only.
"""
import copy
from evaluate_ytmusic_batch import merge_graphs
from music_clusters import scope_graphs


def evaluate_clusters(graphs, matcher, config, executable, classifications, *, tags=(), kind=None,
                      excluded_kinds=(), mode='only', profile='without-compatible-genre',
                      artist_preference='moderate', affinity_preference='neutral', root_pids=None, limit=25, core_factory=None,
                      fallback_tags=(), fallback_limit=None):
    from evaluate_ytmusic_batch import evaluate_graphs
    if fallback_limit is not None and (type(fallback_limit) is not int or not 0 <= fallback_limit <= 25):
        raise ValueError('Fallback limit must be an integer from 0 to 25')
    bounded = fallback_limit is not None or bool(fallback_tags)
    if bounded and (mode != 'prefer' or not (tags or kind is not None)):
        raise ValueError('Fallback conditions require a preferred scope')
    filtered, scope = scope_graphs(graphs, classifications, tags, kind, excluded_kinds, mode=mode)
    fallback_allowed = None
    if bounded:
        _, fallback_scope = scope_graphs(graphs, classifications, fallback_tags, None, excluded_kinds, mode='prefer')
        fallback_allowed = set(fallback_scope['preferred_pids'])
        scope.update(fallback_tags=fallback_scope['tags'], fallback_limit=fallback_limit)

    options = dict(profile=profile, artist_preference=artist_preference,
                   affinity_preference=affinity_preference,
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
        outside_count = 0
        for pid in [*early['playlist_pids'],
                    *(pid for pid in row['playlist_pids'] if pid in preferred), *row['playlist_pids']]:
            if pid == seed or pid in order or len(order) >= limit:
                continue
            if pid not in preferred:
                if fallback_allowed is not None and pid not in fallback_allowed:
                    continue
                if fallback_limit is not None and outside_count >= fallback_limit:
                    continue
                outside_count += 1
            order.append(pid)
        playlist = [items[pid] for pid in order]
        post_composition_affinity = None
        if affinity_preference != 'neutral' and order:
            from genius_profiles import seed_affinity_order
            before_affinity = list(order)
            seed_in_order = seed in order
            preferred_order = [pid for pid in order if pid != seed and pid in preferred]
            fallback_order = [pid for pid in order if pid != seed and pid not in preferred]
            hops = {item['persistent_id']: item.get('observed_relation_hops') for item in playlist}
            if seed_in_order:
                preferred_order, preferred_affinity = seed_affinity_order(
                    [seed, *preferred_order], matcher.tracks, matcher, seed,
                    affinity_preference, hops)
                preferred_order = [pid for pid in preferred_order if pid != seed]
            else:
                preferred_affinity = {'version': 1, 'preference': affinity_preference,
                                      'changed': False, 'candidates': []}
            fallback_order, fallback_affinity = seed_affinity_order(
                fallback_order, matcher.tracks, matcher, seed, affinity_preference, hops)
            order = ([seed] if seed_in_order else []) + preferred_order + fallback_order
            final_affinity = {item['persistent_id']: item for item in
                              [*preferred_affinity.get('candidates', []),
                               *fallback_affinity.get('candidates', [])]}
            playlist = [items[pid] for pid in order]
            for item in playlist:
                detail = final_affinity.get(item['persistent_id'])
                if detail:
                    item['seed_artist_credit_match'] = detail['artist_credit_match']
                    item['seed_album_title_match'] = detail['album_title_match']
                    item['seed_affinity_rank_movement'] = detail['rank_movement']
            post_composition_affinity = {
                'version': 1, 'preference': affinity_preference,
                'policy': 'bounded_affinity_with_preferred_scope_before_fallback',
                'maximum_promoted_positions_within_scope': (
                    preferred_affinity.get('maximum_promoted_positions', 0),
                    fallback_affinity['maximum_promoted_positions']),
                'preferred_scope': preferred_affinity,
                'fallback_scope': fallback_affinity,
                'changed': order != before_affinity,
                'preferred_before_fallback_preserved': all(
                    pid in preferred for pid in order[1:1 + len(preferred_order)]),
            }
            if not post_composition_affinity['preferred_before_fallback_preserved']:
                raise ValueError('Affinity composition violated preferred scope priority')
        for item in playlist:
            item['cluster_preferred'] = item['persistent_id'] in preferred
        tracks_by_pid = {track['persistent_id']: track for track in matcher.tracks}
        credits = [matcher.artist_credits(tracks_by_pid[pid].get('artist') or '') for pid in order]
        adjacent = sum(left == right for left, right in zip(credits, credits[1:]))
        row.update(playlist_pids=order, playlist=playlist, generated_count=len(order),
                   generated_nonroot_count=sum(pid != seed for pid in order),
                   direct_candidate_count=sum(item['observed_relation_hops'] == 1 for item in playlist),
                   indirect_candidate_count=sum(item['observed_relation_hops'] > 1 for item in playlist),
                   unique_artist_count=len(set(credits)), adjacent_same_artist_count=adjacent,
                   adjacent_same_artist_rate=adjacent / (len(credits)-1) if len(credits)>1 else None,
                   selection_policy='user_cluster_preferred_bounded_fallback' if bounded else 'user_cluster_preferred_then_fill',
                   fallback_tags=list(fallback_tags), fallback_limit=fallback_limit,
                   core_result=None, core_passes={'preferred': early['core_result'], 'fallback': original['core_result']},
                   post_composition_affinity=post_composition_affinity,
                   unscoped_playlist_pids=original['playlist_pids'],
                   preferred_count=sum(pid in preferred for pid in order),
                   preferred_nonroot_count=sum(pid in preferred and pid != seed for pid in order),
                   fallback_nonroot_count=sum(pid not in preferred and pid != seed for pid in order))
    # The original overlaps describe the fallback, so do not present them as
    # measurements of the composed result.
    baseline['root_overlaps'] = []
    baseline['overlap_measurement'] = 'not_computed_for_cluster_composition'
    baseline['selection_policy'] = 'user_cluster_preferred_bounded_fallback' if bounded else 'user_cluster_preferred_then_fill'
    baseline['total_instructions'] += first['total_instructions']
    baseline['native_selection_order_verified'] = False
    return baseline, scope
