"""Empirical distance profiles for the observed Music Genius config."""
import unicodedata

from emulate_ytmusic_candidates import controlled_configs
from genius_format import pack_config, parse_config


def artist_preference_config(config, preference='neutral'):
    """Relax initial artist spacing by one or two; preserve its floor and other filters."""
    if preference not in ('neutral', 'slight', 'moderate'):
        raise ValueError('Unsupported artist preference')
    if preference == 'neutral':
        return config
    parsed = parse_config(config)
    for item in parsed['filters']:
        if item['type'] == 3 and item['parameters'][0] == 1:
            parameters = item['parameters']
            parameters[2] = max(parameters[1], parameters[2] - {'slight': 1, 'moderate': 2}[preference])
    # Relations-only has no artist filter, so this modifier has no effect there.
    return pack_config(parsed)


_AFFINITY_MOVEMENT = {'neutral': 0, 'slight': 2, 'moderate': 4}
_GENERIC_ALBUMS = frozenset({
    'va', 'various', 'variousartists', 'variousartist',
    'オムニバス', 'ヴァリアス', 'ヴァリアスアーティスト', '複数アーティスト',
})
_GENERIC_ARTISTS = frozenset({
    'va', 'various', 'variousartist', 'variousartists',
    'オムニバス', 'ヴァリアス', 'ヴァリアスアーティスト', '複数アーティスト',
})


def _album_key(value):
    if not isinstance(value, str):
        return ''
    key = ' '.join(unicodedata.normalize('NFKC', value).casefold().split())
    compact = ''.join(char for char in key if char.isalnum())
    return '' if compact in _GENERIC_ALBUMS else key


def _generic_artist(value):
    if not isinstance(value, str):
        return True
    key = ' '.join(unicodedata.normalize('NFKC', value).casefold().split())
    compact = ''.join(char for char in key if char.isalnum())
    return not compact or compact in _GENERIC_ARTISTS


def seed_affinity_order(pids, tracks, matcher, root_pid, preference='neutral', hops=None):
    """Return a bounded app-level rerank of an existing generated PID sequence.

    Artist and album matches are exact signals. Artist equality requires a
    nonempty canonical credit set; album equality uses the complete normalized
    album title and ignores generic Various Artists labels. Nonseed candidates
    can move earlier by at most two places for ``slight`` or four for ``moderate``.
    This helper never adds candidates and never changes observed graph edges.
    """
    if not isinstance(preference, str) or preference not in _AFFINITY_MOVEMENT:
        raise ValueError('Unsupported affinity preference')
    if (not isinstance(pids, (list, tuple)) or any(not isinstance(pid, str) or not pid for pid in pids)
            or len(pids) != len(set(pids))):
        raise ValueError('Affinity candidates must be unique ordered PIDs')
    if not isinstance(tracks, (list, tuple)):
        raise ValueError('Affinity track metadata must be a sequence')
    by_pid = {track.get('persistent_id'): track for track in tracks
              if isinstance(track, dict) and isinstance(track.get('persistent_id'), str)}
    if any(pid not in by_pid for pid in pids) or root_pid not in by_pid:
        raise ValueError('Affinity PID has no local track metadata')
    if hops is not None and (not isinstance(hops, dict) or any(
            pid in hops and hops[pid] is not None
            and (type(hops[pid]) is not int or hops[pid] < 0) for pid in pids)):
        raise ValueError('Invalid observed source-hop distances')

    root = by_pid[root_pid]
    root_artist = matcher.artist_credits(root.get('artist') or '')
    root_artist_valid = bool(root_artist) and not _generic_artist(root.get('artist'))
    root_album = _album_key(root.get('album'))
    scores = {}
    for pid in pids:
        track = by_pid[pid]
        artist = matcher.artist_credits(track.get('artist') or '')
        artist_match = (root_artist_valid and not _generic_artist(track.get('artist'))
                        and bool(artist) and artist == root_artist)
        album = _album_key(track.get('album'))
        album_match = bool(root_album and album and root_album == album)
        # Artist credit is the stronger signal; album is an independent hint.
        scores[pid] = (2 * int(artist_match) + int(album_match), bool(artist_match), album_match)

    ordered = list(pids)
    ranking_input = list(pids)
    movement = _AFFINITY_MOVEMENT[preference]
    if movement:
        # The seed is the list anchor. Keep it first even when an emulated
        # core returned its ID later in the row; the promotion cap applies to
        # recommendation candidates, not this required anchor.
        if root_pid in ordered:
            ordered.remove(root_pid)
            ordered.insert(0, root_pid)
        ranking_input = list(ordered)
        # Stable insertion swaps keep every item within the same absolute
        # displacement bound, including candidates that would otherwise be
        # pushed back by several promoted tracks.
        for original_index in range(len(ranking_input)):
            pid = ranking_input[original_index]
            while True:
                current = ordered.index(pid)
                if current == 0 or original_index - current >= movement:
                    break
                previous = ordered[current - 1]
                if previous == root_pid:
                    break
                previous_original = ranking_input.index(previous)
                if current - previous_original >= movement:
                    break
                if scores[pid][0] <= scores[previous][0]:
                    break
                ordered[current - 1], ordered[current] = ordered[current], ordered[current - 1]
    details = []
    for generated_index, pid in enumerate(pids):
        affinity_base_index = ranking_input.index(pid)
        new_index = ordered.index(pid)
        score, artist_match, album_match = scores[pid]
        details.append({'persistent_id': pid, 'generated_rank': generated_index + 1,
                        'affinity_base_rank': affinity_base_index + 1,
                        'selected_rank': new_index + 1, 'rank_movement': new_index - affinity_base_index,
                        'artist_credit_match': artist_match, 'album_title_match': album_match,
                        'affinity_score': score,
                        'observed_relation_hops': hops.get(pid) if hops is not None else None})
    return ordered, {'version': 1, 'preference': preference,
                     'policy': 'bounded_exact_seed_artist_credit_and_album_title',
                     'maximum_promoted_positions': movement,
                     'candidate_count': len(pids), 'changed': ordered != list(pids),
                     'candidates': sorted(details, key=lambda item: item['selected_rank'])}


def distance_profiles(config):
    baseline = parse_config(controlled_configs(config)['without-compatible-genre'])
    observed = {item['parameters'][0] for item in baseline['filters'] if item['type'] == 3}
    if not {1, 2, 3} <= observed:
        raise ValueError('Expected observed artist/album/song distance indices')
    variants = {}
    for name, lower, remove in [
        ('baseline', set(), set()),
        ('artist-minimum-one', {1}, set()),
        ('album-minimum-one', {2}, set()),
        ('artist-album-minimum-one', {1, 2}, set()),
        ('without-artist-distance', set(), {1}),
        ('without-album-distance', set(), {2}),
        ('without-artist-album-distance', set(), {1, 2}),
    ]:
        parsed = parse_config(pack_config(baseline))
        filters = []
        for item in parsed['filters']:
            if item['type'] == 3:
                index = item['parameters'][0]
                if index in remove:
                    continue
                if index in lower:
                    item['parameters'][1] = 1
            filters.append(item)
        parsed['filters'] = filters
        variants[name] = pack_config(parsed)
    variants['relations-only'] = controlled_configs(config)['relations-only']
    return variants
